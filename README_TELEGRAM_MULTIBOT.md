# Phoenix Telegram Multi-bot v0.3

이 패치는 두 방식을 모두 지원합니다.

## 방식 A: 단일 봇 토큰 + 여러 chat_id

A/B/C 모두 같은 봇 username에게 메시지를 보냅니다.

```env
TELEGRAM_BOT_TOKEN=공통봇토큰
TELEGRAM_ALLOWED_CHAT_IDS=A_ID,B_ID,C_ID
TELEGRAM_DAILY_CHAT_IDS=A_ID,B_ID,C_ID
```

## 방식 B: 여러 봇 토큰 + 각자의 chat_id

기존 프로젝트처럼 A/B/C가 각각 다른 봇을 쓰는 방식입니다.

```env
TELEGRAM_BOTS=A|A_chat_id:A_bot_token,B|B_chat_id:B_bot_token,C|C_chat_id:C_bot_token
```

예:

```env
TELEGRAM_BOTS=A|111111111:123456789:AAAAAA,B|222222222:234567890:BBBBBB,C|333333333:345678901:CCCCCC
```

각 사용자는 자기 봇에게만 메시지를 보내면 됩니다.

```text
A -> A봇 -> A에게만 응답
B -> B봇 -> B에게만 응답
C -> C봇 -> C에게만 응답
```

## 실행

```bash
python telegram_bot_run.py
```

## 테스트

각자 자기 봇에게:

```text
/whoami
/ping
/top 5
/analyze MRVL
```

## systemd 재시작

```bash
sudo systemctl restart phoenix-telegram-bot
journalctl -u phoenix-telegram-bot -f
```
