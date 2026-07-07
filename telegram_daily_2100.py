"""
telegram_daily_2100.py

v0.2:
- Daily alert는 TELEGRAM_DAILY_CHAT_IDS에 broadcast한다.
- 없으면 TELEGRAM_CHAT_IDS, 없으면 TELEGRAM_ALLOWED_CHAT_IDS, 없으면 TELEGRAM_CHAT_ID 순서로 사용한다.
- 개인 수동 명령과 달리 daily alert는 원하는 사람 모두가 받게 할 수 있다.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from phoenix_core.services.telegram_message_formatter import compact_cli_output, disclaimer, header
from phoenix_core.services.telegram_sender import TelegramSender, load_env_file, parse_chat_ids


KST = ZoneInfo("Asia/Seoul")


def _daily_targets() -> list[str]:
    return (
        parse_chat_ids(os.getenv("TELEGRAM_DAILY_CHAT_IDS"))
        or parse_chat_ids(os.getenv("TELEGRAM_CHAT_IDS"))
        or parse_chat_ids(os.getenv("TELEGRAM_ALLOWED_CHAT_IDS"))
        or ([os.getenv("TELEGRAM_CHAT_ID")] if os.getenv("TELEGRAM_CHAT_ID") else [])
    )


def run_daily_once() -> None:
    load_env_file(".env")
    sender = TelegramSender()

    targets = _daily_targets()
    if not targets:
        raise ValueError("Daily alert 대상이 없습니다. TELEGRAM_DAILY_CHAT_IDS를 설정하세요.")

    project_dir = Path(os.getenv("PHOENIX_PROJECT_DIR", ".")).resolve()
    python_exe = os.getenv("PHOENIX_PYTHON", sys.executable)
    timeout_sec = int(os.getenv("PHOENIX_DAILY_TIMEOUT", os.getenv("PHOENIX_COMMAND_TIMEOUT", "420")))
    top_n = int(os.getenv("PHOENIX_DAILY_TOP_N", os.getenv("PHOENIX_TOP_N", "10")))
    refresh = os.getenv("PHOENIX_DAILY_REFRESH", "1").strip().lower() in {"1", "true", "yes", "y", "on"}

    cmd = [python_exe, "main.py", "--top", "--top-n", str(top_n)]
    if refresh:
        cmd.append("--refresh")

    sender.broadcast_message(f"{header(f'21:00 Daily Top {top_n} 실행 시작')}\n\n분석 중입니다...", chat_ids=targets)

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    proc = subprocess.run(
        cmd,
        cwd=str(project_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_sec,
        env=env,
        shell=False,
    )

    output = ""
    if proc.stdout:
        output += proc.stdout
    if proc.stderr:
        output += "\n[stderr]\n" + proc.stderr

    output = compact_cli_output(output, max_chars=3300)

    title = f"21:00 Daily Top {top_n}"
    if proc.returncode != 0:
        msg = f"{header(title)}\n\n⚠️ Phoenix 실행 실패 code={proc.returncode}\n\n{output}\n\n{disclaimer()}"
    else:
        msg = f"{header(title)}\n\n{output}\n\n{disclaimer()}"

    sender.broadcast_message(msg, chat_ids=targets)


def run_loop() -> None:
    last_sent_date: str | None = None
    print("Daily 21:00 KST scheduler started. Ctrl+C to stop.")

    while True:
        now = datetime.now(KST)
        today = now.strftime("%Y-%m-%d")

        if now.hour == 21 and now.minute == 0 and last_sent_date != today:
            try:
                run_daily_once()
                last_sent_date = today
            except Exception as exc:
                TelegramSender().broadcast_message(f"⚠️ Daily job error\n\n{type(exc).__name__}: {exc}", chat_ids=_daily_targets())
                last_sent_date = today

        time.sleep(20)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="한 번만 실행하고 종료")
    args = parser.parse_args()

    if args.once:
        run_daily_once()
    else:
        run_loop()


if __name__ == "__main__":
    main()
