"""命令列工具。

  python cli.py serve                 啟動網頁介面（http://127.0.0.1:8000）
  python cli.py forecast 台北         文字版 16 天預報
  python cli.py train 台北            重新訓練該地點的修正模型
  python cli.py verify 台北           顯示該地點的實測準確率
  python cli.py tropical              目前全球颱風／颶風
  python cli.py enso                  聖嬰／反聖嬰現況
地點可以是城市名稱，或「緯度,經度」，例如 25.03,121.56
"""
import argparse
import sys

from weather_system import enso, tropical
from weather_system.correction import LocationCorrector
from weather_system.data_sources import geocode
from weather_system.forecast import build_forecast
from weather_system.verification import format_text, report

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _place(query: str) -> dict:
    results = geocode(query)
    if not results:
        sys.exit(f"找不到地點：{query}")
    p = results[0]
    print(f"地點：{p['name']} {p.get('admin1', '')} {p.get('country', '')}"
          f"（{p['latitude']:.3f}, {p['longitude']:.3f}）\n")
    return p


def cmd_forecast(args):
    p = _place(args.place)
    f = build_forecast(p["latitude"], p["longitude"], p["name"], p.get("timezone", "auto"),
                       p.get("country_code", ""), [p.get("admin1", ""), p.get("admin2", "")])
    print(" 日期        天氣              最低~最高°C   降雨機率  雨量mm  陣風km/h  預估準確  警示")
    for d in f["daily"]:
        acc = d["expected_accuracy"]
        acc_s = f"{acc['tmax_within_2']:.0%}" if acc and acc.get("tmax_within_2") is not None else "  —"
        haz = "、".join(h["title"] for h in d["hazards"][:3])
        print(f" {d['date']}  {d['text']:<14}{d['tmin']:>5.1f} ~{d['tmax']:>5.1f}   "
              f"{(d['rain_prob'] or 0):>6.0%}  {(d['precip_mm'] or 0):>6.1f}  {(d['gust_max'] or 0):>7.0f}"
              f"   {acc_s:>6}   {haz}")
    print("\n「預估準確」= 該預報天數的最高溫落在 ±2°C 內的歷史命中率")
    storms = [s for s in f["tropical"]["storms"] if s["threat_level"] > 0]
    for s in storms:
        print(f"⚠ {s['kind']} {s['name']}：距離 {s['distance_km']} km，{s['category']}，{s['threat']}")
    e = f["enso"]
    if "label" in e:
        print(f"聖嬰監測：{e['label']}（ONI {e['oni']['anomaly']:+.2f}°C，{e['trend']}）")
    if f["alerts"]["alerts"]:
        for a in f["alerts"]["alerts"]:
            print(f"官方警報：{a['headline']}")
    elif f["alerts"]["note"]:
        print(f"官方警報：{f['alerts']['note']}")


def cmd_train(args):
    p = _place(args.place)
    c = LocationCorrector(p["latitude"], p["longitude"]).train(p.get("timezone", "auto"))
    print("\n" + format_text(report(c.state)))


def cmd_verify(args):
    p = _place(args.place)
    c = LocationCorrector(p["latitude"], p["longitude"]).load_or_train(p.get("timezone", "auto"))
    print(format_text(report(c.state)))


def cmd_tropical(args):
    storms, errors = tropical.active_cyclones()
    if not storms:
        print("目前全球沒有活躍的熱帶氣旋")
    for s in storms:
        wind = f"{s['max_wind_kmh']:.0f} km/h" if s.get("max_wind_kmh") else "—"
        print(f"{s['kind']} {s['name']:<12} {s['lat']:>6.1f}, {s['lon']:>7.1f}  {s['category']}"
              f"（{s['wind_note']} {wind}）  {s['source']}")
    for e in errors:
        print(f"（資料來源錯誤：{e}）")


def cmd_enso(args):
    e = enso.status()
    print(f"目前狀態：{e['label']}，趨勢{e['trend']}")
    print(f"ONI（{e['oni']['season']} {e['oni']['year']}）：{e['oni']['anomaly']:+.2f}°C")
    if e["weekly"]:
        print(f"最新一週 Niño 3.4（{e['weekly']['week']}）：{e['weekly']['nino34_anomaly']:+.1f}°C")
    print("可能影響：")
    for i in e["impacts"]:
        print(f"  • {i}")


def cmd_serve(args):
    import uvicorn
    print(f"開啟瀏覽器前往 http://127.0.0.1:{args.port}")
    uvicorn.run("app:app", host="127.0.0.1", port=args.port)


def main():
    ap = argparse.ArgumentParser(description="全球氣象預測系統")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("forecast", cmd_forecast), ("train", cmd_train), ("verify", cmd_verify)):
        p = sub.add_parser(name)
        p.add_argument("place")
        p.set_defaults(fn=fn)
    sub.add_parser("tropical").set_defaults(fn=cmd_tropical)
    sub.add_parser("enso").set_defaults(fn=cmd_enso)
    p = sub.add_parser("serve")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(fn=cmd_serve)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
