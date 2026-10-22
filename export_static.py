"""把系統的計算結果匯出成靜態資料，供 Claude Artifact 版本使用。

Artifact 頁面無法連外部網站，所以預報、地圖格點、颱風路徑、停班停課機率
都在這台電腦先算好，存成 artifact_build/data/*.json，再由 build_artifact.py 組成頁面。

  python export_static.py            匯出全部預設地點
  python export_static.py 台北 台中   只更新指定地點（其他地點沿用上次的檔案）
"""
import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from weather_system import maps, typhoon_event
from weather_system.forecast import build_forecast

OUT = Path(__file__).parent / "artifact_build" / "data"
VENDOR = Path(__file__).parent / "artifact_build" / "vendor"

# 預設地點：台灣主要城市 + 兩個國外驗證範例（有真實氣象站）
PRESETS = [
    {"id": "taipei", "name": "台北", "latitude": 25.0531, "longitude": 121.5264, "timezone": "Asia/Taipei", "country_code": "TW", "admin1": "臺北市", "country": "台灣"},
    {"id": "taichung", "name": "台中", "latitude": 24.1469, "longitude": 120.6839, "timezone": "Asia/Taipei", "country_code": "TW", "admin1": "臺中市", "country": "台灣"},
    {"id": "tainan", "name": "台南", "latitude": 22.99, "longitude": 120.21, "timezone": "Asia/Taipei", "country_code": "TW", "admin1": "臺南市", "country": "台灣"},
    {"id": "kaohsiung", "name": "高雄", "latitude": 22.6163, "longitude": 120.3133, "timezone": "Asia/Taipei", "country_code": "TW", "admin1": "高雄市", "country": "台灣"},
    {"id": "hualien", "name": "花蓮", "latitude": 23.9769, "longitude": 121.6044, "timezone": "Asia/Taipei", "country_code": "TW", "admin1": "花蓮縣", "country": "台灣"},
    {"id": "taitung", "name": "台東", "latitude": 22.7583, "longitude": 121.1444, "timezone": "Asia/Taipei", "country_code": "TW", "admin1": "臺東縣", "country": "台灣"},
    {"id": "sapporo", "name": "札幌", "latitude": 43.06667, "longitude": 141.35, "timezone": "Asia/Tokyo", "country_code": "JP", "admin1": "北海道", "country": "日本"},
    {"id": "okc", "name": "奧克拉荷馬市", "latitude": 35.4676, "longitude": -97.5164, "timezone": "America/Chicago", "country_code": "US", "admin1": "奧克拉荷馬州", "country": "美國"},
]
MAP_CENTER = (23.7, 121.0)  # 天氣地圖以台灣為中心


def _write(name: str, obj) -> int:
    path = OUT / name
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    path.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))


def main(only: list[str]):
    OUT.mkdir(parents=True, exist_ok=True)
    tw = timezone(timedelta(hours=8))
    meta_path = OUT / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {"locations": {}}

    for p in PRESETS:
        if only and p["name"] not in only and p["id"] not in only:
            continue
        print(f"▶ {p['name']}…", flush=True)
        try:
            f = build_forecast(p["latitude"], p["longitude"], p["name"], p["timezone"], p["country_code"],
                               [p["admin1"]], log=lambda m: print("   ", m, flush=True))
        except Exception as e:  # 單一地點失敗不影響其他地點
            print(f"   ✗ 失敗：{e}", flush=True)
            continue
        size = _write(f"forecast-{p['id']}.json", f)
        meta["locations"][p["id"]] = f["generated_at"]
        print(f"   ✓ {size // 1024} KB", flush=True)

    if not only:
        print("▶ 天氣地圖格點…", flush=True)
        print(f"   ✓ {_write('grid.json', maps.grid_forecast(*MAP_CENTER)) // 1024} KB", flush=True)
        print("▶ 颱風路徑…", flush=True)
        print(f"   ✓ {_write('cyclones.json', maps.cyclone_tracks()) // 1024} KB", flush=True)
        print("▶ 颱風事件與停班停課機率…", flush=True)
        print(f"   ✓ {_write('event.json', typhoon_event.taiwan_event()) // 1024} KB", flush=True)
        for name in ("world-50m.json", "taiwan-counties.json"):
            shutil.copy(VENDOR / name, OUT / name)

    meta["presets"] = [{k: v for k, v in p.items()} for p in PRESETS
                       if (OUT / f"forecast-{p['id']}.json").exists()]
    meta["exported_at"] = datetime.now(tw).isoformat(timespec="minutes")
    _write("meta.json", meta)
    print(f"完成：{len(meta['presets'])} 個地點，匯出時間 {meta['exported_at']}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main(sys.argv[1:])
