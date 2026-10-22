"""全球熱帶氣旋（颱風／颶風／氣旋）即時監測。

資料來源：
- GDACS（聯合國與歐盟全球災害警報系統），涵蓋全球各海域，西太平洋颱風資料來自 JTWC
- 美國國家颶風中心 NHC（大西洋、東/中太平洋，含移動方向與速度）
"""
from datetime import datetime, timedelta, timezone

from .net import get_json
from .stations import haversine_km

GDACS_URL = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
NHC_URL = "https://www.nhc.noaa.gov/CurrentStorms.json"


def basin_name(lat: float, lon: float) -> str:
    if lat < 0:
        return "熱帶氣旋"
    if 100 <= lon <= 180:
        return "颱風"
    if 40 <= lon < 100:
        return "氣旋風暴"
    return "颶風"


def intensity_class(wind_kmh: float | None, kind: str) -> str:
    if wind_kmh is None:
        return "未知"
    ms = wind_kmh / 3.6
    if kind == "颱風":  # 中央氣象署分級
        return ("熱帶性低氣壓" if ms < 17.2 else "輕度颱風" if ms < 32.7
                else "中度颱風" if ms < 51.0 else "強烈颱風")
    # 薩菲爾-辛普森颶風等級
    for limit, name in ((63, "熱帶性低氣壓"), (119, "熱帶風暴"), (154, "1 級"), (178, "2 級"),
                        (209, "3 級（強烈）"), (252, "4 級（強烈）")):
        if wind_kmh < limit:
            return name
    return "5 級（強烈）"


def _gdacs() -> list[dict]:
    now = datetime.now(timezone.utc)
    data = get_json(GDACS_URL, {
        "eventlist": "TC",
        "fromDate": (now - timedelta(days=10)).strftime("%Y-%m-%d"),
        "toDate": now.strftime("%Y-%m-%d"),
    }, ttl=1800)
    storms = []
    for f in data.get("features", []):
        p = f["properties"]
        if str(p.get("iscurrent")).lower() != "true":
            continue
        last = datetime.fromisoformat(p["todate"]).replace(tzinfo=timezone.utc)
        if now - last > timedelta(hours=36):
            continue
        lon, lat = f["geometry"]["coordinates"][:2]
        sev = p.get("severitydata") or {}
        storms.append({
            "name": p.get("eventname", "").split("-")[0].title(),
            "lat": lat, "lon": lon,
            "max_wind_kmh": sev.get("severity"),
            "wind_note": "生命期最大風速",
            "alert_level": p.get("alertlevel"),
            "countries": p.get("country"),
            "updated": p.get("datemodified"),
            "source": f"GDACS / {p.get('source', '')}",
            "link": (p.get("url") or {}).get("report"),
        })
    return storms


def _nhc() -> list[dict]:
    data = get_json(NHC_URL, ttl=1800)
    storms = []
    for s in data.get("activeStorms", []):
        try:
            kt = float(s.get("intensity"))
        except (TypeError, ValueError):
            kt = None
        storms.append({
            "name": s.get("name", ""),
            "lat": s.get("latitudeNumeric"), "lon": s.get("longitudeNumeric"),
            "max_wind_kmh": kt * 1.852 if kt is not None else None,
            "wind_note": "目前最大持續風速",
            "pressure_hpa": s.get("pressure"),
            "movement": f"{s.get('movementDir')}° 方向，{s.get('movementSpeed')} 節",
            "updated": s.get("lastUpdate"),
            "source": "NOAA 國家颶風中心",
            "link": (s.get("publicAdvisory") or {}).get("url"),
        })
    return storms


def active_cyclones() -> tuple[list[dict], list[str]]:
    storms, errors = [], []
    for fn, label in ((_nhc, "NHC"), (_gdacs, "GDACS")):
        try:
            for s in fn():
                if s["lat"] is None or s["lon"] is None:
                    continue
                # NHC 優先（資料較即時），同名風暴不重複
                if not any(s["name"].lower() == o["name"].lower() for o in storms):
                    storms.append(s)
        except Exception as e:
            errors.append(f"{label}: {e}")
    for s in storms:
        s["kind"] = basin_name(s["lat"], s["lon"])
        s["category"] = intensity_class(s.get("max_wind_kmh"), s["kind"])
        if s["kind"] == "颶風" and s["category"] in ("熱帶性低氣壓", "熱帶風暴"):
            s["kind"] = s["category"]  # 未達颶風強度
        if s.get("wind_note") == "生命期最大風速":
            s["category"] = "生命期最強達" + s["category"]
    return storms, errors


def assess(lat: float, lon: float) -> dict:
    storms, errors = active_cyclones()
    for s in storms:
        d = float(haversine_km(lat, lon, s["lat"], s["lon"]))
        s["distance_km"] = round(d)
        s["threat_level"] = 3 if d < 300 else 2 if d < 800 else 1 if d < 1500 else 0
        s["threat"] = ["遠方，無直接影響", "注意動態", "可能受影響，請密切注意", "影響範圍內，請做好防颱準備"][s["threat_level"]]
    storms.sort(key=lambda s: s["distance_km"])
    return {"storms": storms, "errors": errors}
