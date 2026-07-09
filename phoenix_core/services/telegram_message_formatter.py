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
    return ('🔥 Phoenix Quant Bot\n\n명령어:\n/ping - 연결 확인\n/whoami - 내 chat_id 확인\n/top 10 - Daily 후보 50개 이상 + intraday 재정렬\n/hot 10 - 장중 관심 후보 필터\n/analyze NVDA - 상세 분석 + intraday context\n/intraday NVDA - 현재가/10m/30m/VWAP 빠른 확인\n/regime - SPY 시장 상태\n/status - 봇 설정\n/help - 도움말\n\n※ 매매 추천/자동매매가 아니라 참고용 분석 보조 도구입니다.')
def disclaimer()->str: return '※ 참고용 분석입니다. 매수/매도 추천이 아니며 최종 판단은 사용자가 직접 합니다.'


def _as_float(value, default=0.0):
    try:
        cleaned = str(value).replace('$', '').replace(',', '').replace('%', '').strip()
        return float(cleaned)
    except Exception:
        return default


def parse_ranking_rows(text: str, max_rows: int | None = None) -> list[dict]:
    text = clean_cli_output(text)
    rows = []
    for line in text.split('\n'):
        cols = [col.strip() for col in line.split('|')]
        if len(cols) < 7 or not cols[0].isdigit():
            continue
        try:
            if len(cols) >= 14:
                row = {
                    'rank': int(cols[0]),
                    'ticker': cols[1].upper(),
                    'final': _as_float(cols[2]),
                    'xgb': _as_float(cols[3]),
                    'suitability': _as_float(cols[4]),
                    'confidence': _as_float(cols[5]),
                    'risk': _as_float(cols[6]),
                    'market': _as_float(cols[7]),
                    'entry': _as_float(cols[8]),
                    'take_profit': _as_float(cols[9]),
                    'stop_loss': _as_float(cols[10]),
                    'hold': cols[11],
                    'hit5': _as_float(cols[12]),
                    'label': cols[13],
                    'reason': cols[14] if len(cols) >= 15 else '',
                }
            else:
                row = {
                    'rank': int(cols[0]),
                    'ticker': cols[1].upper(),
                    'final': _as_float(cols[2]),
                    'xgb': 0.0,
                    'suitability': _as_float(cols[2]),
                    'confidence': _as_float(cols[3]),
                    'risk': _as_float(cols[4]),
                    'market': _as_float(cols[5]) if len(cols) > 5 else 0.0,
                    'entry': 0.0,
                    'take_profit': 0.0,
                    'stop_loss': 0.0,
                    'hold': '',
                    'hit5': _as_float(cols[-2]),
                    'label': cols[-1],
                    'reason': '',
                }
        except (ValueError, IndexError):
            continue
        rows.append(row)
        if max_rows is not None and len(rows) >= max_rows:
            break
    return rows


def compact_ranking_output(text: str, max_rows: int = 10) -> str:
    text = clean_cli_output(text)
    lines = text.split('\n')
    as_of = next((line.strip() for line in lines if line.strip().startswith('기준일:')), '')
    parsed = parse_ranking_rows(text, max_rows=max_rows)
    if not parsed:
        return compact_cli_output(text, max_chars=2200)
    rows = []
    for row in parsed:
        reason = f" | {row['reason']}" if row.get('reason') else ''
        rows.append(
            f"{row['rank']:>2}. {row['ticker']:<6} final {row['final']:>4.1f} | "
            f"xgb {row['xgb']:>4.0f} | score {row['suitability']:>4.1f} | "
            f"conf {row['confidence']:>4.0f} | risk {row['risk']:>4.0f} | "
            f"5D {row['hit5']:>3.0f}% | {row['label']}{reason}"
        )
    parts = []
    if as_of:
        parts.append(as_of)
    parts.append('Rank | Ticker | Final/XGB | Score | Conf | Risk | 5D | Label | Reason')
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
        ('top_candidate_pool_n', 'candidate_pool'),
        ('hot_min_score', 'hot_min_score'),
        ('intraday_enabled', 'intraday'),
        ('intraday_overlay', 'overlay'),
        ('intraday_overlay_rerank', 'rerank'),
        ('intraday_feature_cache', 'feature_cache'),
    ]
    for key, label in ordered:
        if key in values:
            lines.append(f'- {label}: {values[key]}')
    return '\n'.join(lines)
