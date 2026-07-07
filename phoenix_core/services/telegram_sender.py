"""
phoenix_core/services/telegram_sender.py

Telegram Bot API minimal client.
v0.2: multi-user / multi-chat support.

원칙:
- 봇 토큰은 1개면 충분하다.
- A/B/C가 각각 물어보면 응답은 요청한 chat_id로만 보낸다.
- Daily alert만 TELEGRAM_DAILY_CHAT_IDS에 broadcast 가능하다.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen


TELEGRAM_LIMIT = 4096


def load_env_file(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if key and key not in os.environ:
            os.environ[key] = value


def parse_chat_ids(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    return [x.strip() for x in raw.replace(";", ",").split(",") if x.strip()]


class TelegramSender:
    def __init__(
        self,
        token: Optional[str] = None,
        chat_id: Optional[str] = None,
        *,
        env_path: str = ".env",
    ):
        load_env_file(env_path)

        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = str(chat_id or os.getenv("TELEGRAM_CHAT_ID") or "")

        # Broadcast 후보. 없으면 TELEGRAM_CHAT_ID 하나만 사용.
        self.chat_ids = (
            parse_chat_ids(os.getenv("TELEGRAM_CHAT_IDS"))
            or parse_chat_ids(os.getenv("TELEGRAM_DAILY_CHAT_IDS"))
            or ([self.chat_id] if self.chat_id else [])
        )

        if not self.token:
            raise ValueError("TELEGRAM_BOT_TOKEN이 없습니다. .env 또는 환경변수에 설정하세요.")

    def api(self, method: str, params: Optional[Dict[str, Any]] = None, *, timeout: int = 30) -> dict:
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        payload = params or {}
        data = urlencode(payload).encode("utf-8")
        req = Request(url, data=data, method="POST")
        with urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
        return json.loads(body)

    def send_message(self, text: str, *, chat_id: Optional[str] = None, parse_mode: Optional[str] = None) -> dict:
        target_chat_id = str(chat_id or self.chat_id or "")
        if not target_chat_id:
            raise ValueError("전송할 chat_id가 없습니다. chat_id 인자를 넘기거나 TELEGRAM_CHAT_ID를 설정하세요.")

        payload: Dict[str, Any] = {
            "chat_id": target_chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        return self.api("sendMessage", payload)

    def send_long_message(self, text: str, *, chat_id: Optional[str] = None) -> None:
        for chunk in split_telegram_message(text):
            self.send_message(chunk, chat_id=chat_id)

    def broadcast_message(self, text: str, *, chat_ids: Optional[Iterable[str]] = None) -> dict[str, bool]:
        targets = list(chat_ids or self.chat_ids)
        results: dict[str, bool] = {}

        if not targets:
            raise ValueError("broadcast 대상 chat_id가 없습니다. TELEGRAM_CHAT_IDS 또는 TELEGRAM_DAILY_CHAT_IDS를 설정하세요.")

        for cid in targets:
            try:
                self.send_long_message(text, chat_id=str(cid))
                results[str(cid)] = True
            except Exception:
                results[str(cid)] = False

        return results

    def send_chat_action(self, action: str = "typing", *, chat_id: Optional[str] = None) -> None:
        try:
            self.api("sendChatAction", {"chat_id": str(chat_id or self.chat_id), "action": action}, timeout=10)
        except Exception:
            pass

    def get_updates(self, *, offset: Optional[int] = None, timeout: int = 25) -> dict:
        payload: Dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            payload["offset"] = offset
        return self.api("getUpdates", payload, timeout=timeout + 10)

    def delete_webhook(self) -> dict:
        return self.api("deleteWebhook", {"drop_pending_updates": False})


def split_telegram_message(text: str, limit: int = 3900) -> list[str]:
    if not text:
        return [""]

    chunks: list[str] = []
    remaining = text

    while len(remaining) > limit:
        cut = remaining.rfind("\n", 0, limit)
        if cut < 1000:
            cut = limit
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")

    if remaining:
        chunks.append(remaining)

    return chunks
