"""
phoenix_core/services/telegram_sender.py

Telegram Bot API minimal client.
v0.3: supports single bot token + multiple users, and multiple bot tokens + mapped chat_ids.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen


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


def parse_csv(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    return [x.strip() for x in raw.replace(";", ",").split(",") if x.strip()]


def parse_chat_ids(raw: Optional[str]) -> list[str]:
    return parse_csv(raw)


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


def telegram_api(token: str, method: str, params: Optional[Dict[str, Any]] = None, *, timeout: int = 30) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    payload = params or {}
    data = urlencode(payload).encode("utf-8")
    req = Request(url, data=data, method="POST")
    with urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body)


def send_long_message_with_token(token: str, chat_id: str, text: str) -> None:
    for chunk in split_telegram_message(text):
        telegram_api(
            token,
            "sendMessage",
            {"chat_id": str(chat_id), "text": chunk, "disable_web_page_preview": True},
        )


def send_chat_action_with_token(token: str, chat_id: str, action: str = "typing") -> None:
    try:
        telegram_api(token, "sendChatAction", {"chat_id": str(chat_id), "action": action}, timeout=10)
    except Exception:
        pass


class TelegramSender:
    def __init__(self, token: Optional[str] = None, chat_id: Optional[str] = None, *, env_path: str = ".env"):
        load_env_file(env_path)
        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = str(chat_id or os.getenv("TELEGRAM_CHAT_ID") or "")
        self.chat_ids = (
            parse_chat_ids(os.getenv("TELEGRAM_CHAT_IDS"))
            or parse_chat_ids(os.getenv("TELEGRAM_DAILY_CHAT_IDS"))
            or ([self.chat_id] if self.chat_id else [])
        )
        if not self.token:
            raise ValueError("TELEGRAM_BOT_TOKEN이 없습니다. .env 또는 환경변수에 설정하세요.")

    def api(self, method: str, params: Optional[Dict[str, Any]] = None, *, timeout: int = 30) -> dict:
        return telegram_api(self.token, method, params=params, timeout=timeout)

    def send_message(self, text: str, *, chat_id: Optional[str] = None, parse_mode: Optional[str] = None) -> dict:
        target_chat_id = str(chat_id or self.chat_id or "")
        if not target_chat_id:
            raise ValueError("전송할 chat_id가 없습니다.")
        payload: Dict[str, Any] = {"chat_id": target_chat_id, "text": text, "disable_web_page_preview": True}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        return self.api("sendMessage", payload)

    def send_long_message(self, text: str, *, chat_id: Optional[str] = None) -> None:
        for chunk in split_telegram_message(text):
            self.send_message(chunk, chat_id=chat_id)

    def broadcast_message(self, text: str, *, chat_ids: Optional[Iterable[str]] = None) -> dict[str, bool]:
        targets = list(chat_ids or self.chat_ids)
        if not targets:
            raise ValueError("broadcast 대상 chat_id가 없습니다.")
        results: dict[str, bool] = {}
        for cid in targets:
            try:
                self.send_long_message(text, chat_id=str(cid))
                results[str(cid)] = True
            except Exception:
                results[str(cid)] = False
        return results
