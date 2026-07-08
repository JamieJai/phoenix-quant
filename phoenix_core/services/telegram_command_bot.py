from __future__ import annotations
import os, re, subprocess, sys, time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from phoenix_core.engines.intraday_context_engine import IntradayContextEngine
from phoenix_core.intraday_feature_store import append_intraday_feature_rows, default_intraday_feature_cache_path
from phoenix_core.services.intraday_message_formatter import extract_candidate_tickers, format_intraday_context, format_intraday_overlay
from phoenix_core.services.telegram_message_formatter import compact_analysis_output, compact_cli_output, compact_ranking_output, disclaimer, format_status_message, header, help_message
from phoenix_core.services.telegram_sender import load_env_file, parse_chat_ids, send_chat_action_with_token, send_long_message_with_token, telegram_api

TICKER_RE=re.compile(r'^[A-Za-z0-9\.\-]{1,12}$')
@dataclass
class BotProfile:
    name:str; token:str; allowed_chat_ids:set[str]; offset:Optional[int]=None

def _parse_bot_profiles()->list[BotProfile]:
    raw=os.getenv('TELEGRAM_BOTS','').strip(); profiles=[]
    if raw:
        for idx,item in enumerate(raw.replace('\n',',').replace(';',',').split(','),1):
            item=item.strip()
            if not item: continue
            name=f'bot{idx}'
            if '|' in item: name,rest=item.split('|',1); name=name.strip() or f'bot{idx}'
            else: rest=item
            if ':' not in rest: raise ValueError('TELEGRAM_BOTS 형식 오류. 예: A|111111111:123456:ABCDEF')
            chat_id,token=rest.split(':',1); chat_id=chat_id.strip(); token=token.strip()
            profiles.append(BotProfile(name,token,{chat_id}))
    if profiles: return profiles
    token=os.getenv('TELEGRAM_BOT_TOKEN')
    allowed=set(parse_chat_ids(os.getenv('TELEGRAM_ALLOWED_CHAT_IDS')) or parse_chat_ids(os.getenv('TELEGRAM_CHAT_IDS')) or ([os.getenv('TELEGRAM_CHAT_ID')] if os.getenv('TELEGRAM_CHAT_ID') else []))
    if not token: raise ValueError('TELEGRAM_BOT_TOKEN 또는 TELEGRAM_BOTS가 필요합니다.')
    return [BotProfile('default',token,allowed)]

class PhoenixTelegramBot:
    def __init__(self, env_path='.env'):
        load_env_file(env_path)
        self.profiles=_parse_bot_profiles(); self.allow_all=_env_bool('TELEGRAM_ALLOW_ALL',False)
        self.project_dir=Path(os.getenv('PHOENIX_PROJECT_DIR','.')).resolve(); self.python_exe=os.getenv('PHOENIX_PYTHON',sys.executable)
        self.timeout_sec=int(os.getenv('PHOENIX_COMMAND_TIMEOUT','240')); self.default_top_n=int(os.getenv('PHOENIX_TOP_N','5'))
        self.refresh_on_analyze=_env_bool('PHOENIX_REFRESH_ON_ANALYZE',False); self.refresh_on_top=_env_bool('PHOENIX_REFRESH_ON_TOP',False)
        self.intraday_enabled=_env_bool('PHOENIX_INTRADAY_ENABLED',True); self.overlay_enabled=_env_bool('PHOENIX_TOP_INTRADAY_OVERLAY',True)
        self.overlay_max=int(os.getenv('PHOENIX_INTRADAY_OVERLAY_MAX',str(self.default_top_n)))
        self.intraday_feature_cache_enabled=_env_bool('PHOENIX_INTRADAY_FEATURE_CACHE',True)
        self.intraday_feature_cache_path=os.getenv('PHOENIX_INTRADAY_FEATURE_CACHE_PATH',default_intraday_feature_cache_path('data'))
        self.intraday_overlay_rerank=_env_bool('PHOENIX_INTRADAY_OVERLAY_RERANK',True)
        self.intraday_engine=IntradayContextEngine(
            intraday_period=os.getenv('PHOENIX_INTRADAY_PERIOD','5d'), interval_10m=os.getenv('PHOENIX_INTRADAY_INTERVAL_10M','10m'),
            interval_30m=os.getenv('PHOENIX_INTRADAY_INTERVAL_30M','30m'), include_prepost=_env_bool('PHOENIX_INTRADAY_PREPOST',True))
    def run_forever(self):
        print('Phoenix Telegram Bot v0.4 intraday/multi-bot started.')
        print(f'profiles: {[p.name for p in self.profiles]}'); print(f'intraday_enabled: {self.intraday_enabled}'); print(f'project_dir: {self.project_dir}')
        for p in self.profiles:
            try:
                telegram_api(p.token,'deleteWebhook',{'drop_pending_updates':False},timeout=15)
                me=telegram_api(p.token,'getMe',timeout=15); username=(me.get('result') or {}).get('username')
                print(f'[{p.name}] bot username: @{username}, allowed_chat_ids={sorted(p.allowed_chat_ids)}')
            except Exception as e: print(f'[{p.name}] init warning: {e!r}')
        while True:
            try:
                for p in self.profiles: self._poll(p)
            except KeyboardInterrupt:
                print('Stopped.'); return
            except Exception as e:
                print('Polling loop error:',repr(e)); time.sleep(3)
    def _poll(self,p:BotProfile):
        try:
            payload={'timeout':3}
            if p.offset is not None: payload['offset']=p.offset
            updates=telegram_api(p.token,'getUpdates',payload,timeout=8)
            if not updates.get('ok'):
                print(f'[{p.name}] getUpdates failed: {updates}'); return
            for u in updates.get('result',[]):
                p.offset=int(u['update_id'])+1; self._handle(p,u)
        except Exception as e:
            print(f'[{p.name}] poll error: {e!r}'); time.sleep(1)
    def _handle(self,p:BotProfile,u:dict):
        msg=u.get('message') or u.get('edited_message')
        if not msg: return
        chat_id=str((msg.get('chat') or {}).get('id','')); text=(msg.get('text') or '').strip()
        if not chat_id or not text: return
        cmd=text.split()[0].split('@',1)[0].lower()
        if cmd=='/whoami':
            send_long_message_with_token(p.token,chat_id,f'당신의 chat_id:\n{chat_id}\n\nbot_profile: {p.name}'); return
        if not (self.allow_all or chat_id in p.allowed_chat_ids):
            print(f'[{p.name}] ignored unauthorized chat_id={chat_id}: {text}'); return
        print(f'[{p.name}][{chat_id}] {text}')
        try:
            resp=self.handle_text(text,chat_id=chat_id,profile=p)
            if resp: send_long_message_with_token(p.token,chat_id,resp)
        except Exception as e:
            send_long_message_with_token(p.token,chat_id,f'⚠️ 처리 중 오류\n\n{type(e).__name__}: {e}')
    def handle_text(self,text,chat_id,profile):
        parts=text.split(); cmd=parts[0].split('@',1)[0].lower(); args=parts[1:]
        if cmd in {'/start','/help'}: return help_message()
        if cmd=='/ping': return 'pong ✅'
        if cmd=='/status':
            return format_status_message(bot_profile=profile.name,your_chat_id=chat_id,project_dir=self.project_dir,python=self.python_exe,timeout_sec=self.timeout_sec,default_top_n=self.default_top_n,intraday_enabled=self.intraday_enabled,intraday_overlay=self.overlay_enabled,intraday_overlay_rerank=self.intraday_overlay_rerank,intraday_feature_cache=self.intraday_feature_cache_enabled)
        if cmd=='/top': return self._cmd_top(args,chat_id,profile)
        if cmd=='/analyze': return self._cmd_analyze(args,chat_id,profile)
        if cmd=='/intraday': return self._cmd_intraday(args,chat_id,profile)
        if cmd=='/regime': return self._cmd_regime(chat_id,profile)
        return help_message()
    def _cmd_top(self,args,chat_id,p):
        top_n=self.default_top_n; refresh=self.refresh_on_top
        for a in args:
            if a.lower()=='refresh': refresh=True
            elif a.isdigit(): top_n=max(1,min(int(a),30))
        cmd=[self.python_exe,'main.py','--top','--top-n',str(top_n)]
        if refresh: cmd.append('--refresh')
        send_chat_action_with_token(p.token,chat_id,'typing'); out=self._run(cmd)
        extra=''
        if self.intraday_enabled and self.overlay_enabled:
            tickers=extract_candidate_tickers(out,limit=self.overlay_max)
            if tickers:
                contexts=self.intraday_engine.analyze_many(tickers)
                self._record_intraday_contexts(contexts)
                extra='\n\n'+format_intraday_overlay(contexts,self.overlay_max,rerank=self.intraday_overlay_rerank)
        summary=compact_ranking_output(out, max_rows=top_n)
        return f'{header(f"Top {top_n} 후보")}\n\n{summary}{extra}\n\n{disclaimer()}'
    def _cmd_analyze(self,args,chat_id,p):
        if not args: return '사용법: /analyze NVDA'
        ticker=args[0].upper().strip()
        if not TICKER_RE.match(ticker): return '티커 형식이 이상해. 예: /analyze NVDA'
        refresh=self.refresh_on_analyze or any(a.lower()=='refresh' for a in args[1:])
        cmd=[self.python_exe,'main.py','--ticker',ticker]
        if refresh: cmd.append('--refresh')
        send_chat_action_with_token(p.token,chat_id,'typing'); out=self._run(cmd)
        extra=''
        if self.intraday_enabled:
            ctx=self.intraday_engine.analyze(ticker)
            self._record_intraday_contexts([ctx])
            extra='\n\n'+format_intraday_context(ctx)
        summary=compact_analysis_output(out)
        return f'{header(f"{ticker} 상세 분석")}\n\n{summary}{extra}\n\n{disclaimer()}'
    def _cmd_intraday(self,args,chat_id,p):
        if not args: return '사용법: /intraday NVDA'
        ticker=args[0].upper().strip()
        if not TICKER_RE.match(ticker): return '티커 형식이 이상해. 예: /intraday NVDA'
        send_chat_action_with_token(p.token,chat_id,'typing')
        ctx=self.intraday_engine.analyze(ticker)
        self._record_intraday_contexts([ctx])
        return f'{header(f"{ticker} Intraday")}\n\n{format_intraday_context(ctx)}\n\n{disclaimer()}'
    def _cmd_regime(self,chat_id,p):
        send_chat_action_with_token(p.token,chat_id,'typing'); out=self._run([self.python_exe,'main.py','--ticker','SPY'])
        extra=''
        if self.intraday_enabled:
            ctx=self.intraday_engine.analyze('SPY')
            self._record_intraday_contexts([ctx])
            extra='\n\n'+format_intraday_context(ctx)
        summary=compact_analysis_output(out)
        return f'{header("시장 국면 참고 분석 - SPY")}\n\n{summary}{extra}\n\n{disclaimer()}'
    def _record_intraday_contexts(self,contexts):
        if not self.intraday_feature_cache_enabled:
            return
        try:
            append_intraday_feature_rows(contexts,self.intraday_feature_cache_path)
        except Exception as e:
            print(f'intraday feature cache warning: {e!r}')
    def _run(self,cmd):
        env=os.environ.copy(); env['PYTHONUTF8']='1'; env['PYTHONIOENCODING']='utf-8'
        proc=subprocess.run(cmd,cwd=str(self.project_dir),capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=self.timeout_sec,env=env,shell=False)
        out=(proc.stdout or '') + (('\n[stderr]\n'+proc.stderr) if proc.stderr else '')
        out=compact_cli_output(out)
        return f'⚠️ Phoenix 실행 실패 code={proc.returncode}\n\n명령:\n{" ".join(cmd)}\n\n{out}' if proc.returncode else (out or '(출력 없음)')

def _env_bool(k,default):
    raw=os.getenv(k)
    return default if raw is None else raw.strip().lower() in {'1','true','yes','y','on'}
def main(): PhoenixTelegramBot().run_forever()
if __name__=='__main__': main()
