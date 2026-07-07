# Phoenix Quant Telegram Bot v0.1

## 설치 위치

압축을 프로젝트 루트에 풀어 넣으세요.

```text
C:\Users\EZ\Downloads\phoenix_quant_v1_2\phoenix_ai_core_mvp\
```

포함 파일:

```text
phoenix_core/services/__init__.py
phoenix_core/services/telegram_sender.py
phoenix_core/services/telegram_message_formatter.py
phoenix_core/services/telegram_command_bot.py
telegram_bot_run.py
telegram_daily_2100.py
.env.telegram.example
GITIGNORE_ADD.txt
```

## .env 설정

기존 `.env`가 있으면 `.env.telegram.example`의 내용을 복사해서 추가하세요.

```env
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
PHOENIX_PROJECT_DIR=.
PHOENIX_PYTHON=python
PHOENIX_TOP_N=10
```

`.gitignore`에는 반드시 추가:

```text
.env
*.env
```

## 봇 실행

```powershell
python telegram_bot_run.py
```

텔레그램에서 테스트:

```text
/ping
/top 5
/analyze NVDA
/regime
/status
```

## 21:00 Daily Alert 테스트

한 번만 실행:

```powershell
python telegram_daily_2100.py --once
```

상시 루프:

```powershell
python telegram_daily_2100.py
```

권장 방식은 Windows 작업 스케줄러에서 매일 21:00에 아래 명령을 실행하게 하는 것입니다.

```powershell
python telegram_daily_2100.py --once
```

## 주의

이 봇은 매매 추천/자동매매 시스템이 아닙니다.
Phoenix Quant 결과를 텔레그램으로 전달하는 참고용 분석 보조 도구입니다.
