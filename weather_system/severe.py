"""劇烈天氣診斷：雷雨、冰雹、龍捲風有利環境、豪雨、強風、降雪、凍雨、高溫、低溫、濃霧。

數值模式的格點（約 10~25 km）解析不出單一雷雨胞或龍捲風本身，
因此這裡判斷的是『大氣環境是否有利於發生』，屬於風險提示，不是確定預報。
門檻參考中央氣象署特報標準與劇烈對流研究（如 Taszarek et al. 2020 的 WMAXSHEAR）。
"""
import numpy as np
import pandas as pd

LEVELS = {1: "注意", 2: "警戒", 3: "危險"}


def _uv(speed_kmh, direction_deg):
    s = speed_kmh / 3.6  # → m/s
    rad = np.radians(direction_deg)
    return -s * np.sin(rad), -s * np.cos(rad)


def _shear(conv: pd.DataFrame, upper: str) -> pd.Series:
    u1, v1 = _uv(conv["wind_speed_10m"], conv["wind_direction_10m"])
    u2, v2 = _uv(conv[f"wind_speed_{upper}"], conv[f"wind_direction_{upper}"])
    return np.hypot(u2 - u1, v2 - v1)


def _hazard(kind, level, title, detail):
    return {"type": kind, "level": int(level), "level_name": LEVELS[level], "title": title, "detail": detail}


def assess(conv: pd.DataFrame | None, daily: pd.DataFrame, lat: float) -> dict[str, list]:
    """conv：逐時對流資料；daily：每日最終預報（含 tmax/tmin/precip/gust/snow/pressure…）。"""
    out = {d.strftime("%Y-%m-%d"): [] for d in daily.index}

    # ---------------- 逐時對流環境 → 每日彙整
    if conv is not None and not conv.empty:
        c = conv.copy()
        c["shear_deep"] = _shear(c, "500hPa")   # 約 0~5.5 km 垂直風切 (m/s)
        c["shear_low"] = _shear(c, "850hPa")    # 約 0~1.5 km 低層風切 (m/s)
        c["wmaxshear"] = np.sqrt(2 * c["cape"].clip(lower=0)) * c["shear_deep"]
        thunder_code = c["weather_code"].isin([95, 96, 99])
        hail_code = c["weather_code"].isin([96, 99])
        for day, g in c.groupby(c.index.normalize()):
            key = day.strftime("%Y-%m-%d")
            if key not in out:
                continue
            cape, li = g["cape"].max(), g["lifted_index"].min()
            wms = g["wmaxshear"].max()
            i = g["wmaxshear"].idxmax() if g["wmaxshear"].notna().any() else None
            sd = g.loc[i, "shear_deep"] if i is not None else np.nan
            sl = g.loc[i, "shear_low"] if i is not None else np.nan
            fzl = g["freezing_level_height"].min()
            hr_rain = g["precipitation"].max()

            # 雷雨
            convective = (g["cape"] >= 500) & (g["lifted_index"] <= -2) & (g["precipitation"] >= 0.5)
            if thunder_code.loc[g.index].any() or convective.any():
                lvl = 3 if cape >= 2500 else 2 if cape >= 1000 else 1
                out[key].append(_hazard("thunderstorm", lvl, "雷雨",
                                        f"對流可用位能 CAPE 最高 {cape:.0f} J/kg，舉升指數 {li:.1f}"))
            # 劇烈對流綜合指標
            if wms >= 400:
                lvl = 3 if wms >= 1200 else 2 if wms >= 800 else 1
                out[key].append(_hazard("severe_convection", lvl, "劇烈對流環境",
                                        f"WMAXSHEAR {wms:.0f} m²/s²（CAPE×風切），深層風切 {sd:.0f} m/s"))
            # 冰雹
            by_code = bool(hail_code.loc[g.index].any())
            if by_code or (cape >= 1500 and sd >= 15 and fzl <= 4500):
                lvl = 2 if by_code or (cape >= 2500 and sd >= 20) else 1
                reason = "模式預報出現伴隨冰雹的雷雨；" if by_code else ""
                out[key].append(_hazard("hail", lvl, "冰雹",
                                        f"{reason}CAPE {cape:.0f} J/kg、深層風切 {sd:.0f} m/s、結凍高度 {fzl:.0f} m"))
            # 龍捲風有利環境
            if cape >= 1000 and sl >= 10 and sd >= 18 and li <= -4:
                lvl = 3 if cape >= 2500 and sl >= 18 else 2 if cape >= 2000 and sl >= 15 else 1
                out[key].append(_hazard("tornado", lvl, "龍捲風有利環境",
                                        f"高不穩定度（CAPE {cape:.0f}）配合強低層風切 {sl:.0f} m/s；"
                                        "僅代表環境有利，實際是否生成無法由模式預報"))
            # 時雨量
            if hr_rain >= 40:
                out[key].append(_hazard("heavy_rain_hourly", 2 if hr_rain < 100 else 3, "短延時強降雨",
                                        f"最大時雨量約 {hr_rain:.0f} mm"))
            # 濃霧
            vis = g["visibility"].min()
            if vis < 1000:
                out[key].append(_hazard("fog", 2 if vis < 200 else 1, "濃霧",
                                        f"最低能見度約 {vis:.0f} m"))
            # 凍雨
            if g["weather_code"].isin([56, 57, 66, 67]).any():
                out[key].append(_hazard("freezing_rain", 2, "凍雨／凍毛雨", "地面低於 0°C 時降雨會結冰"))

    # ---------------- 每日變數
    for day, r in daily.iterrows():
        key = day.strftime("%Y-%m-%d")
        rain = r.get("precipitation_sum", np.nan)
        if rain >= 80:  # 中央氣象署 24 小時累積雨量分級
            lvl, name = ((3, "超大豪雨") if rain >= 500 else (3, "大豪雨") if rain >= 350
                         else (2, "豪雨") if rain >= 200 else (1, "大雨"))
            out[key].append(_hazard("heavy_rain", lvl, name, f"日累積雨量預估 {rain:.0f} mm"))
        p80 = r.get("p_rain_80mm", np.nan)
        if not rain >= 80 and p80 >= 0.2:
            out[key].append(_hazard("heavy_rain", 1, "大雨可能",
                                    f"系集預報中 {p80:.0%} 成員達 80 mm 以上"))

        gust = r.get("wind_gusts_10m_max", np.nan)
        if gust >= 62:  # 蒲福 8 級 = 陸上強風特報
            lvl = 3 if gust >= 118 else 2 if gust >= 89 else 1
            out[key].append(_hazard("wind", lvl, "強風", f"最大陣風約 {gust:.0f} km/h（{gust / 3.6:.0f} m/s）"))

        snow = r.get("snowfall_sum", np.nan)
        if snow >= 0.5:
            lvl = 3 if snow >= 20 else 2 if snow >= 5 else 1
            out[key].append(_hazard("snow", lvl, "降雪" if snow < 20 else "大雪", f"預估積雪 {snow:.1f} cm"))

        tmax, tmin = r.get("temperature_2m_max", np.nan), r.get("temperature_2m_min", np.nan)
        if tmax >= 36:
            lvl = 3 if tmax >= 40 else 2 if tmax >= 38 else 1
            out[key].append(_hazard("heat", lvl, "高溫", f"最高溫約 {tmax:.1f}°C，注意中暑"))
        if abs(lat) < 30:  # 亞熱帶/熱帶地區採中央氣象署低溫標準
            if tmin <= 10:
                lvl = 3 if tmin <= 4 else 2 if tmin <= 6 else 1
                out[key].append(_hazard("cold", lvl, "低溫", f"最低溫約 {tmin:.1f}°C"))
        elif tmin <= -15:
            lvl = 3 if tmin <= -25 else 2
            out[key].append(_hazard("cold", lvl, "酷寒", f"最低溫約 {tmin:.1f}°C"))
        elif tmin <= 0:
            out[key].append(_hazard("frost", 1, "結冰／霜", f"最低溫約 {tmin:.1f}°C，路面可能結冰"))

        pmin = r.get("pressure_msl_min", np.nan)
        if pmin <= 990 and gust >= 89:
            out[key].append(_hazard("cyclone_signal", 3 if pmin <= 970 else 2, "強烈低氣壓／熱帶氣旋影響",
                                    f"模式預報最低氣壓 {pmin:.0f} hPa，伴隨強陣風"))

    for k in out:
        out[k].sort(key=lambda h: -h["level"])
    return out
