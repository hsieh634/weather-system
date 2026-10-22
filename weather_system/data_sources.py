"""所有外部氣象資料的抓取與整理（Open-Meteo，免金鑰）。"""
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .config import (
    CONVECTIVE_VARS, DAILY_EXTRA, DAILY_TARGETS, FORECAST_DAYS, HOURLY_VARS, MODELS,
)
from .net import NeedClientFetch, get_json

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
PREVIOUS_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

_CJK = re.compile(r"[㐀-鿿]")
_LATLON = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)\s*$")


# ---------------------------------------------------------------- 地點
def geocode(query: str, count: int = 8) -> list[dict]:
    """城市名稱或「緯度,經度」→ 候選地點清單。"""
    m = _LATLON.match(query)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        return [{"name": f"{lat:.3f}, {lon:.3f}", "latitude": lat, "longitude": lon,
                 "country": "", "admin1": "", "country_code": "", "timezone": "auto"}]
    q = query.strip()
    if _CJK.search(q):
        # 中文地名資料庫多半要完整名稱（台北 → 臺北市），自動嘗試常見寫法
        base = {q, q.replace("台", "臺"), q.replace("臺", "台")}
        suffixes = ("市", "縣", "區", "鄉", "鎮")
        variants = list(base) + [b + s for b in base for s in suffixes if not b.endswith(suffixes)]
        lang = "zh"
    else:
        variants, lang = [q], "en"

    def search(v):
        try:
            return get_json(GEOCODE_URL, {"name": v, "count": count, "language": lang, "format": "json"},
                            ttl=86400 * 7).get("results") or []
        except NeedClientFetch as e:
            return e

    with ThreadPoolExecutor(max_workers=4) as pool:
        batches = list(pool.map(search, variants))
    need = [s for b in batches if isinstance(b, NeedClientFetch) for s in b.specs]
    if need:
        raise NeedClientFetch(need)
    seen, results = set(), []
    for r in (r for batch in batches for r in batch):
        key = (r.get("name"), round(r["latitude"], 2), round(r["longitude"], 2))
        if key not in seen:
            seen.add(key)
            results.append(r)
    # 同名地點以人口多者優先（例如「高雄」優先台灣高雄市，而非同名小村）；台灣地點其次
    results.sort(key=lambda r: (-(r.get("population") or 0), r.get("country_code") != "TW"))
    places = [{
        "name": r.get("name"),
        "latitude": r["latitude"],
        "longitude": r["longitude"],
        "country": r.get("country", ""),
        "admin1": (r.get("admin1") or "").split(" or ")[0],
        "admin2": r.get("admin2", ""),
        "country_code": (r.get("country_code") or "").upper(),
        "timezone": r.get("timezone", "auto"),
        "elevation": r.get("elevation"),
    } for r in results[:count]]
    return places or _nominatim(q, count)


def _nominatim(query: str, count: int) -> list[dict]:
    """備援：OpenStreetMap Nominatim，對中文與小地名的支援較好。"""
    try:
        data = get_json(NOMINATIM_URL, {"q": query, "format": "jsonv2", "limit": count,
                                        "addressdetails": 1, "accept-language": "zh-TW,zh,en"},
                        ttl=86400 * 7)
    except Exception:
        return []
    out = []
    for r in data:
        a = r.get("address", {})
        out.append({
            "name": r.get("name") or r.get("display_name", "").split(",")[0],
            "latitude": float(r["lat"]), "longitude": float(r["lon"]),
            "country": a.get("country", ""),
            "admin1": a.get("state") or a.get("city") or a.get("county") or "",
            "admin2": a.get("county") or a.get("city") or "",
            "country_code": (a.get("country_code") or "").upper(),
            "timezone": "auto", "elevation": None,
        })
    return out


# ---------------------------------------------------------------- 工具
def _model_frame(block: dict, var: str, models) -> pd.DataFrame:
    """把 Open-Meteo 多模式回應中的某變數整理成 (時間 × 模式) 表格。"""
    idx = pd.to_datetime(block["time"])
    cols = {}
    for m in models:
        key = f"{var}_{m}"
        if key in block:
            cols[m] = pd.to_numeric(pd.Series(block[key], index=idx), errors="coerce")
        elif len(models) == 1 and var in block:
            cols[m] = pd.to_numeric(pd.Series(block[var], index=idx), errors="coerce")
    df = pd.DataFrame(cols, index=idx, dtype=float)
    return df.dropna(axis=1, how="all")


def _single_frame(block: dict, variables) -> pd.DataFrame:
    idx = pd.to_datetime(block["time"])
    return pd.DataFrame({v: pd.to_numeric(pd.Series(block.get(v, [np.nan] * len(idx)), index=idx),
                                          errors="coerce") for v in variables}, index=idx)


# ---------------------------------------------------------------- 即時預報
def fetch_forecast(lat: float, lon: float, tz: str = "auto") -> dict:
    """各模式未來 16 天逐時與每日預報。回傳 {'daily': {var: DataFrame}, 'hourly': {...}, 'meta'}。"""
    models = list(MODELS)
    data = get_json(FORECAST_URL, {
        "latitude": lat, "longitude": lon, "timezone": tz,
        "forecast_days": FORECAST_DAYS,
        "models": ",".join(models),
        "daily": ",".join(DAILY_TARGETS + DAILY_EXTRA),
        "hourly": ",".join(HOURLY_VARS),
        "wind_speed_unit": "kmh",
    }, ttl=1800)
    return {
        "daily": {v: _model_frame(data["daily"], v, models) for v in DAILY_TARGETS + DAILY_EXTRA},
        "hourly": {v: _model_frame(data["hourly"], v, models) for v in HOURLY_VARS},
        "meta": {"timezone": data.get("timezone"), "elevation": data.get("elevation"),
                 "utc_offset_seconds": data.get("utc_offset_seconds", 0)},
    }


def fetch_convective(lat: float, lon: float, tz: str = "auto") -> pd.DataFrame:
    """劇烈天氣診斷所需的對流與高空風資料（最佳匹配模式，逐時）。"""
    data = get_json(FORECAST_URL, {
        "latitude": lat, "longitude": lon, "timezone": tz, "forecast_days": FORECAST_DAYS,
        "hourly": ",".join(CONVECTIVE_VARS), "wind_speed_unit": "kmh",
    }, ttl=1800)
    return _single_frame(data["hourly"], CONVECTIVE_VARS)


def fetch_ensemble(lat: float, lon: float, tz: str = "auto") -> dict:
    """ECMWF(51 成員) + GFS(31 成員) 系集預報 → 每日降雨機率與溫度不確定範圍。"""
    data = get_json(ENSEMBLE_URL, {
        "latitude": lat, "longitude": lon, "timezone": tz, "forecast_days": 15,
        "models": "ecmwf_ifs025,gfs025",
        "daily": "precipitation_sum,temperature_2m_max,temperature_2m_min,snowfall_sum",
    }, ttl=3600)
    block = data["daily"]
    idx = pd.to_datetime(block["time"])
    out = {}
    for var in ("precipitation_sum", "temperature_2m_max", "temperature_2m_min", "snowfall_sum"):
        cols = [k for k in block if k == var or k.startswith(var + "_")]
        members = pd.DataFrame({k: pd.to_numeric(pd.Series(block[k], index=idx), errors="coerce")
                                for k in cols}, index=idx, dtype=float)
        out[var] = members.dropna(axis=1, how="all")
    return out


# ---------------------------------------------------------------- 歷史資料（訓練與驗證用）
def fetch_previous_runs_daily(lat: float, lon: float, start: date, end: date, lead: int,
                              tz: str = "auto") -> dict[str, pd.DataFrame]:
    """各模式『在 lead 天前真正發出的預報』，彙整成每日值（時間×模式）。

    使用 Open-Meteo Previous Runs API：每個小時的值都來自 lead 天前的那次模式運算，
    因此 lead=1 代表『明天的預報』、lead=5 代表『5 天後的預報』，不會偷看答案。
    """
    names = {v: f"{v}_previous_day{lead}" for v in ("temperature_2m", "precipitation", "wind_speed_10m")}

    def one_model(m):
        # 一次請求所有模式時伺服器回傳很慢，分模式下載快得多
        try:
            data = get_json(PREVIOUS_RUNS_URL, {
                "latitude": lat, "longitude": lon, "timezone": tz,
                "start_date": start.isoformat(), "end_date": end.isoformat(),
                "models": m, "hourly": ",".join(names.values()), "wind_speed_unit": "kmh",
            }, ttl=86400, timeout=180)
        except NeedClientFetch as e:
            return e
        except Exception:
            return {}
        return {v: _model_frame(data["hourly"], n, [m]) for v, n in names.items()}

    with ThreadPoolExecutor(max_workers=2) as pool:  # Open-Meteo 限制同時連線數
        results = list(pool.map(one_model, MODELS))
    need = [s for r in results if isinstance(r, NeedClientFetch) for s in r.specs]
    if need:  # 一次把 7 個模式都交給瀏覽器代抓
        raise NeedClientFetch(need)
    parts = [p for p in results if p]
    if not parts:
        raise RuntimeError("歷次預報資料下載失敗")
    t, p, w = (pd.concat([part[v] for part in parts], axis=1) for v in names)
    full = t.resample("D").count() >= 20  # 一天至少 20 小時有資料才算
    return {
        "temperature_2m_max": t.resample("D").max().where(full),
        "temperature_2m_min": t.resample("D").min().where(full),
        "precipitation_sum": p.resample("D").sum(min_count=20),
        "wind_speed_10m_max": w.resample("D").max().where(w.resample("D").count() >= 20),
    }


def fetch_observations(lat: float, lon: float, start: date, end: date,
                       tz: str = "auto") -> pd.DataFrame:
    """ERA5 再分析（實際發生的天氣，作為訓練與驗證的『正確答案』）。"""
    data = get_json(ARCHIVE_URL, {
        "latitude": lat, "longitude": lon, "timezone": tz,
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "daily": ",".join(DAILY_TARGETS), "wind_speed_unit": "kmh",
    }, ttl=86400, timeout=180)
    return _single_frame(data["daily"], DAILY_TARGETS)


def training_window(days: int, lag: int) -> tuple[date, date]:
    end = date.today() - timedelta(days=lag)
    return end - timedelta(days=days - 1), end
