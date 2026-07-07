from __future__ import annotations
import re
from datetime import datetime
ANSI_RE=re.compile(r'\x1b\[[0-9;]*m')
def clean_cli_output(text:str)->str:
    text=ANSI_RE.sub('',text or '').replace('\r\n','\n').replace('\r','\n')
    lines=[x.rstrip() for x in text.split('\n')]; out=[]; blank=False
    for line in lines:
        if not line.strip():
            if not blank: out.append('')
            blank=True
        else:
            out.append(line); blank=False
    return '\n'.join(out).strip()
def compact_cli_output(text:str,max_chars:int=3300)->str:
    text=clean_cli_output(text)
    if len(text)<=max_chars: return text
    return f'{text[:900].rstrip()}\n\n...\n[중간 출력 생략]\n...\n\n{text[-(max_chars-1100):].lstrip()}'
def header(title:str)->str:
    return f'🔥 Phoenix Quant\n{title}\n시간: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}'
def help_message()->str:
    return ('🔥 Phoenix Quant Bot\n\n명령어:\n/ping - 연결 확인\n/whoami - 내 chat_id 확인\n/top 5 - Top 후보 + intraday overlay\n/analyze NVDA - 상세 분석 + intraday context\n/intraday NVDA - 현재가/10m/30m/VWAP 빠른 확인\n/regime - SPY 시장 상태\n/status - 봇 설정\n/help - 도움말\n\n※ 매매 추천/자동매매가 아니라 참고용 분석 보조 도구입니다.')
def disclaimer()->str: return '※ 참고용 분석입니다. 매수/매도 추천이 아니며 최종 판단은 사용자가 직접 합니다.'
