from __future__ import annotations
from dataclasses import dataclass, asdict, field
from datetime import datetime
from typing import Optional, Any
import math, warnings
import pandas as pd

from phoenix_core.intraday_features import build_intraday_feature_dict

@dataclass
class IntradayContext:
    ticker: str
    timestamp: str
    source: str
    current_price: Optional[float]
    previous_close: Optional[float]
    current_vs_prev_close_pct: Optional[float]
    day_open: Optional[float]
    intraday_return_pct: Optional[float]
    latest_10m_return_pct: Optional[float]
    latest_30m_return_pct: Optional[float]
    today_volume: Optional[float]
    avg_intraday_volume: Optional[float]
    intraday_volume_ratio: Optional[float]
    vwap: Optional[float]
    vwap_position_pct: Optional[float]
    above_vwap: Optional[bool]
    intraday_high: Optional[float]
    pullback_from_intraday_high_pct: Optional[float]
    intraday_score: int
    intraday_risk_score: int
    label: str
    notes: list[str]
    features: dict[str, float] = field(default_factory=dict)
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

def _f(x):
    try:
        if x is None: return None
        v=float(x)
        if math.isnan(v) or math.isinf(v): return None
        return v
    except Exception:
        return None

def _pct(a,b):
    if a is None or b in (None,0): return None
    return (a/b-1.0)*100.0

def _flat(df):
    if df is None or df.empty: return pd.DataFrame()
    out=df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns=[c[0] if isinstance(c,tuple) else c for c in out.columns]
    mp={}
    for c in out.columns:
        lc=str(c).lower()
        if lc=='open': mp[c]='Open'
        elif lc=='high': mp[c]='High'
        elif lc=='low': mp[c]='Low'
        elif lc=='close': mp[c]='Close'
        elif lc=='adj close': mp[c]='Adj Close'
        elif lc=='volume': mp[c]='Volume'
    return out.rename(columns=mp)

def _last_close(df):
    if df is None or df.empty or 'Close' not in df.columns: return None
    s=pd.to_numeric(df['Close'],errors='coerce').dropna()
    return _f(s.iloc[-1]) if len(s) else None

class IntradayContextEngine:
    def __init__(self, intraday_period='5d', interval_10m='10m', interval_30m='30m', include_prepost=True):
        self.intraday_period=intraday_period; self.interval_10m=interval_10m; self.interval_30m=interval_30m; self.include_prepost=include_prepost
    def analyze_many(self,tickers:list[str])->list[IntradayContext]:
        return [self.analyze(t) for t in tickers]
    def analyze(self,ticker:str)->IntradayContext:
        ticker=ticker.upper().strip(); notes=[]
        try:
            import yfinance as yf
        except Exception as e:
            return self._err(ticker,'DATA_ERROR',[f'yfinance import 실패: {type(e).__name__}: {e}'])
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                df10=_flat(yf.download(ticker,period=self.intraday_period,interval=self.interval_10m,prepost=self.include_prepost,auto_adjust=False,progress=False,threads=False))
                df30=_flat(yf.download(ticker,period=self.intraday_period,interval=self.interval_30m,prepost=self.include_prepost,auto_adjust=False,progress=False,threads=False))
                dfd=_flat(yf.download(ticker,period='10d',interval='1d',prepost=self.include_prepost,auto_adjust=False,progress=False,threads=False))
        except Exception as e:
            return self._err(ticker,'DATA_ERROR',[f'yfinance download 실패: {type(e).__name__}: {e}'])
        if df10.empty and df30.empty and dfd.empty:
            return self._err(ticker,'NO_DATA',['intraday/daily 데이터를 가져오지 못했습니다.'])
        cur=_last_close(df10) or _last_close(df30) or _last_close(dfd)
        prev=self._previous_close(dfd)
        gap=_pct(cur,prev)
        day10=self._latest_session(df10); day30=self._latest_session(df30)
        day_open=self._first(day10,'Open') or self._first(day30,'Open')
        intraday_ret=_pct(cur,day_open)
        r10=self._lookback(df10,3); r30=self._lookback(df30,2)
        today_vol,avg_vol,vol_ratio=self._volume(df10)
        vwap=self._vwap(day10); vwap_pos=_pct(cur,vwap); above=None if vwap_pos is None else vwap_pos>0
        high=self._max(day10,'High') or self._max(day30,'High')
        pull=_pct(cur,high)
        score,risk,label,score_notes=self._score(gap,intraday_ret,r10,r30,vol_ratio,vwap_pos,pull)
        notes+=score_notes
        if self.include_prepost: notes.append('prepost=True 기준입니다. 무료 데이터는 지연/누락될 수 있습니다.')
        features=build_intraday_feature_dict(
            gap_prev_close_pct=gap,
            session_return_pct=intraday_ret,
            ret_fast_3bar_pct=r10,
            ret_slow_2bar_pct=r30,
            relative_intraday_volume=vol_ratio,
            vwap_position_pct=vwap_pos,
            pullback_from_intraday_high_pct=pull,
            intraday_score=float(score),
            intraday_risk_score=float(risk),
        )
        return IntradayContext(ticker,datetime.now().isoformat(timespec='seconds'),'yfinance',cur,prev,gap,day_open,intraday_ret,r10,r30,today_vol,avg_vol,vol_ratio,vwap,vwap_pos,above,high,pull,score,risk,label,notes,features)
    def _err(self,ticker,label,notes):
        return IntradayContext(ticker,datetime.now().isoformat(timespec='seconds'),'yfinance',None,None,None,None,None,None,None,None,None,None,None,None,None,None,None,0,100,label,notes)
    def _previous_close(self,df):
        if df is None or df.empty or 'Close' not in df.columns: return None
        s=pd.to_numeric(df['Close'],errors='coerce').dropna()
        if len(s)>=2: return _f(s.iloc[-2])
        if len(s)==1: return _f(s.iloc[-1])
        return None
    def _latest_session(self,df):
        if df is None or df.empty: return pd.DataFrame()
        out=df.copy(); idx=pd.to_datetime(out.index,errors='coerce'); out=out.loc[~pd.isna(idx)].copy()
        if out.empty: return pd.DataFrame()
        out['_date']=pd.to_datetime(out.index).date; latest=out['_date'].iloc[-1]
        return out[out['_date']==latest].drop(columns=['_date'],errors='ignore')
    def _first(self,df,col):
        if df is None or df.empty or col not in df.columns: return None
        s=pd.to_numeric(df[col],errors='coerce').dropna(); return _f(s.iloc[0]) if len(s) else None
    def _max(self,df,col):
        if df is None or df.empty or col not in df.columns: return None
        s=pd.to_numeric(df[col],errors='coerce').dropna(); return _f(s.max()) if len(s) else None
    def _lookback(self,df,bars):
        if df is None or df.empty or 'Close' not in df.columns: return None
        s=pd.to_numeric(df['Close'],errors='coerce').dropna()
        if len(s)<=bars: return None
        return _pct(_f(s.iloc[-1]), _f(s.iloc[-1-bars]))
    def _volume(self,df):
        if df is None or df.empty or 'Volume' not in df.columns: return None,None,None
        out=df.copy(); out['Volume']=pd.to_numeric(out['Volume'],errors='coerce').fillna(0.0); out['_date']=pd.to_datetime(out.index).date
        g=out.groupby('_date')['Volume'].sum()
        if g.empty: return None,None,None
        today=_f(g.iloc[-1]); prev=g.iloc[:-1]; avg=_f(prev.tail(4).mean()) if len(prev) else None
        ratio=today/avg if today is not None and avg and avg>0 else None
        return today,avg,ratio
    def _vwap(self,df):
        if df is None or df.empty or 'Close' not in df.columns or 'Volume' not in df.columns: return None
        c=pd.to_numeric(df['Close'],errors='coerce'); v=pd.to_numeric(df['Volume'],errors='coerce').fillna(0.0); m=c.notna()&(v>0)
        if not m.any() or v[m].sum()<=0: return None
        return _f((c[m]*v[m]).sum()/v[m].sum())
    def _score(self,gap,dayret,r10,r30,vr,vpos,pull):
        score=0.0; risk=25.0; notes=[]
        if gap is not None:
            score += min(max(gap,0)/8,1)*25 if gap>0 else max(0,10+gap)
            if gap>=8: risk+=25; notes.append('전일 대비 갭이 큽니다. 추격 리스크가 있습니다.')
            elif gap<=-5: risk+=15; notes.append('전일 대비 약세 갭입니다.')
        if dayret is not None:
            if dayret>0: score+=min(dayret/4,1)*15
            elif dayret<-1: risk+=10
        if r10 is not None:
            if r10>0: score+=min(r10/2,1)*15
            elif r10<-1: risk+=10
        if r30 is not None:
            if r30>0: score+=min(r30/3,1)*15
            elif r30<-1.5: risk+=10
        if vr is not None:
            if vr>=1: score+=min((vr-1)/2,1)*15
            if vr>=3: risk+=8; notes.append('거래량이 크게 증가했습니다. 변동성 확대 가능성이 있습니다.')
        if vpos is not None:
            if vpos>0: score+=min(vpos/2,1)*15
            else: risk+=8
        if pull is not None:
            if pull>-1: score+=10
            elif pull<-4: risk+=15; notes.append('당일 고점 대비 밀림이 큽니다.')
        score=int(max(0,min(100,round(score)))); risk=int(max(0,min(100,round(risk))))
        label='STRONG_INTRADAY_MOMENTUM' if score>=75 and risk<65 else 'POSITIVE_INTRADAY_CONTEXT' if score>=55 else 'MIXED_INTRADAY_CONTEXT' if score>=35 else 'WEAK_INTRADAY_CONTEXT'
        return score,risk,label,notes
