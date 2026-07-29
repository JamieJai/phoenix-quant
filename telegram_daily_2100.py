from __future__ import annotations
import argparse, hashlib, json, os, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from phoenix_core.engines.intraday_context_engine import IntradayContextEngine
from phoenix_core.intraday_feature_store import append_intraday_feature_rows, default_intraday_feature_cache_path
from phoenix_core.services.intraday_message_formatter import extract_candidate_tickers, format_intraday_overlay
from phoenix_core.services.telegram_message_formatter import compact_cli_output, disclaimer, header
from phoenix_core.services.telegram_sender import TelegramSender, load_env_file, parse_chat_ids, send_long_message_with_token
from phoenix_core.services.telegram_command_bot import _parse_bot_profiles
KST=ZoneInfo('Asia/Seoul')
NY=ZoneInfo('America/New_York')

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
    return os.getenv('PHOENIX_DAILY_LABEL', '21:00 Daily Top').strip() or '21:00 Daily Top'


def _daily_hour() -> int:
    raw = os.getenv('PHOENIX_DAILY_HOUR', '21').strip()
    try:
        hour = int(raw)
    except ValueError:
        return 21
    return max(0, min(hour, 23))


def _persist_paper_signal_universe(tickers: list[str]) -> Path | None:
    tickers=list(dict.fromkeys(str(t).upper().strip() for t in tickers if str(t).strip()))
    if not tickers:
        return None
    known_at=datetime.now(timezone.utc)
    market_date=known_at.astimezone(NY).date().isoformat()
    root=Path('data/research/phoenix_paper_signal_universe')
    root.mkdir(parents=True,exist_ok=True)
    path=root/f'{market_date}.json'
    payload={
        'status':'IMMUTABLE',
        'source':'PHOENIX_DAILY_TOP',
        'market_date':market_date,
        'known_at_utc':known_at.isoformat(timespec='seconds').replace('+00:00','Z'),
        'selection_rule':'frozen daily candidate order, maximum 5',
        'tickers':tickers[:5],
        'ticker_count':len(tickers[:5]),
        'production_score_changed':False,
        'broker_routes_called':False,
    }
    payload['selection_sha256']=hashlib.sha256(
        json.dumps(payload['tickers'],sort_keys=True,separators=(',',':')).encode()
    ).hexdigest()
    if path.exists():
        return path
    path.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    path.with_suffix('.sha256').write_text(
        hashlib.sha256(path.read_bytes()).hexdigest()+'  '+path.name+'\n',
        encoding='utf-8',
    )
    return path


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
    refresh=_env_bool('PHOENIX_DAILY_REFRESH',True); intraday=_env_bool('PHOENIX_DAILY_INTRADAY_OVERLAY',False); cache_intraday=_env_bool('PHOENIX_INTRADAY_FEATURE_CACHE',True); overlay_max=int(os.getenv('PHOENIX_INTRADAY_OVERLAY_MAX',str(top_n))); overlay_rerank=_env_bool('PHOENIX_INTRADAY_OVERLAY_RERANK',True)
    cmd=[py,'main.py','--top','--top-n',str(scan_n)]
    if refresh: cmd.append('--refresh')
    label=_daily_label()
    _send(f'{header(f"{label} {top_n} 실행 시작")}\n\n분석 중입니다...')
    env=os.environ.copy(); env['PYTHONUTF8']='1'; env['PYTHONIOENCODING']='utf-8'
    proc=subprocess.run(cmd,cwd=str(project_dir),capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=timeout,env=env,shell=False)
    raw_out=(proc.stdout or '') + (('\n[stderr]\n'+proc.stderr) if proc.stderr else '')
    out=compact_cli_output(raw_out,max_chars=3300)
    extra=''
    if (intraday or cache_intraday) and proc.returncode==0:
        tickers=extract_candidate_tickers(raw_out,limit=overlay_max)
        if tickers:
            try:
                _persist_paper_signal_universe(tickers)
            except Exception as e:
                print(f'paper signal universe warning: {e!r}')
            eng=IntradayContextEngine(os.getenv('PHOENIX_INTRADAY_PERIOD','5d'),os.getenv('PHOENIX_INTRADAY_INTERVAL_10M','10m'),os.getenv('PHOENIX_INTRADAY_INTERVAL_30M','30m'),_env_bool('PHOENIX_INTRADAY_PREPOST',True))
            contexts=eng.analyze_many(tickers)
            if cache_intraday:
                try:
                    append_intraday_feature_rows(contexts,os.getenv('PHOENIX_INTRADAY_FEATURE_CACHE_PATH',default_intraday_feature_cache_path('data')))
                except Exception as e:
                    print(f'intraday feature cache warning: {e!r}')
            if intraday:
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
def preflight() -> dict:
    load_env_file('.env')
    result = {
        'status': 'HEALTHY',
        'effective_daily_hour_kst': _daily_hour(),
        'send_only_at_configured_hour': _env_bool('PHOENIX_DAILY_SEND_ONLY_AT_CONFIGURED_HOUR', True),
        'intraday_overlay_display_enabled': _env_bool('PHOENIX_DAILY_INTRADAY_OVERLAY', False),
        'intraday_feature_cache_enabled': _env_bool('PHOENIX_INTRADAY_FEATURE_CACHE', True),
        'intraday_feature_cache_path': os.getenv('PHOENIX_INTRADAY_FEATURE_CACHE_PATH', default_intraday_feature_cache_path('data')),
        'telegram_send_attempted': False,
        'market_data_requested': False,
    }
    if result['effective_daily_hour_kst'] != 21 or not result['intraday_feature_cache_enabled']:
        result['status'] = 'DEGRADED'
    return result


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--once',action='store_true'); ap.add_argument('--force',action='store_true'); ap.add_argument('--preflight',action='store_true'); args=ap.parse_args()
    if args.preflight:
        import json
        result=preflight()
        print(json.dumps(result,ensure_ascii=False))
        raise SystemExit(0 if result['status']=='HEALTHY' else 2)
    run_daily_once(force=args.force) if args.once else run_loop()
if __name__=='__main__': main()
