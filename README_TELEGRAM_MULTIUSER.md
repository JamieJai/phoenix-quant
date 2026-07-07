# Phoenix Telegram Multi-user Bot v0.2

## 핵심 로직

봇 토큰은 1개만 사용합니다.

A/B/C가 같은 봇에게 각각 명령을 보냅니다.

```text
A -> /analyze NVDA -> A에게만 답장
B -> /analyze TSLA -> B에게만 답장
C -> /top 5        -> C에게만 답장
```

Daily 21:00 알림만 `TELEGRAM_DAILY_CHAT_IDS`에 지정된 사람들에게 broadcast 됩니다.

## 파일 위치

프로젝트 루트에 압축을 풀어 넣으세요.

```text
phoenix_core/services/telegram_sender.py
phoenix_core/services/telegram_message_formatter.py
phoenix_core/services/telegram_command_bot.py
telegram_bot_run.py
telegram_daily_2100.py
.env.multiuser.example
```

## .env 설정

```env
TELEGRAM_BOT_TOKEN=봇토큰
TELEGRAM_CHAT_ID=A의_chat_id
TELEGRAM_ALLOWED_CHAT_IDS=A의_chat_id,B의_chat_id,C의_chat_id
TELEGRAM_DAILY_CHAT_IDS=A의_chat_id,B의_chat_id,C의_chat_id
```

## chat_id 확인

A/B/C 각각 봇에게 아래를 보냅니다.

```text
/whoami
```

봇이 각자의 chat_id를 알려줍니다.

그 값을 `TELEGRAM_ALLOWED_CHAT_IDS`에 쉼표로 넣으세요.

## 실행

```powershell
python telegram_bot_run.py
```

테스트:

```text
/ping
/status
/top 5
/analyze NVDA
```

## Daily 테스트

```powershell
python telegram_daily_2100.py --once
```

## 보안

- `TELEGRAM_ALLOWED_CHAT_IDS`에 없는 사람의 명령은 무시합니다.
- 단, `/whoami`는 chat_id 확인용으로만 허용됩니다.
- `.env`는 GitHub에 올리면 안 됩니다.
