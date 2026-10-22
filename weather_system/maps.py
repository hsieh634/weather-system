"""天氣地圖資料：區域格點預報（動畫用）與颱風／颶風路徑。

格點：Open-Meteo 支援一次查詢多個座標，取 13×13 個點、未來 7 天每 3 小時，
前端再用雙線性內插畫成連續的色彩場與風場粒子動畫。
免費 API 每個座標算一次呼叫（每日上限約 10,000），因此區域會對齊到固定網格並快取 6 小時，
平移地圖時盡量重複使用已下載的區域。
"""
import re
from datetime import datetime, timezone

import numpy as np

from .net import get_json, post_json

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GDACS_LIST_URL = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
GDACS_GEOM_URL = "https://www.gdacs.org/gdacsapi/api/polygons/getgeometry"

GRID_N = 13                 # 每邊格點數（13×13 = 169 次查詢，兼顧細緻度與免費額度）
SPAN_LAT, SPAN_LON = 24.0, 30.0
SNAP = 6.0                  # 區域中心對齊間距（度），讓鄰近查詢共用快取
GRID_VARS = ["temperature_2m", "precipitation", "wind_speed_10m", "wind_direction_10m",
             "wind_gusts_10m", "pressure_msl", "cloud_cover", "relative_humidity_2m"]
OUT_NAMES = {"temperature_2m": "temp", "precipitation": "precip", "wind_gusts_10m": "gust",
             "pressure_msl": "pressure", "cloud_cover": "cloud", "relative_humidity_2m": "humidity"}


def _snap(v, step):
    return round(v / step) * step


def grid_forecast(lat: float, lon: float, days: int = 7) -> dict:
    clat = float(np.clip(_snap(lat, SNAP), -78 + SPAN_LAT / 2, 78 - SPAN_LAT / 2))
    clon = _snap(lon, SNAP)
    lats = np.round(np.linspace(clat - SPAN_LAT / 2, clat + SPAN_LAT / 2, GRID_N), 3)
    lons = np.round(np.linspace(clon - SPAN_LON / 2, clon + SPAN_LON / 2, GRID_N), 3)
    LA, LO = np.meshgrid(lats, lons, indexing="ij")
    wrapped = ((LO + 180) % 360) - 180
    data = post_json(FORECAST_URL, {
        "latitude": ",".join(map(str, LA.ravel())),
        "longitude": ",".join(map(str, np.round(wrapped.ravel(), 3))),
        "hourly": ",".join(GRID_VARS), "forecast_days": days,
        "temporal_resolution": "hourly_3", "timezone": "GMT", "wind_speed_unit": "kmh",
    }, ttl=6 * 3600, timeout=180)
    if isinstance(data, dict):
        data = [data]
    times = data[0]["hourly"]["time"]
    T = len(times)

    def cube(var):
        arr = np.array([[np.nan if v is None else v for v in p["hourly"][var]] for p in data], float)
        return arr.T.reshape(T, GRID_N, GRID_N)  # [時間][緯度][經度]

    speed = cube("wind_speed_10m") / 3.6               # m/s
    rad = np.radians(cube("wind_direction_10m"))
    fields = {OUT_NAMES[v]: cube(v) for v in OUT_NAMES}
    fields["wind"] = speed * 3.6
    fields["u"] = -speed * np.sin(rad)
    fields["v"] = -speed * np.cos(rad)

    def pack(a, digits=1):
        a = np.round(a, digits)
        return [[None if np.isnan(x) else float(x) for x in frame.ravel()] for frame in a]

    return {
        "lats": lats.tolist(), "lons": lons.tolist(),
        "times": [t + "Z" for t in times],
        "fields": {k: pack(v, 2 if k in ("u", "v") else 1) for k, v in fields.items()},
        "source": "Open-Meteo（各地最佳模式）",
    }


# ---------------------------------------------------------------- 颱風路徑
_CAT = {"TD": "熱帶性低氣壓", "TS": "熱帶風暴", "HU": "颱風／颶風"}
# GDACS 暴風範圍多邊形：綠 = 60 km/h（約 7 級風）、橘 = 90 km/h（約 10 級風）、紅 = 120 km/h
_RADII = {"Poly_Green": "r60", "Poly_Orange": "r90", "Poly_Red": "r120"}
_FALLBACK_R60 = {"TD": 120, "TS": 220, "HU": 320}


def _attach_radii(points: list[dict], wind_polys: list) -> None:
    """把各時間的暴風半徑（km）接到路徑點上；沒有多邊形的時間點用前後內插。"""
    from .stations import haversine_km
    known = {"r60": {}, "r90": {}, "r120": {}}
    for key, t, ring in wind_polys:
        center = min(points, key=lambda q: abs((q["time"] - t).total_seconds()))
        if abs((center["time"] - t).total_seconds()) > 3 * 3600:
            continue
        d = haversine_km(center["lat"], center["lon"], ring[:, 1], ring[:, 0])
        known[key][t] = float(np.median(d))
    for key, table in known.items():
        ts = sorted(table)
        for pt in points:
            if ts:
                x = [q.timestamp() for q in ts]
                pt[key] = round(float(np.interp(pt["time"].timestamp(), x, [table[q] for q in ts])))
            else:
                pt[key] = 0
    for pt in points:
        if not pt["r60"]:  # 沒有範圍資料時依強度給概略值
            pt["r60"] = _FALLBACK_R60.get(pt.get("category"), 150)


def _parse_time(label: str):
    m = re.match(r"(\d{2})/(\d{2}) (\d{2}):(\d{2})", label or "")
    if not m:
        return None
    d, mo, h, mi = map(int, m.groups())
    now = datetime.now(timezone.utc)
    year = now.year - (1 if mo == 12 and now.month == 1 else 0)
    return datetime(year, mo, d, h, mi, tzinfo=timezone.utc)


def cyclone_tracks() -> list[dict]:
    now = datetime.now(timezone.utc)
    events = get_json(GDACS_LIST_URL, {"eventlist": "TC",
                                       "fromDate": (now.replace(day=1) if now.day > 10 else now).strftime("%Y-%m-01"),
                                       "toDate": now.strftime("%Y-%m-%d")}, ttl=1800)
    out = []
    for f in events.get("features", []):
        p = f["properties"]
        if str(p.get("iscurrent")).lower() != "true":
            continue
        last_obs = datetime.fromisoformat(p["todate"]).replace(tzinfo=timezone.utc)
        if (now - last_obs).total_seconds() > 36 * 3600:
            continue
        try:
            geo = get_json(GDACS_GEOM_URL, {"eventtype": "TC", "eventid": p["eventid"],
                                            "episodeid": p["episodeid"]}, ttl=1800)
        except Exception:
            continue
        points, segments, cone, wind_polys = [], [], None, []
        for g in geo.get("features", []):
            cls, gp = g["properties"].get("Class", ""), g["properties"]
            if cls.startswith("Point_Polygon_Point"):
                ring = np.array(g["geometry"]["coordinates"][0])
                t = _parse_time(gp.get("polygonlabel"))
                if t is not None:
                    points.append({"time": t, "lon": float(ring[:, 0].mean()), "lat": float(ring[:, 1].mean())})
            elif cls.startswith("Line_Line"):
                segments.append((gp.get("polygonlabel"), g["geometry"]["coordinates"]))
            elif cls in _RADII:
                t = _parse_time(gp.get("polygonlabel"))
                geom = g["geometry"]
                ring = geom["coordinates"][0] if geom["type"] == "Polygon" else geom["coordinates"][0][0]
                if t is not None and len(ring) > 3:
                    wind_polys.append((_RADII[cls], t, np.array(ring)))
            elif cls == "Poly_Cones":
                geom = g["geometry"]
                cone = geom["coordinates"][0] if geom["type"] == "Polygon" else geom["coordinates"][0][0]
        if len(points) < 2:
            continue
        points.sort(key=lambda x: x["time"])
        # 每個點的強度分類：取從該點出發（或抵達該點）的線段
        for pt in points:
            best, cat = 1e9, None
            for label, coords in segments:
                for c in coords:
                    d = (c[0] - pt["lon"]) ** 2 + (c[1] - pt["lat"]) ** 2
                    if d < best:
                        best, cat = d, label
            pt["category"] = cat
            pt["category_name"] = _CAT.get(cat, cat or "")
            pt["forecast"] = pt["time"] > last_obs
        _attach_radii(points, wind_polys)
        for pt in points:
            pt["time"] = pt["time"].isoformat().replace("+00:00", "Z")
        sev = p.get("severitydata") or {}
        out.append({
            "name": p.get("eventname", "").split("-")[0].title(),
            "source": f"GDACS / {p.get('source', '')}",
            "max_wind_kmh": sev.get("severity"),
            "last_observed": last_obs.isoformat().replace("+00:00", "Z"),
            "points": points,
            "cone": cone,
            "link": (p.get("url") or {}).get("report"),
        })
    return out
