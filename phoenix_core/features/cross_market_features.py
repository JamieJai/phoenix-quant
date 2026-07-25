from __future__ import annotations
from typing import Mapping
import numpy as np
import pandas as pd

CROSS_MARKET_FEATURE_NAMES = ["cross_spy_return_20d", "cross_qqq_return_20d", "cross_smh_return_20d", "cross_vix_level", "cross_smh_relative_spy_20d", "cross_smh_relative_qqq_20d", "cross_semiconductor_breadth_20d"]
def _ret(df, n=20):
    if df is None or df.empty or "Close" not in df: return 0.0
    d=df.loc[:].sort_index(); c=pd.to_numeric(d["Close"], errors="coerce").dropna()
    return float(c.iloc[-1]/c.iloc[-n-1]-1) if len(c)>n and c.iloc[-n-1] else 0.0
def compute_cross_market_features(market_data: Mapping[str,pd.DataFrame], as_of=None, target=None):
    d={k:(v.loc[:pd.Timestamp(as_of)] if as_of is not None and v is not None else v) for k,v in market_data.items()}
    spy,qqq,smh=(_ret(d.get(k)) for k in ("SPY","QQQ","SMH"))
    v=d.get("^VIX",d.get("VIX")); c=pd.to_numeric(v["Close"],errors="coerce").dropna() if v is not None and not v.empty and "Close" in v else pd.Series(dtype=float)
    peers=[d[k] for k in ("SMH","SOXX") if k in d]; breadth=float(np.mean([_ret(x)>0 for x in peers])) if peers else 0.0
    return {"cross_spy_return_20d":spy,"cross_qqq_return_20d":qqq,"cross_smh_return_20d":smh,"cross_vix_level":float(c.iloc[-1]) if len(c) else 0.0,"cross_smh_relative_spy_20d":smh-spy,"cross_smh_relative_qqq_20d":smh-qqq,"cross_semiconductor_breadth_20d":breadth}
