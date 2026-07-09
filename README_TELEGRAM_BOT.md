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
/top 5            # 일봉 기반 후보 + 별도 intraday overlay
/toplive 10       # 실험: 장중 adjusted_score 재정렬
/top live 10      # /toplive와 동일
/hot 10           # 장중 강세 조건 충족 후보
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


## 라벨 의미

```text
관심: 우선 관찰 후보
관찰: 일부 조건 양호, 추가 확인 필요
보류: 조건 부족, 매매 후보로 해석 금지
제외: 제외 대상
```

`/toplive`는 OOS 검증 전 실험 기능이며 `/top` 기본 동작을 대체하지 않습니다.
