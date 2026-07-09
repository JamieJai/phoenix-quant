from __future__ import annotations
import re
from typing import Iterable
from phoenix_core.engines.intraday_context_engine import IntradayContext
from phoenix_core.intraday_overlay_ranker import rank_intraday_overlay_contexts

EXCLUDE_TOKENS={
    'TOP','ETF','USD','KST','UTC','AI','API','CSV','HTML','INFO','WARN','ERROR',
    'PHOENIX','QUANT','BUY','SELL','HOLD','NONE','RISK','SCORE','XGB','TP','SL',
    'VWAP','FINAL','CONF','LABEL','RANK',
}
NO_INTRADAY_DATA_LABELS={'NO_DATA','DATA_ERROR','ERROR'}
RANKING_ROW_TICKER_RE=re.compile(r'(?im)^\s*\d+\s*(?:[.]\s+|\|\s*)([A-Z][A-Z0-9\.\-]{0,7})\b')

def _money(x): return '-' if x is None else f'${x:,.2f}'
def _pct(x): return '-' if x is None else f'{x:+.2f}%'
def _ratio(x): return '-' if x is None else f'{x:.2f}x'

def format_intraday_context(ctx: IntradayContext) -> str:
    if ctx.label in {'NO_DATA','DATA_ERROR','ERROR'}:
        notes='\n'.join(f'- {n}' for n in ctx.notes[:3]) if ctx.notes else '-'
        return f'📡 Intraday Context - {ctx.ticker}\n상태: {ctx.label}\n{notes}'
    vwap_state='-'
    if ctx.above_vwap is not None:
        vwap_state='VWAP 위' if ctx.above_vwap else 'VWAP 아래'
    notes='\n'.join(f'- {n}' for n in ctx.notes[:3]) if ctx.notes else '- 특이 주의사항 없음'
    return (
        f'📡 Intraday - {ctx.ticker}\n'
        f'Score {ctx.intraday_score}/100 | Risk {ctx.intraday_risk_score}/100 | {ctx.label}\n'
        f'현재 {_money(ctx.current_price)} | 전일 {_pct(ctx.current_vs_prev_close_pct)} | 세션 {_pct(ctx.intraday_return_pct)}\n'
        f'10m {_pct(ctx.latest_10m_return_pct)} | 30m {_pct(ctx.latest_30m_return_pct)} | Vol {_ratio(ctx.intraday_volume_ratio)}\n'
        f'VWAP {_pct(ctx.vwap_position_pct)} ({vwap_state}) | 고점대비 {_pct(ctx.pullback_from_intraday_high_pct)}\n\n'
        f'주의:\n{notes}'
    )

def format_intraday_overlay(contexts: Iterable[IntradayContext], max_items:int=5, rerank:bool=True) -> str:
    rows=[]
    contexts_list=filter_intraday_overlay_contexts(contexts)
    if rerank:
        ranked=rank_intraday_overlay_contexts(contexts_list,max_items=max_items)
        for i,item in enumerate(ranked,1):
            ctx=item.context
            rows.append(f'{i}. {ctx.ticker}  adj {item.adjusted_score:.0f} | daily #{item.original_rank} | intra {ctx.intraday_score} | risk {ctx.intraday_risk_score}\n   현재 {_money(ctx.current_price)} | 전일 {_pct(ctx.current_vs_prev_close_pct)} | 10m {_pct(ctx.latest_10m_return_pct)} | VWAP {_pct(ctx.vwap_position_pct)}')
    else:
        for i,ctx in enumerate(contexts_list[:max_items],1):
            rows.append(f'{i}. {ctx.ticker}  intra {ctx.intraday_score} | risk {ctx.intraday_risk_score}\n   현재 {_money(ctx.current_price)} | 전일 {_pct(ctx.current_vs_prev_close_pct)} | 10m {_pct(ctx.latest_10m_return_pct)} | VWAP {_pct(ctx.vwap_position_pct)}')
    title='📡 Intraday Overlay' + (' - 장중 재정렬' if rerank else '')
    return title + '\n' + ('\n'.join(rows) if rows else '후보 티커를 추출하지 못했습니다.')

def extract_candidate_tickers(text:str, limit:int=10)->list[str]:
    found=[]
    for m in RANKING_ROW_TICKER_RE.finditer(text or ''):
        t=m.group(1).upper()
        if _valid(t) and t not in found:
            found.append(t)
            if len(found)>=limit: return found
    return found

def filter_intraday_overlay_contexts(contexts: Iterable[IntradayContext]) -> list[IntradayContext]:
    return [ctx for ctx in contexts if ctx.label not in NO_INTRADAY_DATA_LABELS and ctx.current_price is not None]

def _valid(t):
    return bool(t and t not in EXCLUDE_TOKENS and len(t)<=8 and re.search(r'[A-Z]',t))
