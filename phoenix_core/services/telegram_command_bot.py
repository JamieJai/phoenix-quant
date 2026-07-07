"""
phoenix_core/services/telegram_command_bot.py

v0.2 Multi-user logic:
- TELEGRAM_ALLOWED_CHAT_IDS에 있는 사용자만 Phoenix 분석 명령 사용 가능.
- A가 /analyze NVDA를 보내면 A에게만 답한다.
- B/C에게는 전송하지 않는다.
- /whoami는 허용되지 않은 사용자도 chat_id 확인용으로 응답한다.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from phoenix_core.services.telegram_message_formatter import (
    compact_cli_output,
    disclaimer,
    header,
    help_message,
)
from phoenix_core.services.telegram_sender import TelegramSender, load_env_file, parse_chat_ids


TICKER_RE = re.compile(r"^[A-Za-z0-9.\-]{1,12}$")


class PhoenixTelegramBot:
    def __init__(self, *, env_path: str = ".env"):
        load_env_file(env_path)

        self.sender = TelegramSender(env_path=env_path)

        # 허용 사용자 목록 우선순위:
        # 1. TELEGRAM_ALLOWED_CHAT_IDS
        # 2. TELEGRAM_CHAT_IDS
        # 3. TELEGRAM_CHAT_ID
        self.allowed_chat_ids = set(
            parse_chat_ids(os.getenv("TELEGRAM_ALLOWED_CHAT_IDS"))
            or parse_chat_ids(os.getenv("TELEGRAM_CHAT_IDS"))
            or ([os.getenv("TELEGRAM_CHAT_ID")] if os.getenv("TELEGRAM_CHAT_ID") else [])
        )
        self.allow_all = _env_bool("TELEGRAM_ALLOW_ALL", False)

        self.project_dir = Path(os.getenv("PHOENIX_PROJECT_DIR", ".")).resolve()
        self.python_exe = os.getenv("PHOENIX_PYTHON", sys.executable)
        self.timeout_sec = int(os.getenv("PHOENIX_COMMAND_TIMEOUT", "240"))

        self.default_top_n = int(os.getenv("PHOENIX_TOP_N", "10"))
        self.refresh_on_analyze = _env_bool("PHOENIX_REFRESH_ON_ANALYZE", False)
        self.refresh_on_top = _env_bool("PHOENIX_REFRESH_ON_TOP", False)

    def run_forever(self) -> None:
        self.sender.delete_webhook()
        print("Phoenix Telegram Bot v0.2 multi-user started.")
        print(f"Allowed chat_ids: {sorted(self.allowed_chat_ids) if self.allowed_chat_ids else '(none)'}")
        print(f"Allow all: {self.allow_all}")
        print(f"Project dir: {self.project_dir}")
        print("Press Ctrl+C to stop.")

        offset: Optional[int] = None

        while True:
            try:
                updates = self.sender.get_updates(offset=offset, timeout=25)
                if not updates.get("ok"):
                    print("getUpdates failed:", updates)
                    time.sleep(3)
                    continue

                for update in updates.get("result", []):
                    offset = int(update["update_id"]) + 1
                    self._handle_update(update)

            except KeyboardInterrupt:
                print("Stopped.")
                return
            except Exception as exc:
                print("Polling error:", repr(exc))
                time.sleep(5)

    def _handle_update(self, update: dict) -> None:
        message = update.get("message") or update.get("edited_message")
        if not message:
            return

        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        text = (message.get("text") or "").strip()
        if not chat_id or not text:
            return

        cmd = text.split()[0].split("@", 1)[0].lower()

        # /whoami는 미허용 사용자도 자기 chat_id 확인 가능.
        if cmd == "/whoami":
            self.sender.send_message(
                f"당신의 chat_id:\n{chat_id}\n\n관리자가 이 값을 TELEGRAM_ALLOWED_CHAT_IDS에 추가해야 Phoenix 명령을 사용할 수 있습니다.",
                chat_id=chat_id,
            )
            return

        if not self._is_allowed(chat_id):
            print(f"[ignored unauthorized chat_id={chat_id}] {text}")
            return

        print(f"[{chat_id}] {text}")

        try:
            response = self.handle_text(text, chat_id=chat_id)
            if response:
                # 핵심: 응답은 항상 요청한 chat_id로만 보낸다.
                self.sender.send_long_message(response, chat_id=chat_id)
        except Exception as exc:
            self.sender.send_long_message(f"⚠️ 처리 중 오류\n\n{type(exc).__name__}: {exc}", chat_id=chat_id)

    def _is_allowed(self, chat_id: str) -> bool:
        return self.allow_all or chat_id in self.allowed_chat_ids

    def handle_text(self, text: str, *, chat_id: str) -> str:
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
                f"your_chat_id: {chat_id}\n"
                f"allowed_users: {len(self.allowed_chat_ids)}\n"
                f"project_dir: {self.project_dir}\n"
                f"python: {self.python_exe}\n"
                f"timeout_sec: {self.timeout_sec}\n"
                f"default_top_n: {self.default_top_n}\n"
                f"refresh_on_analyze: {self.refresh_on_analyze}\n"
                f"refresh_on_top: {self.refresh_on_top}\n"
            )

        if cmd == "/top":
            return self._cmd_top(args, chat_id=chat_id)

        if cmd == "/analyze":
            return self._cmd_analyze(args, chat_id=chat_id)

        if cmd == "/regime":
            return self._cmd_regime(chat_id=chat_id)

        return help_message()

    def _cmd_top(self, args: list[str], *, chat_id: str) -> str:
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

        self.sender.send_chat_action("typing", chat_id=chat_id)
        out = self._run_phoenix(cmd)
        return f"{header(f'Top {top_n} 후보')}\n\n{out}\n\n{disclaimer()}"

    def _cmd_analyze(self, args: list[str], *, chat_id: str) -> str:
        if not args:
            return "사용법: /analyze NVDA"

        ticker = args[0].upper().strip()
        if not TICKER_RE.match(ticker):
            return "티커 형식이 이상해. 예: /analyze NVDA"

        refresh = self.refresh_on_analyze or any(arg.lower() == "refresh" for arg in args[1:])

        cmd = [self.python_exe, "main.py", "--ticker", ticker]
        if refresh:
            cmd.append("--refresh")

        self.sender.send_chat_action("typing", chat_id=chat_id)
        out = self._run_phoenix(cmd)
        return f"{header(f'{ticker} 상세 분석')}\n\n{out}\n\n{disclaimer()}"

    def _cmd_regime(self, *, chat_id: str) -> str:
        cmd = [self.python_exe, "main.py", "--ticker", "SPY"]
        self.sender.send_chat_action("typing", chat_id=chat_id)
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

        if not output:
            output = "(출력 없음)"

        return output


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def main() -> None:
    PhoenixTelegramBot().run_forever()


if __name__ == "__main__":
    main()
