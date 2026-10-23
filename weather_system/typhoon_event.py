"""颱風／豪雨事件：台灣各縣市災害程度與停班停課機率。

做法：
- 取 ECMWF（51 組）與 GFS（31 組）系集預報，在各縣市代表點計算每天的
  最大平均風、最大陣風、日累積雨量。
- 「停班停課機率」= 系集成員中，達到《天然災害停止上班及上課作業辦法》參考標準的比例：
    平均風力 7 級以上（≥ 50 km/h）或陣風 10 級以上（≥ 89 km/h），
    或 24 小時雨量 ≥ 200 mm（平地）。
  實際是否停班停課由各縣市政府綜合判斷並公告，這裡只是依預報估計，不是官方決定。
- 「災害程度」依風雨強度分 0~4 級。
"""
import json
from pathlib import Path
from datetime import date, datetime, time, timedelta, timezone

import numpy as np

from .maps import cyclone_tracks
from .config import CACHE_DIR
from .net import get_text, post_json
from .stations import haversine_km

ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
TW_CENTER = (23.7, 121.0)
TZ = timezone(timedelta(hours=8))

# 代表點：縣市政府所在地附近的平地；mountain = 轄內有大面積山區（土石流風險）
COUNTIES = {
    "A": ("台北市", 25.0375, 121.5637, False), "F": ("新北市", 25.0120, 121.4650, True),
    "C": ("基隆市", 25.1276, 121.7392, False), "H": ("桃園市", 24.9937, 121.3010, True),
    "O": ("新竹市", 24.8039, 120.9647, False), "J": ("新竹縣", 24.8270, 121.0128, True),
    "K": ("苗栗縣", 24.5602, 120.8214, True), "B": ("台中市", 24.1618, 120.6469, True),
    "N": ("彰化縣", 24.0809, 120.5381, False), "M": ("南投縣", 23.9097, 120.6839, True),
    "P": ("雲林縣", 23.7092, 120.4313, False), "I": ("嘉義市", 23.4801, 120.4491, False),
    "Q": ("嘉義縣", 23.4588, 120.2927, True), "D": ("台南市", 22.9920, 120.1850, False),
    "E": ("高雄市", 22.6273, 120.3014, True), "T": ("屏東縣", 22.6727, 120.4880, True),
    "G": ("宜蘭縣", 24.7570, 121.7530, True), "U": ("花蓮縣", 23.9910, 121.6110, True),
    "V": ("台東縣", 22.7583, 121.1444, True), "X": ("澎湖縣", 23.5711, 119.5793, False),
    "W": ("金門縣", 24.4321, 118.3171, False), "Z": ("連江縣", 26.1602, 119.9517, False),
}
WIND_7, GUST_10, RAIN_PLAIN = 50.0, 89.0, 200.0
LEVELS = ["無明顯影響", "輕度", "中度", "嚴重", "極嚴重"]
CRITERIA = ("參考《天然災害停止上班及上課作業辦法》：預測平均風力 7 級以上或陣風 10 級以上，"
            "或 24 小時累積雨量達 200 mm（平地）。實際以各縣市政府及行政院人事行政總處公告為準。")


def _level(gust, wind, rain):
    g = 4 if gust >= 150 else 3 if gust >= 118 else 2 if gust >= 89 else 1 if gust >= 62 else 0
    w = 2 if wind >= 62 else 1 if wind >= 50 else 0
    r = 4 if rain >= 500 else 3 if rain >= 350 else 2 if rain >= 200 else 1 if rain >= 80 else 0
    return max(g, w, r)


def _members(block: dict, var: str) -> np.ndarray:
    """回傳 [成員][日] 陣列（合併所有模式的系集成員）。"""
    cols = [k for k in block if k == var or k.startswith(var + "_")]
    arr = np.array([[np.nan if v is None else v for v in block[k]] for k in cols], float)
    return arr[~np.all(np.isnan(arr), axis=1)]


def _closest_approach(storm: dict) -> dict | None:
    best = None
    for p in storm["points"]:
        d = float(haversine_km(TW_CENTER[0], TW_CENTER[1], p["lat"], p["lon"]))
        if best is None or d < best["distance_km"]:
            best = {"distance_km": round(d), "time": p["time"], "forecast": p["forecast"],
                    "category": p.get("category_name", "")}
    return best


def taiwan_event(days: int = 4) -> dict:
    ids = list(COUNTIES)
    data = post_json(ENSEMBLE_URL, {
        "latitude": ",".join(str(COUNTIES[i][1]) for i in ids),
        "longitude": ",".join(str(COUNTIES[i][2]) for i in ids),
        "daily": "wind_gusts_10m_max,wind_speed_10m_max,precipitation_sum",
        "models": "ecmwf_ifs025,gfs025", "forecast_days": days, "timezone": "Asia/Taipei",
    }, ttl=3600, timeout=120)
    return build_event(data, datetime.now(TZ))


def build_event(data, now: datetime, live: bool = True) -> dict:
    """由系集資料算出各縣市災害程度與停班停課機率；live=False 時不抓官方公告（回測用）。"""
    ids = list(COUNTIES)
    if isinstance(data, dict):
        data = [data]
    dates = data[0]["daily"]["time"]
    today = now.date().isoformat()

    per_day = {d: [] for d in dates if d > today}
    members = {d: {} for d in per_day}
    for cid, loc in zip(ids, data):
        name, lat, lon, mountain = COUNTIES[cid]
        blk = loc["daily"]
        gust, wind, rain = (_members(blk, v) for v in ("wind_gusts_10m_max", "wind_speed_10m_max", "precipitation_sum"))
        for k, d in enumerate(dates):
            if d not in per_day:
                continue
            g, w, r = gust[:, k], wind[:, k], rain[:, k]
            n = min(len(g), len(w), len(r))
            g, w, r = g[:n], w[:n], r[:n]
            ok = ~(np.isnan(g) | np.isnan(w) | np.isnan(r))
            g, w, r = g[ok], w[ok], r[ok]
            if not len(g):
                continue
            members[d][cid] = {"g": g, "w": w, "r": r}
            by_wind = (w >= WIND_7) | (g >= GUST_10)
            by_rain = r >= RAIN_PLAIN
            med = lambda a: float(np.median(a))
            p90 = lambda a: float(np.percentile(a, 90))
            lvl = max(_level(med(g), med(w), med(r)), _level(p90(g), p90(w), p90(r)) - 1, 0)
            hazards = []
            if med(g) >= 62 or p90(g) >= 89:
                hazards.append("強風")
            if med(r) >= 80 or p90(r) >= 200:
                hazards.append("豪雨淹水")
                if mountain and (med(r) >= 200 or p90(r) >= 350):
                    hazards.append("山區土石流")
            per_day[d].append({
                "id": cid, "name": name, "lat": lat, "lon": lon,
                "probability": round(float(np.mean(by_wind | by_rain)), 3),
                "p_wind": round(float(np.mean(by_wind)), 3),
                "p_rain": round(float(np.mean(by_rain)), 3),
                "gust": {"median": round(med(g)), "p90": round(p90(g))},
                "wind": {"median": round(med(w)), "p90": round(p90(w))},
                "rain": {"median": round(med(r)), "p90": round(p90(r))},
                "level": int(lvl), "level_name": LEVELS[int(lvl)],
                "hazards": hazards, "members": int(len(g)),
            })

    # 颱風：路徑接近台灣 600 km 內就視為事件
    storms = []
    try:
        for s in cyclone_tracks():
            ca = _closest_approach(s)
            if ca and ca["distance_km"] <= 600:
                storms.append({"name": s["name"], "closest": ca, "source": s["source"], "link": s["link"]})
    except Exception:
        pass

    style_report = _apply_dayoff_model(per_day, members)
    review = _review(per_day, now, live)

    max_p = max((c["probability"] for cs in per_day.values() for c in cs), default=0)
    if storms:
        title, kind = f"颱風 {storms[0]['name']}", "typhoon"
    elif max_p >= 0.3:
        title, kind = "豪雨／強風", "rain"
    else:
        title, kind = "", "none"
    return {
        "active": bool(storms) or max_p >= 0.3,
        "kind": kind, "title": title, "storms": storms,
        "days": [{"date": d, "counties": sorted(cs, key=lambda c: -c["probability"])}
                 for d, cs in per_day.items() if cs],
        "criteria": CRITERIA,
        "style_model": style_report,
        "review": review,
        "notes": [
            "機率 = 82 組系集預報（ECMWF 51 + GFS 31）中達到參考標準的比例。",
            "代表點取各縣市政府附近平地；山區、沿海或離島局部可能更嚴重。",
            "全球模式格點約 25 km，強烈颱風的陣風常被低估，請同時參考中央氣象署警報。",
            "「縣市長風格」以人事行政總處歷年公告校正，屬娛樂性質；實際仍以各縣市政府公告為準。",
        ],
        "official_link": "https://www.dgpa.gov.tw/typh/daily/nds.html",
        "updated": datetime.now(TZ).isoformat(timespec="minutes"),
    }


# ---------------------------------------------------------------- 縣市長風格校正與實際結果對比
HISTORY = CACHE_DIR / "pred_history.json"
SEED_HISTORY = Path(__file__).parent / "data" / "pred_history_seed.json"  # 隨程式發佈的歷次預測（例：10/23）
LIVE_URL = "https://www.dgpa.gov.tw/typh/daily/nds.html"


def _apply_dayoff_model(per_day: dict, members: dict) -> dict | None:
    from . import dayoff_model  # 延後載入，避免循環引用
    model = dayoff_model.load()
    if model is None:
        return None
    for d, cs in per_day.items():
        if not members.get(d):
            continue
        preds = dayoff_model.predict_event(model, members[d], date.fromisoformat(d))
        for c in cs:
            pr = preds.get(c["id"])
            if pr:
                c.update({"p_weather": pr["p_weather"], "p_style": pr["p_style"], "mayor": pr["mayor"],
                          "style": pr["style"]})
    rep = model.report
    return {"cv": rep["cv"], "samples": rep["samples"], "events": rep["events"], "period": rep["period"]}


def official_status() -> dict[str, dict[str, str]]:
    """人事行政總處即時公告 → {日期: {縣市 id: full/half/partial/normal}}"""
    from .dayoff_history import classify, fetch_live, parse_snapshot
    snap = parse_snapshot(fetch_live(LIVE_URL))
    ref = date.fromisoformat(snap["ref_date"])
    id_of = {v[0].replace("台", "臺"): k for k, v in COUNTIES.items()}
    out: dict[str, dict[str, str]] = {}
    for county, lines in snap["counties"].items():
        for d, st in classify(lines, ref).items():
            if county in id_of:
                out.setdefault(d, {})[id_of[county]] = st
    return out


def _review(per_day: dict, now: datetime, live: bool) -> list[dict]:
    """把公告前（前一天 22:00 以前）最後一次預測存起來，官方公布後拿來對比。"""
    hist = json.loads(SEED_HISTORY.read_text(encoding="utf-8")) if SEED_HISTORY.exists() else {}
    if HISTORY.exists():
        hist.update(json.loads(HISTORY.read_text(encoding="utf-8")))
    for d, cs in per_day.items():
        deadline = datetime.combine(date.fromisoformat(d) - timedelta(days=1), time(22, 0), TZ)
        if now < deadline and cs:
            hist[d] = {"made_at": now.isoformat(timespec="minutes"),
                       "counties": {c["id"]: {"rule": c["probability"], "style": c.get("p_style")} for c in cs}}
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY.write_text(json.dumps(hist, ensure_ascii=False), encoding="utf-8")
    if not live:
        return []
    try:
        actual = official_status()
    except Exception:
        return []
    out = []
    for d, sts in sorted(actual.items()):
        if d not in hist:
            continue
        rows = []
        for cid, pr in hist[d]["counties"].items():
            st = sts.get(cid, "normal")
            rows.append({"id": cid, "name": COUNTIES[cid][0], "rule": pr["rule"], "style": pr.get("style"),
                         "actual": st, "off": st in ("full", "half")})
        out.append({"date": d, "made_at": hist[d]["made_at"], "counties": rows})
    return out
