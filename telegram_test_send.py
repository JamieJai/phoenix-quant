
"""
telegram_test_send.py

Phoenix Quant Telegram 연결 테스트.
프로젝트 루트에서 실행:

python telegram_test_send.py

.env 예시:

TELEGRAM_BOT_TOKEN=123456789:AA...
TELEGRAM_CHAT_ID=123456789
"""

from __future__ import annotations

from datetime import datetime

from phoenix_core.services.telegram_sender import TelegramSender


def main() -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    msg = (
        "🔥 Phoenix Quant Telegram Test\\n\\n"
        f"시간: {now}\\n"
        "상태: 연결 성공\\n\\n"
        "다음 단계:\\n"
        "/top - 오늘의 후보\\n"
        "/analyze NVDA - 티커 상세 분석\\n"
        "/regime - 시장 국면\\n\\n"
        "※ 이 메시지는 매매 추천이 아니라 시스템 연결 테스트입니다."
    )

    sender = TelegramSender()
    result = sender.send_message(msg)
    print("Telegram send result:", result.get("ok"))


if __name__ == "__main__":
    main()
