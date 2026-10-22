"""官方氣象警報。

- 美國：國家氣象局 NWS（免金鑰）
- 台灣：中央氣象署開放資料 W-C0033-001（需自行申請免費金鑰，設定環境變數 CWA_API_KEY）
  申請網址：https://opendata.cwa.gov.tw/user/authkey
其他國家請以當地氣象機關公告為準。
"""
import os

from .config import CWA_API_KEY_ENV
from .net import get_json

NWS_URL = "https://api.weather.gov/alerts/active"
CWA_URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/W-C0033-001"

_NWS_SEVERITY = {"Extreme": 3, "Severe": 3, "Moderate": 2, "Minor": 1, "Unknown": 1}


def _nws(lat, lon) -> list[dict]:
    data = get_json(NWS_URL, {"point": f"{lat:.4f},{lon:.4f}"}, ttl=600)
    return [{
        "source": "美國國家氣象局 NWS",
        "event": p.get("event"),
        "headline": p.get("headline"),
        "level": _NWS_SEVERITY.get(p.get("severity"), 1),
        "description": (p.get("description") or "")[:800],
        "instruction": (p.get("instruction") or "")[:400],
        "effective": p.get("effective"),
        "ends": p.get("ends") or p.get("expires"),
    } for p in (f["properties"] for f in data.get("features", []))]


def _cwa(names: list[str], key: str) -> list[dict]:
    data = get_json(CWA_URL, {"Authorization": key, "format": "JSON"}, ttl=600)
    locations = data.get("records", {}).get("location", [])
    wanted = [n.replace("台", "臺") for n in names if n]
    alerts = []
    for loc in locations:
        name = loc.get("locationName", "")
        if wanted and not any(name in w or w in name for w in wanted):
            continue
        for hz in (loc.get("hazardConditions") or {}).get("hazards") or []:
            info = hz.get("info", {})
            vt = hz.get("validTime", {})
            alerts.append({
                "source": "中央氣象署",
                "event": f"{info.get('phenomena', '')}{info.get('significance', '')}",
                "headline": f"{name}：{info.get('phenomena', '')}{info.get('significance', '')}",
                "level": 3 if info.get("significance") == "警報" else 2,
                "description": "",
                "instruction": "",
                "effective": vt.get("startTime"),
                "ends": vt.get("endTime"),
            })
    return alerts


def fetch(lat: float, lon: float, country_code: str = "", admin_names: list[str] | None = None) -> dict:
    cc = (country_code or "").upper()
    try:
        if cc == "US":
            return {"alerts": _nws(lat, lon), "available": True, "note": ""}
        if cc == "TW":
            key = os.environ.get(CWA_API_KEY_ENV)
            if not key:
                return {"alerts": [], "available": False,
                        "note": f"台灣官方警報需中央氣象署金鑰：申請後設定環境變數 {CWA_API_KEY_ENV}"}
            return {"alerts": _cwa(admin_names or [], key), "available": True, "note": ""}
    except Exception as e:
        return {"alerts": [], "available": False, "note": f"警報資料取得失敗：{e}"}
    return {"alerts": [], "available": False,
            "note": "此地區尚未串接官方警報，請以當地氣象機關公告為準（系統仍會顯示自行診斷的劇烈天氣風險）"}
