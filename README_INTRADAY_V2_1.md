# Phoenix Quant v2.1 Intraday Context Patch

## 변경 내용

`/analyze MRVL` 결과에 기존 일봉 Phoenix 분석뿐 아니라 현재가/전일대비/10분봉/30분봉/VWAP/거래량 비율을 추가합니다.

새 명령어:

```text
/intraday MRVL
```

`/top 5`와 매일 21:00 알림에는 기존 Top 리스트 아래에 Intraday Overlay가 붙습니다.

## 설치

프로젝트 루트에서 압축을 덮어쓰세요.

```bash
cd ~/phoenix_ai_core_mvp
unzip -o phoenix_intraday_v2_1_patch.zip
```

## .env 추가

```env
PHOENIX_INTRADAY_ENABLED=1
PHOENIX_TOP_INTRADAY_OVERLAY=1
PHOENIX_INTRADAY_OVERLAY_MAX=5
PHOENIX_INTRADAY_PERIOD=5d
PHOENIX_INTRADAY_INTERVAL_10M=10m
PHOENIX_INTRADAY_INTERVAL_30M=30m
PHOENIX_INTRADAY_PREPOST=1
```

Multi-bot 방식이면:

```env
TELEGRAM_BOTS=A|A_chat_id:A_bot_token,B|B_chat_id:B_bot_token,C|C_chat_id:C_bot_token
```

## 재시작

```bash
sudo systemctl restart phoenix-telegram-bot
journalctl -u phoenix-telegram-bot -f
```

## 테스트

```text
/intraday MRVL
/analyze MRVL
/top 5
```

## 주의

현재 intraday 데이터는 yfinance 기반입니다. 무료 데이터라서 premarket/after-hours에서 지연이나 누락이 있을 수 있습니다. 베타 테스트와 참고용으로는 가능하지만, 실시간성이 중요한 운영에는 별도 market data vendor가 필요합니다.
