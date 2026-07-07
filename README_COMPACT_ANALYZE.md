# Phoenix compact analyze v2.1.1 hotfix

## 목적

`/analyze TICKER`에서 기존 일봉 리포트 전체가 너무 길고, 그 뒤에 intraday context가 붙으면서 Telegram 메시지가 과하게 길어지는 문제를 줄입니다.

## 적용

프로젝트 루트에서:

```bash
cd ~/phoenix_ai_core_mvp
python apply_compact_analyze_patch.py
```

그리고 `.env`에 추가:

```env
PHOENIX_ANALYZE_COMPACT=1
```

재시작:

```bash
sudo systemctl restart phoenix-telegram-bot
journalctl -u phoenix-telegram-bot -f
```

## 되돌리기

패치 실행 시 `.bak_compact_YYYYMMDD_HHMMSS` 백업 파일이 생성됩니다.

또는 긴 원문을 다시 보고 싶으면 `.env`에서:

```env
PHOENIX_ANALYZE_COMPACT=0
```

으로 바꾸고 재시작하세요.
