"""
phoenix_core/services/telegram_command_bot.py

v0.3 Multi-bot / Multi-user logic.

지원 구조 1:
- 봇 토큰 1개
- TELEGRAM_ALLOWED_CHAT_IDS에 A/B/C chat_id 등록
- A/B/C 모두 같은 봇 username에게 메시지

지원 구조 2:
- 봇 토큰 여러 개
- TELEGRAM_BOTS에 token:chat_id 매핑
- A/B/C가 각자 자기 봇에게 메시지
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from phoenix_core.services.telegram_message_formatter import compact_cli_output, disclaimer, header, help_message
from phoenix_core.services.telegram_sender import (
    load_env_file,
    parse_chat_ids,
    send_chat_action_with_token,
    send_long_message_with_token,
    telegram_api,
)

TICKER_RE = re.compile(r"^[A-Za-z0-9.\-]{1,12}$")


@dataclass
class BotProfile:
    name: str
    token: str
    allowed_chat_ids: set[str]
    offset: Optional[int] = None


def _parse_bot_profiles() -> list[BotProfile]:
    """
    TELEGRAM_BOTS 형식:
      A|111111111:123456789:AAAAAA
      B|222222222:234567890:BBBBBB

    한 줄 예시:
      TELEGRAM_BOTS=A|111111111:123456789:AAAAAA,B|222222222:234567890:BBBBBB
    """
    raw = os.getenv("TELEGRAM_BOTS", "").strip()
    profiles: list[BotProfile] = []

    if raw:
        for idx, item in enumerate(raw.replace("\n", ",").replace(";", ",").split(","), start=1):
            item = item.strip()
            if not item:
                continue

            name = f"bot{idx}"
            if "|" in item:
                name_part, rest = item.split("|", 1)
                name = name_part.strip() or name
            else:
                rest = item

            if ":" not in rest:
                raise ValueError("TELEGRAM_BOTS 형식 오류. 예: A|111111111:123456:ABCDEF")

            chat_id, token = rest.split(":", 1)
            chat_id = chat_id.strip()
            token = token.strip()
            if not chat_id or not token:
                raise ValueError("TELEGRAM_BOTS 항목에 chat_id 또는 token이 비어 있습니다.")
            profiles.append(BotProfile(name=name, token=token, allowed_chat_ids={chat_id}))

    if profiles:
        return profiles

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    allowed = set(
        parse_chat_ids(os.getenv("TELEGRAM_ALLOWED_CHAT_IDS"))
        or parse_chat_ids(os.getenv("TELEGRAM_CHAT_IDS"))
        or ([os.getenv("TELEGRAM_CHAT_ID")] if os.getenv("TELEGRAM_CHAT_ID") else [])
    )
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN 또는 TELEGRAM_BOTS가 필요합니다.")
    return [BotProfile(name="default", token=token, allowed_chat_ids=allowed)]


class PhoenixTelegramBot:
    def __init__(self, *, env_path: str = ".env"):
        load_env_file(env_path)
        self.profiles = _parse_bot_profiles()
        self.allow_all = _env_bool("TELEGRAM_ALLOW_ALL", False)
        self.project_dir = Path(os.getenv("PHOENIX_PROJECT_DIR", ".")).resolve()
        self.python_exe = os.getenv("PHOENIX_PYTHON", sys.executable)
        self.timeout_sec = int(os.getenv("PHOENIX_COMMAND_TIMEOUT", "240"))
        self.default_top_n = int(os.getenv("PHOENIX_TOP_N", "10"))
        self.refresh_on_analyze = _env_bool("PHOENIX_REFRESH_ON_ANALYZE", False)
        self.refresh_on_top = _env_bool("PHOENIX_REFRESH_ON_TOP", False)

    def run_forever(self) -> None:
        print("Phoenix Telegram Bot v0.3 multi-bot started.")
        print(f"profiles: {[p.name for p in self.profiles]}")
        print(f"allow_all: {self.allow_all}")
        print(f"project_dir: {self.project_dir}")
        print("Press Ctrl+C to stop.")

        for profile in self.profiles:
            try:
                telegram_api(profile.token, "deleteWebhook", {"drop_pending_updates": False}, timeout=15)
                me = telegram_api(profile.token, "getMe", timeout=15)
                username = (me.get("result") or {}).get("username")
                print(f"[{profile.name}] bot username: @{username}, allowed_chat_ids={sorted(profile.allowed_chat_ids)}")
            except Exception as exc:
                print(f"[{profile.name}] init warning: {exc!r}")

        while True:
            try:
                for profile in self.profiles:
                    self._poll_profile(profile)
            except KeyboardInterrupt:
                print("Stopped.")
                return
            except Exception as exc:
                print("Polling loop error:", repr(exc))
                time.sleep(3)

    def _poll_profile(self, profile: BotProfile) -> None:
        try:
            payload = {"timeout": 3}
            if profile.offset is not None:
                payload["offset"] = profile.offset
            updates = telegram_api(profile.token, "getUpdates", payload, timeout=8)
            if not updates.get("ok"):
                print(f"[{profile.name}] getUpdates failed: {updates}")
                return
            for update in updates.get("result", []):
                profile.offset = int(update["update_id"]) + 1
                self._handle_update(profile, update)
        except Exception as exc:
            print(f"[{profile.name}] poll error: {exc!r}")
            time.sleep(1)

    def _handle_update(self, profile: BotProfile, update: dict) -> None:
        message = update.get("message") or update.get("edited_message")
        if not message:
            return
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        text = (message.get("text") or "").strip()
        if not chat_id or not text:
            return
        cmd = text.split()[0].split("@", 1)[0].lower()

        if cmd == "/whoami":
            send_long_message_with_token(profile.token, chat_id, f"당신의 chat_id:\n{chat_id}\n\nbot_profile: {profile.name}")
            return

        if not self._is_allowed(profile, chat_id):
            print(f"[{profile.name}] ignored unauthorized chat_id={chat_id}: {text}")
            return

        print(f"[{profile.name}][{chat_id}] {text}")
        try:
            response = self.handle_text(text, chat_id=chat_id, profile=profile)
            if response:
                send_long_message_with_token(profile.token, chat_id, response)
        except Exception as exc:
            send_long_message_with_token(profile.token, chat_id, f"⚠️ 처리 중 오류\n\n{type(exc).__name__}: {exc}")

    def _is_allowed(self, profile: BotProfile, chat_id: str) -> bool:
        return self.allow_all or chat_id in profile.allowed_chat_ids

    def handle_text(self, text: str, *, chat_id: str, profile: BotProfile) -> str:
        parts = text.split()
        cmd = parts[0].split("@", 1)[0].lower()
        args = parts[1:]
        if cmd in {"/start", "/help"}:
            return help_message()
        if cmd == "/ping":
            return "pong ✅"
        if cmd == "/status":
            return (
                "Phoenix Bot Status\n\n"
                f"bot_profile: {profile.name}\n"
                f"your_chat_id: {chat_id}\n"
                f"allowed_users_this_bot: {len(profile.allowed_chat_ids)}\n"
                f"project_dir: {self.project_dir}\n"
                f"python: {self.python_exe}\n"
                f"timeout_sec: {self.timeout_sec}\n"
                f"default_top_n: {self.default_top_n}\n"
                f"refresh_on_analyze: {self.refresh_on_analyze}\n"
                f"refresh_on_top: {self.refresh_on_top}\n"
            )
        if cmd == "/top":
            return self._cmd_top(args, chat_id=chat_id, profile=profile)
        if cmd == "/analyze":
            return self._cmd_analyze(args, chat_id=chat_id, profile=profile)
        if cmd == "/regime":
            return self._cmd_regime(chat_id=chat_id, profile=profile)
        return help_message()

    def _cmd_top(self, args: list[str], *, chat_id: str, profile: BotProfile) -> str:
        top_n = self.default_top_n
        refresh = self.refresh_on_top
        for arg in args:
            if arg.lower() == "refresh":
                refresh = True
            elif arg.isdigit():
                top_n = max(1, min(int(arg), 30))
        cmd = [self.python_exe, "main.py", "--top", "--top-n", str(top_n)]
        if refresh:
            cmd.append("--refresh")
        send_chat_action_with_token(profile.token, chat_id, "typing")
        out = self._run_phoenix(cmd)
        return f"{header(f'Top {top_n} 후보')}\n\n{out}\n\n{disclaimer()}"

    def _cmd_analyze(self, args: list[str], *, chat_id: str, profile: BotProfile) -> str:
        if not args:
            return "사용법: /analyze NVDA"
        ticker = args[0].upper().strip()
        if not TICKER_RE.match(ticker):
            return "티커 형식이 이상해. 예: /analyze NVDA"
        refresh = self.refresh_on_analyze or any(arg.lower() == "refresh" for arg in args[1:])
        cmd = [self.python_exe, "main.py", "--ticker", ticker]
        if refresh:
            cmd.append("--refresh")
        send_chat_action_with_token(profile.token, chat_id, "typing")
        out = self._run_phoenix(cmd)
        return f"{header(f'{ticker} 상세 분석')}\n\n{out}\n\n{disclaimer()}"

    def _cmd_regime(self, *, chat_id: str, profile: BotProfile) -> str:
        cmd = [self.python_exe, "main.py", "--ticker", "SPY"]
        send_chat_action_with_token(profile.token, chat_id, "typing")
        out = self._run_phoenix(cmd)
        return f"{header('시장 국면 참고 분석 - SPY')}\n\n{out}\n\n{disclaimer()}"

    def _run_phoenix(self, cmd: list[str]) -> str:
        if not self.project_dir.exists():
            raise FileNotFoundError(f"PHOENIX_PROJECT_DIR가 존재하지 않습니다: {self.project_dir}")
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.run(
            cmd,
            cwd=str(self.project_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout_sec,
            env=env,
            shell=False,
        )
        output = ""
        if proc.stdout:
            output += proc.stdout
        if proc.stderr:
            output += "\n[stderr]\n" + proc.stderr
        output = compact_cli_output(output)
        if proc.returncode != 0:
            return f"⚠️ Phoenix 실행 실패 code={proc.returncode}\n\n명령:\n{' '.join(cmd)}\n\n{output}"
        return output or "(출력 없음)"


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def main() -> None:
    PhoenixTelegramBot().run_forever()


if __name__ == "__main__":
    main()
