"""總流程：抓預報 → 融合 → 機器學習修正 → 劇烈天氣/颱風/聖嬰/警報 → 輸出 JSON。"""
import math
from datetime import datetime

import numpy as np
import pandas as pd

from . import alerts, enso, severe, tropical
from .config import MODELS, RAIN_THRESHOLD_MM
from .correction import LocationCorrector
from .data_sources import fetch_convective, fetch_ensemble, fetch_forecast
from .ensemble import blend, spread
from .net import NeedClientFetch, gather
from .verification import accuracy_by_lead, report

WEATHER_TEXT = {
    0: "晴", 1: "晴時多雲", 2: "多雲", 3: "陰", 45: "霧", 48: "霧淞",
    51: "毛毛雨", 53: "毛毛雨", 55: "毛毛雨", 56: "凍毛雨", 57: "凍毛雨",
    61: "小雨", 63: "中雨", 65: "大雨", 66: "凍雨", 67: "凍雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "米雪", 80: "陣雨", 81: "陣雨", 82: "強陣雨",
    85: "陣雪", 86: "強陣雪", 95: "雷雨", 96: "雷雨伴隨冰雹", 99: "強雷雨伴隨冰雹",
}


def _clean(obj):
    """NaN/numpy → JSON 可用的型別。"""
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        return None if math.isnan(obj) or math.isinf(obj) else round(float(obj), 2)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, (pd.Timestamp, datetime)):
        return obj.isoformat()
    return obj


def _summary(r: pd.Series, hazards: list) -> tuple[str, str]:
    """決定當天的天氣圖示與文字（以修正後的數值為主，而非單一模式的天氣代碼）。"""
    types = {h["type"] for h in hazards}
    snow, prob, rain = r.get("snowfall_sum", 0), r.get("rain_prob", 0), r.get("precipitation_sum", 0)
    if snow >= 0.5:
        return "snow", "降雪"
    if "thunderstorm" in types and prob >= 0.4:
        return "thunder", "雷陣雨" if "hail" not in types else "雷雨，可能伴隨冰雹"
    if prob >= 0.6:
        return ("heavy_rain", "大雨") if rain >= 30 else ("rain", "有雨")
    if prob >= 0.3:
        return "showers", "可能短暫雨"
    code = r.get("weather_code")
    if code in (45, 48) or "fog" in types:
        return "fog", "有霧"
    if code is not None and not pd.isna(code) and code <= 1:
        return "sun", "晴"
    if code == 2:
        return "partly", "多雲時晴"
    return "cloud", "多雲到陰"


def _beaufort(kmh: float) -> int | None:
    if kmh is None or pd.isna(kmh):
        return None
    limits = [1, 6, 12, 20, 29, 39, 50, 62, 75, 89, 103, 118]
    return next((i for i, l in enumerate(limits) if kmh < l), 12)


def _fit_to_daily(temp: pd.Series, daily: pd.DataFrame) -> pd.Series:
    """把逐時溫度曲線縮放到修正後的每日最高/最低溫，讓逐時與每日數字一致。"""
    out = temp.copy()
    for day, g in temp.groupby(temp.index.normalize()):
        if day not in daily.index or g.count() < 20:
            continue
        hi, lo = daily.at[day, "temperature_2m_max"], daily.at[day, "temperature_2m_min"]
        if pd.isna(hi) or pd.isna(lo):
            continue
        gmax, gmin = g.max(), g.min()
        if gmax - gmin >= 0.5 and hi > lo:
            out[g.index] = lo + (g - gmin) * (hi - lo) / (gmax - gmin)
        else:
            out[g.index] = g + ((hi + lo) - (gmax + gmin)) / 2
    return out


def _mean_direction(speed: pd.DataFrame, direction: pd.DataFrame) -> pd.Series:
    """各模式風向的向量平均（直接平均角度在 0°/360° 附近會出錯）。"""
    cols = [c for c in speed.columns if c in direction.columns]
    rad = np.radians(direction[cols])
    u = (-speed[cols] * np.sin(rad)).mean(axis=1)
    v = (-speed[cols] * np.cos(rad)).mean(axis=1)
    return (np.degrees(np.arctan2(-u, -v)) % 360).where(u.notna())


def build_forecast(lat: float, lon: float, name: str = "", tz: str = "auto",
                   country_code: str = "", admin_names: list[str] | None = None, log=print) -> dict:
    def optional(fn):
        # 系集與對流資料拿不到時主預報照常產生；但需要瀏覽器代抓時要一起回報
        def run():
            try:
                return fn()
            except NeedClientFetch:
                raise
            except Exception:
                return None
        return run

    # 先一次收集所有需要下載的資料，若伺服器額度被擋，瀏覽器只要代抓一輪
    corrector, fc, _, _ = gather(
        lambda: LocationCorrector(lat, lon).load_or_train(tz, log=log),
        lambda: fetch_forecast(lat, lon, tz),
        optional(lambda: fetch_ensemble(lat, lon, tz)),
        optional(lambda: fetch_convective(lat, lon, tz)))
    weights = corrector.weights

    # ---------- 每日：主要變數用機器學習修正，其他變數用相關的權重融合
    daily = corrector.correct(fc["daily"])
    extra_weights = {"wind_gusts_10m_max": weights["wind_speed_10m_max"],
                     "snowfall_sum": weights["precipitation_sum"],
                     "precipitation_hours": weights["precipitation_sum"]}
    for var in ("snowfall_sum", "wind_gusts_10m_max", "precipitation_hours", "relative_humidity_2m_mean"):
        daily[var] = blend(fc["daily"][var], extra_weights.get(var))
    codes = fc["daily"]["weather_code"]
    daily["weather_code"] = codes.mode(axis=1)[0] if not codes.empty else np.nan
    for var in ("temperature_2m_max", "temperature_2m_min", "precipitation_sum"):
        daily[var + "_spread"] = spread(fc["daily"][var])
    pmsl = fc["hourly"]["pressure_msl"].mean(axis=1)
    daily["pressure_msl_min"] = pmsl.resample("D").min().reindex(daily.index)

    # ---------- 系集：降雨機率與溫度不確定範圍
    ens_ok = True
    try:
        ens = fetch_ensemble(lat, lon, tz)
        p = ens["precipitation_sum"]
        n = p.notna().sum(axis=1).replace(0, np.nan)
        daily["p_rain_ens"] = ((p >= RAIN_THRESHOLD_MM).sum(axis=1) / n).reindex(daily.index)
        daily["p_rain_10mm"] = ((p >= 10).sum(axis=1) / n).reindex(daily.index)
        daily["p_rain_80mm"] = ((p >= 80).sum(axis=1) / n).reindex(daily.index)
        for var in ("temperature_2m_max", "temperature_2m_min"):
            t = ens[var]
            med = t.median(axis=1)
            daily[var + "_lo"] = (daily[var] + (t.quantile(0.1, axis=1) - med).reindex(daily.index))
            daily[var + "_hi"] = (daily[var] + (t.quantile(0.9, axis=1) - med).reindex(daily.index))
        s = ens["snowfall_sum"]
        daily["p_snow"] = ((s >= 0.5).sum(axis=1) / s.notna().sum(axis=1).replace(0, np.nan)).reindex(daily.index)
    except Exception as e:
        ens_ok = False
        log(f"系集預報取得失敗：{e}")

    # ---------- 劇烈天氣
    try:
        conv = fetch_convective(lat, lon, tz)
    except Exception as e:
        conv = None
        log(f"對流資料取得失敗：{e}")
    hazards = severe.assess(conv, daily, lat)

    # ---------- 準確率（依預報天數）
    acc = accuracy_by_lead(corrector.state)
    now = pd.Timestamp.now(tz=fc["meta"]["timezone"] or "UTC").tz_localize(None)
    today = now.normalize()

    days = []
    daily = daily[daily["temperature_2m_max"].notna() & daily["temperature_2m_min"].notna()]
    for d, r in daily.iterrows():
        key = d.strftime("%Y-%m-%d")
        lead = (d - today).days
        icon, text = _summary(r, hazards.get(key, []))
        a = acc.get(max(lead, 1))
        days.append({
            "date": key, "lead": lead, "icon": icon, "text": text,
            "tmax": r["temperature_2m_max"], "tmin": r["temperature_2m_min"],
            "tmax_range": [r.get("temperature_2m_max_lo"), r.get("temperature_2m_max_hi")],
            "tmin_range": [r.get("temperature_2m_min_lo"), r.get("temperature_2m_min_hi")],
            "precip_mm": r["precipitation_sum"], "rain_prob": r["rain_prob"],
            "rain_prob_ensemble": r.get("p_rain_ens"), "p_rain_10mm": r.get("p_rain_10mm"),
            "p_rain_80mm": r.get("p_rain_80mm"),
            "precip_hours": r["precipitation_hours"], "snow_cm": r["snowfall_sum"],
            "p_snow": r.get("p_snow"),
            "wind_max": r["wind_speed_10m_max"], "gust_max": r["wind_gusts_10m_max"],
            "beaufort": _beaufort(r["wind_gusts_10m_max"]),
            "humidity": r["relative_humidity_2m_mean"], "pressure_min": r["pressure_msl_min"],
            "model_spread_tmax": r["temperature_2m_max_spread"],
            "hazards": hazards.get(key, []),
            "expected_accuracy": a if lead <= 7 else None,
            "per_model": {
                "tmax": fc["daily"]["temperature_2m_max"].loc[d].to_dict(),
                "precip": fc["daily"]["precipitation_sum"].loc[d].to_dict(),
            },
        })

    # ---------- 逐時（今天 0 時起到預報結束）
    h = fc["hourly"]
    hourly_df = pd.DataFrame({
        "temperature": _fit_to_daily(blend(h["temperature_2m"], weights["temperature_2m_max"]), daily),
        "humidity": h["relative_humidity_2m"].mean(axis=1),
        "precipitation": blend(h["precipitation"], weights["precipitation_sum"]),
        "snowfall": h["snowfall"].mean(axis=1),
        "wind": blend(h["wind_speed_10m"], weights["wind_speed_10m_max"]),
        "wind_dir": _mean_direction(h["wind_speed_10m"], h["wind_direction_10m"]),
        "gust": blend(h["wind_gusts_10m"], weights["wind_speed_10m_max"]),
        "pressure": pmsl,
        "temp_spread": spread(h["temperature_2m"]),
    })
    hourly_df = hourly_df[hourly_df.index >= today]
    hourly_df = hourly_df[hourly_df["temperature"].notna()]
    if conv is not None:
        c = conv.reindex(hourly_df.index)
        hourly_df["cape"] = c["cape"]
        hourly_df["weather_code"] = c["weather_code"]
        hourly_df["precip_prob"] = c["precipitation_probability"] / 100
        hourly_df["cloud_cover"] = c["cloud_cover"]
        hourly_df["is_day"] = c["is_day"]
    future = hourly_df[hourly_df.index >= now.floor("h")]
    current = future.iloc[0].to_dict() if len(future) else {}

    # ---------- 其他模組（任一失敗不影響主預報）
    try:
        tc = tropical.assess(lat, lon)
    except Exception as e:
        tc = {"storms": [], "errors": [str(e)]}
    try:
        enso_status = enso.status()
    except Exception as e:
        enso_status = {"error": str(e)}
    official = alerts.fetch(lat, lon, country_code, admin_names)

    st = corrector.state
    return _clean({
        "location": {"name": name or f"{lat:.2f}, {lon:.2f}", "latitude": lat, "longitude": lon,
                     "timezone": fc["meta"]["timezone"], "elevation": fc["meta"]["elevation"],
                     "country_code": country_code},
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "now_local": now.floor("h").isoformat(),
        "current": current,
        "daily": days,
        "hourly": {"time": [t.isoformat() for t in hourly_df.index],
                   **{c: hourly_df[c].tolist() for c in hourly_df.columns}},
        "tropical": tc,
        "enso": enso_status,
        "alerts": official,
        "verification": report(st),
        "method": {
            "selected": st["selected"],
            "weights": {v: {MODELS[m]: w for m, w in ws.items()} for v, ws in weights.items()},
            "ensemble_available": ens_ok,
        },
        "model_names": MODELS,
    })
