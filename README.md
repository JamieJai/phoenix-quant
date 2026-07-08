# Phoenix Quant

> 미국 주식 단기 모멘텀 후보를 분석하고 Telegram으로 참고용 알림을 보내는 개인용 퀀트 분석 프로젝트입니다.  
> 본 프로젝트는 **자동매매 봇이 아니며**, **매수/매도 추천 서비스가 아닙니다.**

---

## 1. 프로젝트 목적

Phoenix Quant는 미국 주식 후보를 일봉 기반 통계 분석과 장중 컨텍스트로 평가하여, 사용자가 수동 매매 판단을 할 때 참고할 수 있도록 돕는 분석 보조 도구입니다.

주요 목적은 다음과 같습니다.

```text
1. 매일 한국시간 18:00에 미국 주식 관심 후보 Top 5를 Telegram으로 발송
2. Telegram 명령어로 특정 티커를 즉시 분석
3. 일봉 기반 Phoenix Score와 장중 Intraday Context를 함께 제공
4. A/B/C 여러 사용자가 같은 봇 또는 각자 다른 봇으로 사용할 수 있게 지원
```

사용 표현은 다음 기준을 따릅니다.

```text
사용 가능:
- 관심 후보
- 단기 모멘텀 후보
- 참고용 분석
- 통계적 참고 자료
- 위험 요인
- 장중 컨텍스트

사용 금지:
- 급등 확정
- 매수 추천
- 수익 보장
- 자동매매 신호
- 투자 자문
```

---

## 2. 현재 버전 요약

현재 기준 권장 구성은 다음과 같습니다.

```text
Phoenix Quant Core: v1.2 report format 유지
Benchmark / Validation: v2.0.1
Telegram Bot: v0.4
Intraday Context Layer: v2.1
Compact Analyze Hotfix: v2.1.1
```

현재 Telegram 명령어는 다음을 지원합니다.

```text
/ping
/whoami
/status
/top
/top 5
/top 10 refresh
/analyze MRVL
/analyze MRVL refresh
/intraday MRVL
/regime
/help
```

---

## 3. 핵심 기능

### 3.1 Daily Top 5 알림

매일 한국시간 18:00에 후보 종목 Top 5를 Telegram으로 전송합니다.

```text
한국시간 18:00
→ main.py --top --top-n 5 --refresh 실행
→ Top 후보 추출
→ 기본 Telegram 메시지 발송
```

systemd timer 기준:

```text
phoenix-daily-alert.timer
phoenix-daily-alert.service
```

---

### 3.2 `/analyze TICKER`

특정 티커에 대해 기존 Phoenix 일봉 분석과 장중 컨텍스트를 함께 제공합니다.

예:

```text
/analyze MRVL
```

출력 구성:

```text
1. Phoenix Daily Summary
   - 기준일
   - 기준가
   - 단타 적합도
   - 신뢰도
   - 위험도
   - Market Regime
   - Sector Rotation
   - Decision Breakdown
   - AI Summary

2. Intraday Context
   - 현재가
   - 전일 종가
   - 전일 대비
   - 당일/세션 시작가 대비
   - 5분봉 기반 단기 흐름
   - 30분봉 기반 단기 흐름
   - 거래량 비율
   - VWAP 위치
   - 당일 고점 대비 밀림
```

`PHOENIX_ANALYZE_COMPACT=1`이면 Telegram에는 핵심 요약만 출력합니다.

---

### 3.3 `/intraday TICKER`

특정 티커의 현재 장중 흐름만 빠르게 확인합니다.

예:

```text
/intraday NVDA
```

분석 항목:

```text
현재가
전일 종가
전일 대비 %
세션 시작가 대비 %
5분봉 기반 단기 흐름
30분봉 기반 단기 흐름
거래량 비율
VWAP
VWAP 위/아래
당일 고점 대비 밀림
Intraday Score
Intraday Risk Score
```

---

### 3.4 `/top 5`

기존 Top 후보 분석 결과에 Intraday Overlay를 붙입니다.

예:

```text
/top 5
```

출력 예시:

```text
Top 후보 리스트
+
📡 Intraday Overlay
1. NVDA | score 78/100 | 현재 $xxx.xx | 전일대비 +x.xx% | 5m +x.xx% | VWAP +x.xx%
2. MRVL | score 41/100 | 현재 $xxx.xx | 전일대비 -x.xx% | 5m -x.xx% | VWAP -x.xx%
...
```

---

## 4. 검증 결과 요약

v2.0.1 기준 train/test 검증 결과는 다음과 같습니다.

```text
Test Period: 2025-01-16 ~ 2026-07-06
Frequency: monthly
Top N: 10
Test Dates: 18
Total Slots: 180
Active Slots: 176
Cash Slots: 4
```

주요 OOS 결과:

```text
Best OOS Rule:
TP 6 / SL 4 / Hold 7
Portfolio Mean: 0.613%
Random Mean: 0.339%
Alpha: 0.274%
p-value: 0.140
MDD: 3.95%

Default Rule:
TP 5 / SL 3 / Hold 5
Portfolio Mean: 0.590%
Random Mean: 0.381%
Alpha: 0.208%
p-value: 0.163
MDD: 2.94%
```

해석:

```text
- OOS에서 전략이 완전히 붕괴하지는 않았음
- 다만 p-value 기준으로 통계적으로 강하게 유의하다고 보기는 어려움
- Telegram 베타 운영은 가능
- 실전 사용 시 매매 추천이 아닌 참고용 후보 필터로만 사용
```

---

## 5. 시스템 구조

```text
phoenix_ai_core_mvp/
├── main.py
├── benchmark.py
├── telegram_bot_run.py
├── telegram_daily_2100.py
├── .env
├── phoenix_core/
│   ├── engines/
│   │   ├── statistical_validation_engine.py
│   │   └── intraday_context_engine.py
│   └── services/
│       ├── telegram_sender.py
│       ├── telegram_message_formatter.py
│       ├── telegram_command_bot.py
│       └── intraday_message_formatter.py
├── reports/
└── logs/
```

---

## 6. Telegram 구조

Phoenix Quant는 두 가지 Telegram 운영 방식을 지원합니다.

---

### 6.1 단일 봇 토큰 + 여러 chat_id

A/B/C가 모두 같은 Telegram 봇에게 메시지를 보내는 구조입니다.

권장 `.env`:

```env
TELEGRAM_BOT_TOKEN=공통_봇토큰

TELEGRAM_CHAT_ID=기본_chat_id
TELEGRAM_ALLOWED_CHAT_IDS=A_chat_id,B_chat_id,C_chat_id
TELEGRAM_DAILY_CHAT_IDS=A_chat_id,B_chat_id,C_chat_id

TELEGRAM_ALLOW_ALL=0
```

이 방식에서는 A/B/C가 반드시 같은 봇 username에게 메시지를 보내야 합니다.

```text
A → @MyStockBot
B → @MyStockBot
C → @MyStockBot
```

각 사용자의 명령 응답은 요청한 chat_id로만 전송됩니다.

---

### 6.2 여러 봇 토큰 + 각자 chat_id

A/B/C가 각각 다른 Telegram 봇을 사용하는 구조입니다.

```env
TELEGRAM_BOTS=A|A_chat_id:A_bot_token,B|B_chat_id:B_bot_token,C|C_chat_id:C_bot_token
```

해석:

```text
A|111111111:123456789:AAAAAA
  └──────┘ └─────────────────┘
   chat_id      bot_token
```

주의:

```text
- 이름은 임의로 가능
- 이름에는 쉼표, 콜론, 파이프 문자 사용 금지
- bot_token 내부에도 ':'가 있으므로 반드시 chat_id:bot_token 순서로 입력
```

예:

```env
TELEGRAM_BOTS=ME|777777777:123456789:ABCDEF,U1|888888888:234567890:GHIJKL,U2|999999999:345678901:MNOPQR
```

---

## 7. Telegram 충돌 규칙

중요 규칙:

```text
같은 Telegram bot token으로 getUpdates / polling 하는 프로세스는 하나만 실행해야 합니다.
```

가능한 구조:

```text
가능:
- Phoenix가 polling 담당
- python-stock은 sendMessage만 사용

가능:
- python-stock bot token과 Phoenix bot token이 서로 다름

불가능:
- python-stock도 같은 bot token으로 polling
- Phoenix도 같은 bot token으로 polling
```

`HTTPError 409 Conflict`는 보통 다음 의미입니다.

```text
같은 bot token으로 이미 다른 프로세스가 getUpdates 중
```

확인 명령어:

```bash
ps aux | grep -Ei "telegram|bot|stock|python" | grep -v grep
ps aux | grep -E "telegram_bot_run|python-stock|bot.py|polling|getUpdates" | grep -v grep
```

정상 상태:

```text
/home/sysadmin/phoenix_ai_core_mvp/.venv/bin/python telegram_bot_run.py
```

이 프로세스가 1개만 떠 있어야 합니다.

---

## 8. `.env` 권장 설정

단일 봇 토큰 방식 기준 예시:

```env
# ===== Telegram =====
TELEGRAM_BOT_TOKEN=your_bot_token

TELEGRAM_CHAT_ID=5528903861
TELEGRAM_ALLOWED_CHAT_IDS=5528903861,8742487917,8967913607
TELEGRAM_DAILY_CHAT_IDS=5528903861,8742487917,8967913607

TELEGRAM_ALLOW_ALL=0

# ===== Phoenix Path =====
PHOENIX_PROJECT_DIR=/home/sysadmin/phoenix_ai_core_mvp
PHOENIX_PYTHON=/home/sysadmin/phoenix_ai_core_mvp/.venv/bin/python

# ===== Command Settings =====
PHOENIX_COMMAND_TIMEOUT=300
PHOENIX_TOP_N=5
PHOENIX_REFRESH_ON_ANALYZE=0
PHOENIX_REFRESH_ON_TOP=0

# ===== Daily Alert =====
PHOENIX_DAILY_TOP_N=5
PHOENIX_DAILY_SCAN_N=10
PHOENIX_DAILY_REFRESH=1
PHOENIX_DAILY_TIMEOUT=600

# ===== Intraday Layer =====
PHOENIX_INTRADAY_ENABLED=1
PHOENIX_TOP_INTRADAY_OVERLAY=1
PHOENIX_INTRADAY_OVERLAY_MAX=5
PHOENIX_INTRADAY_PERIOD=5d
PHOENIX_INTRADAY_INTERVAL_10M=5m
PHOENIX_INTRADAY_INTERVAL_30M=30m
PHOENIX_INTRADAY_PREPOST=1

# ===== Telegram Output =====
PHOENIX_ANALYZE_COMPACT=1
```

보안 권장:

```bash
chmod 600 .env
```

---

## 9. 설치 및 실행

### 9.1 venv 생성

```bash
cd ~/phoenix_ai_core_mvp

python3.12 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip setuptools wheel
pip install numpy pandas scikit-learn yfinance pyyaml python-dotenv
```

권장 버전 예시:

```bash
pip install "numpy==1.26.4" "pandas==2.2.3" "scikit-learn==1.5.2" yfinance pyyaml python-dotenv
```

---

### 9.2 기본 분석 테스트

```bash
source .venv/bin/activate

python main.py --top --top-n 5
python main.py --ticker MRVL
```

---

### 9.3 Intraday 테스트

```bash
source .venv/bin/activate

python -c "from phoenix_core.engines.intraday_context_engine import IntradayContextEngine; print(IntradayContextEngine().analyze('MRVL'))"
```

주의:

```text
yfinance는 10m interval을 지원하지 않습니다.
5m, 15m, 30m 등을 사용해야 합니다.
```

따라서 `.env`에서는 다음을 사용합니다.

```env
PHOENIX_INTRADAY_INTERVAL_10M=5m
PHOENIX_INTRADAY_INTERVAL_30M=30m
```

---

## 10. systemd 운영

Phoenix Bot은 수동으로 `python telegram_bot_run.py`를 계속 실행하지 않고, systemd service로 운영합니다.

---

### 10.1 Telegram command bot service

```bash
cd ~/phoenix_ai_core_mvp

PROJECT_DIR="$(pwd)"
USER_NAME="$(whoami)"
PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"

sudo tee /etc/systemd/system/phoenix-telegram-bot.service > /dev/null <<EOF2
[Unit]
Description=Phoenix Quant Telegram Command Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER_NAME
WorkingDirectory=$PROJECT_DIR
ExecStart=$PYTHON_BIN telegram_bot_run.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF2

sudo systemctl daemon-reload
sudo systemctl enable phoenix-telegram-bot
sudo systemctl start phoenix-telegram-bot
```

확인:

```bash
sudo systemctl status phoenix-telegram-bot --no-pager
journalctl -u phoenix-telegram-bot -f
```

---

### 10.2 Daily 18:00 Top 5 timer

서버 타임존을 한국시간으로 설정합니다.

```bash
sudo timedatectl set-timezone Asia/Seoul
timedatectl
```

Daily service/timer 생성:

```bash
cd ~/phoenix_ai_core_mvp

PROJECT_DIR="$(pwd)"
USER_NAME="$(whoami)"
PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"

sudo tee /etc/systemd/system/phoenix-daily-alert.service > /dev/null <<EOF2
[Unit]
Description=Phoenix Quant Daily 18:00 Top 5 Alert
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$USER_NAME
WorkingDirectory=$PROJECT_DIR
ExecStart=$PYTHON_BIN telegram_daily_2100.py --once
Environment=PYTHONUNBUFFERED=1
EOF2

sudo tee /etc/systemd/system/phoenix-daily-alert.timer > /dev/null <<EOF2
[Unit]
Description=Run Phoenix Quant Daily Alert every day at 18:00 KST

[Timer]
OnCalendar=*-*-* 18:00:00
Persistent=true
Unit=phoenix-daily-alert.service

[Install]
WantedBy=timers.target
EOF2

sudo systemctl daemon-reload
sudo systemctl enable phoenix-daily-alert.timer
sudo systemctl restart phoenix-daily-alert.timer
```

확인:

```bash
systemctl list-timers | grep phoenix
```

수동 테스트:

```bash
sudo systemctl start phoenix-daily-alert.service
journalctl -u phoenix-daily-alert.service -n 100 --no-pager
```

---

## 11. 운영 명령어

### 봇 상태 확인

```bash
sudo systemctl status phoenix-telegram-bot --no-pager
journalctl -u phoenix-telegram-bot -f
```

### 봇 재시작

```bash
sudo systemctl restart phoenix-telegram-bot
```

### Daily timer 확인

```bash
systemctl list-timers | grep phoenix
```

### Daily 수동 실행

```bash
sudo systemctl start phoenix-daily-alert.service
journalctl -u phoenix-daily-alert.service -n 100 --no-pager
```

### 중복 프로세스 제거

```bash
sudo systemctl stop phoenix-telegram-bot
pkill -f telegram_bot_run.py 2>/dev/null
ps aux | grep telegram_bot_run.py | grep -v grep
sudo systemctl start phoenix-telegram-bot
```

---

## 12. 수동 실행 주의

systemd가 켜져 있을 때 아래 명령을 동시에 실행하면 안 됩니다.

```bash
python telegram_bot_run.py
```

이 경우 같은 bot token으로 polling 프로세스가 2개가 되어 다음 문제가 발생할 수 있습니다.

```text
- HTTPError 409 Conflict
- 같은 명령에 응답이 2번 오는 현상
- 구버전 포맷과 신버전 포맷이 동시에 오는 현상
```

수동 테스트가 필요하면 반드시 먼저 service를 중지합니다.

```bash
sudo systemctl stop phoenix-telegram-bot
source .venv/bin/activate
python telegram_bot_run.py
```

테스트 종료 후:

```bash
Ctrl + C
sudo systemctl start phoenix-telegram-bot
```

---

## 13. python-stock 프로젝트와 함께 쓸 때

`python-stock`과 `phoenix_ai_core_mvp`가 같은 Telegram bot token과 chat_id를 사용할 수는 있습니다.

단, 조건이 있습니다.

```text
python-stock:
- sendMessage만 사용해야 함
- getUpdates / polling 금지

phoenix_ai_core_mvp:
- getUpdates / polling 담당
- /analyze, /top, /intraday 명령 처리
```

괜찮은 구조:

```text
python-stock → 정해진 시간에 sendMessage
Phoenix → Telegram command bot polling
```

문제 되는 구조:

```text
python-stock → polling
Phoenix → polling
같은 bot token
```

충돌 확인:

```bash
grep -R -nE "getUpdates|run_polling|start_polling|infinity_polling|polling\(|Updater|ApplicationBuilder|telebot|aiogram" ~/python-stock 2>/dev/null
```

---

## 14. 데이터 소스와 한계

현재 intraday 데이터는 `yfinance`를 사용합니다.

주의 사항:

```text
- 무료 데이터는 지연될 수 있음
- 프리마켓/애프터마켓 데이터가 누락될 수 있음
- 10m interval은 yfinance에서 지원하지 않음
- 실시간 체결 데이터 수준은 아님
```

실사용 품질을 높이려면 향후 다음 데이터 소스를 검토할 수 있습니다.

```text
- Polygon
- Alpaca
- IEX Cloud
- Nasdaq Data Link
```

현재 버전은 베타 테스트 및 수동 판단 참고용입니다.

---

## 15. 장애 대응

### 15.1 `HTTPError 409 Conflict`

원인:

```text
같은 bot token으로 polling 프로세스가 2개 이상 실행 중
```

조치:

```bash
sudo systemctl stop phoenix-telegram-bot
pkill -f telegram_bot_run.py 2>/dev/null
ps aux | grep telegram_bot_run.py | grep -v grep
sudo systemctl start phoenix-telegram-bot
```

---

### 15.2 `/analyze` 결과가 기준일/기준가에 고정된 것처럼 보임

기존 Phoenix Daily 분석은 일봉 기반입니다.

```text
기준일: 마지막 일봉 날짜
기준가: 마지막 일봉 기준가
```

v2.1부터는 하단에 Intraday Context를 추가하여 현재가와 장중 흐름을 보완합니다.

```text
Daily Phoenix Score:
좋은 후보인가?

Intraday Context:
지금도 살아있는 흐름인가?
```

---

### 15.3 `interval=10m is not supported`

원인:

```text
yfinance는 10m interval을 지원하지 않음
```

조치:

```bash
sed -i 's/^PHOENIX_INTRADAY_INTERVAL_10M=.*/PHOENIX_INTRADAY_INTERVAL_10M=5m/' .env
sudo systemctl restart phoenix-telegram-bot
```

---

### 15.4 NumPy CPU 오류

증상:

```text
RuntimeError: NumPy was built with baseline optimizations (X86_V2)
but your machine doesn't support (X86_V2)
```

가능 원인:

```text
- VM CPU type이 qemu64/kvm64로 잡혀 CPU flag가 숨겨짐
- Python 3.14 등 비권장 환경 사용
```

권장 조치:

```bash
sudo systemctl stop phoenix-telegram-bot
cd ~/phoenix_ai_core_mvp
rm -rf .venv

sudo apt update
sudo apt install -y python3.12 python3.12-venv python3-pip

python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
pip install "numpy==1.26.4" "pandas==2.2.3" "scikit-learn==1.5.2" yfinance pyyaml python-dotenv
```

Proxmox VM이면 CPU type을 `host`로 변경합니다.

```bash
qm set VMID --cpu host
qm shutdown VMID
qm start VMID
```

---

## 16. 보안 주의사항

`.env`에는 Telegram bot token이 들어갑니다.

```text
절대 GitHub에 올리지 말 것
절대 채팅에 그대로 공유하지 말 것
권한은 chmod 600 권장
```

`.gitignore` 예시:

```gitignore
.env
.env.*
!.env.example
__pycache__/
*.pyc
.venv/
venv/
logs/
reports/*.tmp
```

---

## 17. 운영 체크리스트

배포 후 확인:

```bash
cd ~/phoenix_ai_core_mvp

source .venv/bin/activate
python main.py --top --top-n 5
python -c "from phoenix_core.engines.intraday_context_engine import IntradayContextEngine; print(IntradayContextEngine().analyze('MRVL'))"

sudo systemctl restart phoenix-telegram-bot
sudo systemctl status phoenix-telegram-bot --no-pager

systemctl list-timers | grep phoenix
ps aux | grep telegram_bot_run.py | grep -v grep
```

Telegram에서 확인:

```text
/whoami
/ping
/status
/intraday MRVL
/analyze MRVL
/top 5
```

정상 기준:

```text
- telegram_bot_run.py 프로세스는 1개만 실행
- /ping 응답 정상
- /intraday 응답 정상
- /analyze는 Daily Summary + Intraday Context 형태
- 21:00 KST Daily Top 5 timer 등록
```

---

## 18. 면책 고지

Phoenix Quant는 과거 데이터, 통계적 유사도, 시장 컨텍스트, 장중 가격 흐름을 기반으로 참고용 분석을 제공합니다.

```text
본 프로젝트는 투자 자문이 아닙니다.
본 프로젝트는 자동매매 시스템이 아닙니다.
본 프로젝트의 출력은 매수/매도 추천이 아닙니다.
모든 투자 판단과 책임은 사용자 본인에게 있습니다.
```

Telegram 메시지에도 다음 문구를 포함합니다.

```text
※ 참고용 분석입니다. 매수/매도 추천이 아니며 최종 판단은 사용자가 직접 합니다.
```

---

## 19. 현재 권장 운영 구조

현재 가장 안정적인 구조는 다음과 같습니다.

```text
1. Phoenix 전용 systemd service가 Telegram command polling 담당
2. python-stock은 sendMessage만 사용
3. Daily 21:00 Top 5는 phoenix-daily-alert.timer가 담당
4. Telegram bot process는 절대 중복 실행하지 않음
5. /analyze는 compact summary + intraday context로 출력
```

운영 상태 확인 명령:

```bash
sudo systemctl status phoenix-telegram-bot --no-pager
systemctl list-timers | grep phoenix
ps aux | grep telegram_bot_run.py | grep -v grep
```

정상 상태는 다음과 같습니다.

```text
phoenix-telegram-bot.service: active running
phoenix-daily-alert.timer: active waiting
telegram_bot_run.py: 1 process only
```
