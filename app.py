"""網頁伺服器：python cli.py serve 後打開 http://127.0.0.1:8000"""
import os
import threading
from collections import defaultdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from weather_system import enso, maps, tropical, typhoon_event
from weather_system.correction import LocationCorrector
from weather_system.data_sources import geocode
from weather_system.forecast import build_forecast
from weather_system.verification import report

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="全球氣象預測系統")
app.add_middleware(GZipMiddleware, minimum_size=2000)
_locks: dict[tuple, threading.Lock] = defaultdict(threading.Lock)  # 同一地點同時只訓練一次


def _log(msg):
    print(msg, flush=True)


def _key(lat, lon):
    return round(lat, 2), round(lon, 2)


@app.get("/api/geocode")
def api_geocode(q: str = Query(..., min_length=1)):
    try:
        return geocode(q)
    except Exception as e:
        raise HTTPException(502, f"地點搜尋失敗：{e}")


@app.get("/api/forecast")
def api_forecast(lat: float, lon: float, name: str = "", tz: str = "auto",
                 cc: str = "", admin: str = ""):
    with _locks[_key(lat, lon)]:
        try:
            return build_forecast(lat, lon, name, tz, cc, [a for a in admin.split("|") if a], log=_log)
        except Exception as e:
            raise HTTPException(502, f"預報產生失敗：{e}")


@app.get("/api/health")
def api_health():
    """雲端平台的健康檢查（不呼叫任何外部 API）。"""
    return {"ok": True}


@app.post("/api/retrain")
def api_retrain(lat: float, lon: float, tz: str = "auto"):
    # 公開網站上任何人都能呼叫，會大量消耗 API 額度，因此預設關閉
    if os.environ.get("ALLOW_RETRAIN") != "1":
        raise HTTPException(403, "此網站未開放手動重新訓練（模型每 30 天會自動更新）")
    with _locks[_key(lat, lon)]:
        c = LocationCorrector(lat, lon).train(tz, log=_log)
    return report(c.state)


@app.get("/api/map/grid")
def api_map_grid(lat: float, lon: float):
    try:
        return maps.grid_forecast(lat, lon)
    except Exception as e:
        raise HTTPException(502, f"地圖資料取得失敗：{e}")


@app.get("/api/map/cyclones")
def api_map_cyclones():
    try:
        return maps.cyclone_tracks()
    except Exception as e:
        raise HTTPException(502, f"颱風路徑取得失敗：{e}")


@app.get("/api/event/taiwan")
def api_event_taiwan():
    try:
        return typhoon_event.taiwan_event()
    except Exception as e:
        raise HTTPException(502, f"颱風事件資料取得失敗：{e}")


@app.get("/api/tropical")
def api_tropical(lat: float = 25.05, lon: float = 121.53):
    return tropical.assess(lat, lon)


@app.get("/api/enso")
def api_enso():
    return enso.status()


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
