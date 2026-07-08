from __future__ import annotations
import argparse, os, subprocess, sys, time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from phoenix_core.engines.intraday_context_engine import IntradayContextEngine
from phoenix_core.intraday_feature_store import append_intraday_feature_rows, default_intraday_feature_cache_path
from phoenix_core.services.intraday_message_formatter import extract_candidate_tickers, format_intraday_overlay
from phoenix_core.services.telegram_message_formatter import compact_cli_output, disclaimer, header
from phoenix_core.services.telegram_sender import TelegramSender, load_env_file, parse_chat_ids, send_long_message_with_token
from phoenix_core.services.telegram_command_bot import _parse_bot_profiles
KST=ZoneInfo('Asia/Seoul')

def _env_bool(k,default):
    raw=os.getenv(k)
    return default if raw is None else raw.strip().lower() in {'1','true','yes','y','on'}
def _targets():
    return parse_chat_ids(os.getenv('TELEGRAM_DAILY_CHAT_IDS')) or parse_chat_ids(os.getenv('TELEGRAM_CHAT_IDS')) or parse_chat_ids(os.getenv('TELEGRAM_ALLOWED_CHAT_IDS')) or ([os.getenv('TELEGRAM_CHAT_ID')] if os.getenv('TELEGRAM_CHAT_ID') else [])
def _send(text):
    if os.getenv('TELEGRAM_BOTS','').strip():
        for p in _parse_bot_profiles():
            for cid in p.allowed_chat_ids: send_long_message_with_token(p.token,cid,text)
    else:
        TelegramSender().broadcast_message(text,chat_ids=_targets())
def _daily_label() -> str:
    return os.getenv('PHOENIX_DAILY_LABEL', '18:00 Daily Top').strip() or '18:00 Daily Top'


def _daily_hour() -> int:
    raw = os.getenv('PHOENIX_DAILY_HOUR', '18').strip()
    try:
        hour = int(raw)
    except ValueError:
        return 18
    return max(0, min(hour, 23))


def run_daily_once(force: bool = False):
    load_env_file('.env')
    if not force and _env_bool('PHOENIX_DAILY_SEND_ONLY_AT_CONFIGURED_HOUR', True):
        now = datetime.now(KST)
        expected_hour = _daily_hour()
        if now.hour != expected_hour:
            print(f'Daily alert skipped: now={now.hour:02d}:00 KST expected={expected_hour:02d}:00 KST')
            return
    project_dir=Path(os.getenv('PHOENIX_PROJECT_DIR','.')).resolve(); py=os.getenv('PHOENIX_PYTHON',sys.executable)
    timeout=int(os.getenv('PHOENIX_DAILY_TIMEOUT',os.getenv('PHOENIX_COMMAND_TIMEOUT','600')))
    top_n=int(os.getenv('PHOENIX_DAILY_TOP_N',os.getenv('PHOENIX_TOP_N','5'))); scan_n=int(os.getenv('PHOENIX_DAILY_SCAN_N',str(top_n)))
    refresh=_env_bool('PHOENIX_DAILY_REFRESH',True); intraday=_env_bool('PHOENIX_DAILY_INTRADAY_OVERLAY',False); overlay_max=int(os.getenv('PHOENIX_INTRADAY_OVERLAY_MAX',str(top_n))); overlay_rerank=_env_bool('PHOENIX_INTRADAY_OVERLAY_RERANK',True)
    cmd=[py,'main.py','--top','--top-n',str(scan_n)]
    if refresh: cmd.append('--refresh')
    label=_daily_label()
    _send(f'{header(f"{label} {top_n} 실행 시작")}\n\n분석 중입니다...')
    env=os.environ.copy(); env['PYTHONUTF8']='1'; env['PYTHONIOENCODING']='utf-8'
    proc=subprocess.run(cmd,cwd=str(project_dir),capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=timeout,env=env,shell=False)
    out=(proc.stdout or '') + (('\n[stderr]\n'+proc.stderr) if proc.stderr else '')
    out=compact_cli_output(out,max_chars=3300)
    extra=''
    if intraday and proc.returncode==0:
        tickers=extract_candidate_tickers(out,limit=overlay_max)
        if tickers:
            eng=IntradayContextEngine(os.getenv('PHOENIX_INTRADAY_PERIOD','5d'),os.getenv('PHOENIX_INTRADAY_INTERVAL_10M','10m'),os.getenv('PHOENIX_INTRADAY_INTERVAL_30M','30m'),_env_bool('PHOENIX_INTRADAY_PREPOST',True))
            contexts=eng.analyze_many(tickers)
            if _env_bool('PHOENIX_INTRADAY_FEATURE_CACHE',True):
                try:
                    append_intraday_feature_rows(contexts,os.getenv('PHOENIX_INTRADAY_FEATURE_CACHE_PATH',default_intraday_feature_cache_path('data')))
                except Exception as e:
                    print(f'intraday feature cache warning: {e!r}')
            extra='\n\n'+format_intraday_overlay(contexts,max_items=overlay_max,rerank=overlay_rerank)
    title=f'{label} {top_n}'
    msg=f'{header(title)}\n\n⚠️ Phoenix 실행 실패 code={proc.returncode}\n\n{out}\n\n{disclaimer()}' if proc.returncode else f'{header(title)}\n\n{out}{extra}\n\n{disclaimer()}'
    _send(msg)
def run_loop():
    last=None; hour=_daily_hour(); print(f'Daily {hour:02d}:00 KST scheduler started. Ctrl+C to stop.')
    while True:
        now=datetime.now(KST); today=now.strftime('%Y-%m-%d')
        if now.hour==hour and now.minute==0 and last!=today:
            try: run_daily_once(); last=today
            except Exception as e: _send(f'⚠️ Daily job error\n\n{type(e).__name__}: {e}'); last=today
        time.sleep(20)
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--once',action='store_true'); ap.add_argument('--force',action='store_true'); args=ap.parse_args()
    run_daily_once(force=args.force) if args.once else run_loop()
if __name__=='__main__': main()
