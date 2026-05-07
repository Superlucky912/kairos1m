"""
time_utils.py
=============
이 모듈은 UTC 타임스탬프를 한국 표준시(KST)로 변환하거나 
출력 포맷을 통일하는 시간 관련 유틸리티를 제공합니다.
"""

from datetime import datetime, timezone, timedelta
from typing import Any, cast

import pandas as pd

def to_kst(ts: pd.Timestamp | datetime | int | float | str | Any | None) -> str:
    """
    모든 형태의 타임스탬프를 KST 시간 문자열로 변환합니다.
    형식: YYYY-MM-DD HH:MM:SS (KST)
    """
    if ts is None:
        return "N/A"
        
    try:
        if isinstance(ts, (int, float)):
            # 밀리초(ms) 단위 처리
            ts_value = float(ts)
            if ts_value > 1e11:
                ts_value /= 1000
            dt = datetime.fromtimestamp(ts_value, tz=timezone.utc)
        elif isinstance(ts, str):
            dt = pd.to_datetime(ts, utc=True)
        elif isinstance(ts, pd.Timestamp):
            dt = cast(datetime, ts.to_pydatetime())
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        elif isinstance(ts, datetime):
            dt = ts
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        else:
            return str(ts)

        # KST 변환 (+9시간)
        kst_dt = dt.astimezone(timezone(timedelta(hours=9)))
        return kst_dt.strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return str(ts)
