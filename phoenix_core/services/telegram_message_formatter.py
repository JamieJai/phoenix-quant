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


def compact_ranking_output(text: str, max_rows: int = 10) -> str:
    text = clean_cli_output(text)
    lines = text.split('\n')
    as_of = next((line.strip() for line in lines if line.strip().startswith('기준일:')), '')
    rows = []
    for line in lines:
        m = re.match(r'\s*(\d+)\s*\|\s*([A-Z0-9.\-]+)\s*\|\s*([0-9.]+)\s*\|\s*([0-9.]+)\s*\|\s*([0-9.]+).*?\|\s*([0-9.]+)%\s*\|\s*(.+?)\s*$', line)
        if not m:
            continue
        rank, ticker, suitability, confidence, risk, hit5, label = m.groups()
        rows.append(f'{int(rank):>2}. {ticker:<6} score {float(suitability):>4.1f} | conf {float(confidence):>4.0f} | risk {float(risk):>4.0f} | 5D {float(hit5):>3.0f}% | {label}')
        if len(rows) >= max_rows:
            break
    if not rows:
        return compact_cli_output(text, max_chars=2200)
    parts = []
    if as_of:
        parts.append(as_of)
    parts.append('Rank | Ticker | Score | Conf | Risk | 5D | Label')
    parts.extend(rows)
    return '\n'.join(parts)


def compact_analysis_output(text: str, max_chars: int = 2300) -> str:
    text = clean_cli_output(text)
    keep_prefixes = (
        'Ticker:', '기준일:', '기준가:', '단타 적합도:', '신뢰도:', '위험도:',
        'Market Regime:', '  - Regime:', 'Sector Rotation:', '  - Target:',
        'Trade Plan:', '  - 진입 기준가:', '  - 목표 매도가:', '  - 손절가:',
        '  - 최대 보유:', '  - 트레일링 스탑:',
        'Decision Breakdown:', '  - pattern_contribution:', '  - hold_contribution:',
        '  - market_contribution:', '  - sector_adjustment:', '  - regime_adjustment:',
        '  - risk_penalty:', '시장 우호도:', '업종 ETF:', 'Pattern Rarity:',
        '유지 점수:', '과거 유사 사례:', '5거래일 +5% 도달률:', '10거래일 +10% 도달률:',
        'AI Summary:'
    )
    selected = []
    for line in text.split('\n'):
        stripped = line.strip()
        if any(stripped.startswith(prefix.strip()) for prefix in keep_prefixes):
            selected.append(line)
    compact = '\n'.join(selected).strip()
    if not compact:
        return compact_cli_output(text, max_chars=max_chars)
    return compact_cli_output(compact, max_chars=max_chars)


def format_status_message(**values) -> str:
    lines = ['Phoenix Bot Status']
    ordered = [
        ('bot_profile', 'profile'),
        ('your_chat_id', 'chat_id'),
        ('project_dir', 'project'),
        ('python', 'python'),
        ('timeout_sec', 'timeout'),
        ('default_top_n', 'top_n'),
        ('intraday_enabled', 'intraday'),
        ('intraday_overlay', 'overlay'),
        ('intraday_overlay_rerank', 'rerank'),
        ('intraday_feature_cache', 'feature_cache'),
    ]
    for key, label in ordered:
        if key in values:
            lines.append(f'- {label}: {values[key]}')
    return '\n'.join(lines)
