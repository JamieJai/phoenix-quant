"""
phoenix_core/services/telegram_message_formatter.py
"""

from __future__ import annotations

import re
from datetime import datetime


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def clean_cli_output(text: str) -> str:
    text = ANSI_RE.sub("", text or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]

    compact: list[str] = []
    blank = False
    for line in lines:
        if not line.strip():
            if not blank:
                compact.append("")
            blank = True
        else:
            compact.append(line)
            blank = False

    return "\n".join(compact).strip()


def compact_cli_output(text: str, *, max_chars: int = 3300) -> str:
    text = clean_cli_output(text)
    if len(text) <= max_chars:
        return text

    head = text[:900].rstrip()
    tail = text[-(max_chars - 1100):].lstrip()
    return f"{head}\n\n...\n[중간 출력 생략]\n...\n\n{tail}"


def header(title: str) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"🔥 Phoenix Quant\n{title}\n시간: {now}"


def help_message() -> str:
    return (
        "🔥 Phoenix Quant Bot\n\n"
        "명령어:\n"
        "/ping - 연결 확인\n"
        "/whoami - 내 chat_id 확인\n"
        "/top - 오늘의 Top 후보\n"
        "/top 5 - Top 5 후보\n"
        "/top 10 refresh - 데이터 새로고침 후 Top 10\n"
        "/analyze NVDA - 티커 상세 분석\n"
        "/analyze NVDA refresh - 데이터 새로고침 후 분석\n"
        "/regime - SPY 기준 시장 상태 참고 분석\n"
        "/status - 봇 설정 확인\n"
        "/help - 도움말\n\n"
        "※ 이 봇은 매매 추천/자동매매가 아니라 참고용 분석 보조 도구입니다."
    )


def disclaimer() -> str:
    return "※ 참고용 분석입니다. 매수/매도 추천이 아니며 최종 판단은 사용자가 직접 합니다."
