"""真實地面氣象站觀測（NOAA GHCN-Daily，免金鑰）。

有測站時，用溫度計/雨量筒的實測值當『正確答案』來訓練與驗證，
比 ERA5 再分析更能反映真實準確率。台灣測站未公開於 NOAA，需改用中央氣象署資料。
"""
from datetime import date

import numpy as np
import pandas as pd

from .config import CACHE_DIR
from .net import get_json, get_text

STATIONS_URL = "https://www.ncei.noaa.gov/pub/data/ghcn/daily/ghcnd-stations.txt"
DATA_URL = "https://www.ncei.noaa.gov/access/services/data/v1"
MAX_DISTANCE_KM = 25
MIN_COVERAGE = 0.7

_stations: pd.DataFrame | None = None


def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(a))


def _load_stations() -> pd.DataFrame:
    global _stations
    if _stations is None:
        cache = CACHE_DIR / "ghcnd_stations.csv"
        if cache.exists():
            _stations = pd.read_csv(cache, dtype={"id": str})
        else:
            text = get_text(STATIONS_URL, ttl=0, timeout=180)
            rows = [(ln[0:11], float(ln[12:20]), float(ln[21:30]), float(ln[31:37]), ln[41:71].strip())
                    for ln in text.splitlines() if len(ln) >= 41]
            _stations = pd.DataFrame(rows, columns=["id", "lat", "lon", "elev", "name"])
            # 排除只有雨量的志工站（CoCoRaHS，代碼 US1…），保留有溫度的正式測站
            _stations = _stations[~_stations["id"].str.startswith("US1")]
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            _stations.to_csv(cache, index=False)
    return _stations


def nearby_stations(lat: float, lon: float, limit: int = 5) -> pd.DataFrame:
    st = _load_stations()
    box = st[(st.lat.between(lat - 0.5, lat + 0.5)) & (st.lon.between(lon - 0.6, lon + 0.6))].copy()
    if box.empty:
        return box
    box["distance_km"] = haversine_km(lat, lon, box.lat.to_numpy(), box.lon.to_numpy())
    box = box[box.distance_km <= MAX_DISTANCE_KM].sort_values("distance_km")
    # 機場/正式氣象站（第 3 碼為 W）優先，資料品質較好
    box["priority"] = (box["id"].str[2] != "W").astype(int)
    return box.sort_values(["priority", "distance_km"]).head(limit)


def fetch_station_daily(station_id: str, start: date, end: date) -> pd.DataFrame:
    data = get_json(DATA_URL, {
        "dataset": "daily-summaries", "stations": station_id,
        "startDate": start.isoformat(), "endDate": end.isoformat(),
        "dataTypes": "TMAX,TMIN,PRCP", "format": "json", "units": "metric",
    }, ttl=86400, timeout=180)
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    df.index = pd.to_datetime(df.pop("DATE"))
    rename = {"TMAX": "temperature_2m_max", "TMIN": "temperature_2m_min", "PRCP": "precipitation_sum"}
    out = pd.DataFrame(index=df.index)
    for k, v in rename.items():
        out[v] = pd.to_numeric(df[k], errors="coerce") if k in df else np.nan
    return out


def find_station_obs(lat: float, lon: float, start: date, end: date, log=print):
    """回傳 (測站資訊 dict, 每日觀測 DataFrame)；附近沒有足夠資料的測站則回傳 (None, None)。"""
    try:
        candidates = nearby_stations(lat, lon)
    except Exception as e:  # 測站清單下載失敗不影響主流程
        log(f"測站清單無法取得：{e}")
        return None, None
    n_days = (end - start).days + 1
    for _, st in candidates.iterrows():
        try:
            obs = fetch_station_daily(st["id"], start, end)
        except Exception:
            continue
        if obs.empty:
            continue
        cov = obs["temperature_2m_max"].notna().sum() / n_days
        if cov >= MIN_COVERAGE:
            info = {"id": st["id"], "name": st["name"], "distance_km": round(float(st["distance_km"]), 1),
                    "elevation": float(st["elev"]), "coverage": round(float(cov), 2)}
            log(f"找到氣象站 {st['name']}（{info['distance_km']} km，資料完整度 {cov:.0%}）")
            return info, obs
    return None, None
