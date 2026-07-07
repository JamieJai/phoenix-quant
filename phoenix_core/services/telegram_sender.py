from __future__ import annotations
import json, os
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

def load_env_file(path='.env'):
    p=Path(path)
    if not p.exists(): return
    for raw in p.read_text(encoding='utf-8').splitlines():
        line=raw.strip()
        if not line or line.startswith('#') or '=' not in line: continue
        k,v=line.split('=',1); k=k.strip(); v=v.strip().strip('"').strip("'")
        if k and k not in os.environ: os.environ[k]=v

def parse_csv(raw:Optional[str])->list[str]:
    if not raw: return []
    return [x.strip() for x in raw.replace(';',',').split(',') if x.strip()]
def parse_chat_ids(raw:Optional[str])->list[str]: return parse_csv(raw)

def split_telegram_message(text:str, limit:int=3900)->list[str]:
    if not text: return ['']
    chunks=[]; rem=text
    while len(rem)>limit:
        cut=rem.rfind('\n',0,limit)
        if cut<1000: cut=limit
        chunks.append(rem[:cut]); rem=rem[cut:].lstrip('\n')
    if rem: chunks.append(rem)
    return chunks

def telegram_api(token:str, method:str, params:Optional[Dict[str,Any]]=None, timeout:int=30)->dict:
    url=f'https://api.telegram.org/bot{token}/{method}'
    data=urlencode(params or {}).encode('utf-8')
    req=Request(url,data=data,method='POST')
    with urlopen(req,timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))

def send_long_message_with_token(token:str, chat_id:str, text:str)->None:
    for chunk in split_telegram_message(text):
        telegram_api(token,'sendMessage',{'chat_id':str(chat_id),'text':chunk,'disable_web_page_preview':True})

def send_chat_action_with_token(token:str, chat_id:str, action='typing')->None:
    try: telegram_api(token,'sendChatAction',{'chat_id':str(chat_id),'action':action},timeout=10)
    except Exception: pass

class TelegramSender:
    def __init__(self, token=None, chat_id=None, env_path='.env'):
        load_env_file(env_path); self.token=token or os.getenv('TELEGRAM_BOT_TOKEN'); self.chat_id=str(chat_id or os.getenv('TELEGRAM_CHAT_ID') or '')
        self.chat_ids=parse_chat_ids(os.getenv('TELEGRAM_CHAT_IDS')) or parse_chat_ids(os.getenv('TELEGRAM_DAILY_CHAT_IDS')) or ([self.chat_id] if self.chat_id else [])
        if not self.token: raise ValueError('TELEGRAM_BOT_TOKEN이 없습니다.')
    def api(self,method,params=None,timeout=30): return telegram_api(self.token,method,params,timeout)
    def send_message(self,text,chat_id=None,parse_mode=None):
        cid=str(chat_id or self.chat_id or '')
        if not cid: raise ValueError('전송할 chat_id가 없습니다.')
        payload={'chat_id':cid,'text':text,'disable_web_page_preview':True}
        if parse_mode: payload['parse_mode']=parse_mode
        return self.api('sendMessage',payload)
    def send_long_message(self,text,chat_id=None):
        for c in split_telegram_message(text): self.send_message(c,chat_id=chat_id)
    def broadcast_message(self,text,chat_ids:Optional[Iterable[str]]=None):
        targets=list(chat_ids or self.chat_ids); out={}
        if not targets: raise ValueError('broadcast 대상 chat_id가 없습니다.')
        for cid in targets:
            try: self.send_long_message(text,chat_id=str(cid)); out[str(cid)]=True
            except Exception: out[str(cid)]=False
        return out
