# Phoenix Telegram Test Patch

넣을 위치:

```text
phoenix_core/services/telegram_sender.py
telegram_test_send.py
.env.example
```

실행:

```powershell
copy .env.example .env
notepad .env
python telegram_test_send.py
```

주의:
- TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID는 코드에 직접 쓰지 마세요.
- .env는 GitHub에 올리면 안 됩니다.
- `.gitignore`에 `.env`를 추가하세요.
