"""把網頁（static/）與匯出的資料（artifact_build/data/）組成 Claude Artifact 版頁面。

Artifact 只允許從少數 CDN 載入程式庫、樣式表只能來自 Google Fonts，且不能連外部網站，
所以自己的 CSS / JS 全部內嵌進頁面，資料檔另外以 data/*.json 一起發佈。

  python export_static.py     先匯出資料
  python build_artifact.py    產生 artifact_build/index.html
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).parent
STATIC = ROOT / "static"
BUILD = ROOT / "artifact_build"
JS_ORDER = ["relay.js", "weathermap.js", "app.js", "event.js"]


def main():
    meta = json.loads((BUILD / "data" / "meta.json").read_text(encoding="utf-8"))
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    body = re.search(r"<body>(.*)</body>", html, re.S).group(1)

    # 移除本機版的樣式表與自家腳本（改為內嵌），保留允許的 CDN 程式庫
    body = re.sub(r'\s*<script src="/static/[^"]+"></script>', "", body)
    exported = meta["exported_at"].replace("T", " ")
    body = body.replace("7 個全球模式融合 · 機器學習在地修正 · 實測驗證準確率",
                        f"7 個全球模式融合 · 機器學習在地修正 · 資料更新於 {exported}")

    css = (BUILD / "vendor" / "leaflet.css").read_text(encoding="utf-8") + "\n" + (STATIC / "style.css").read_text(encoding="utf-8")
    config = {"presets": meta["presets"], "exported_at": meta["exported_at"]}
    scripts = "\n".join(f"<script>\n{(STATIC / name).read_text(encoding='utf-8')}\n</script>" for name in JS_ORDER)

    page = f"""<title>全球氣象預測系統</title>
<style>
{css}
</style>
{body.strip()}
<script>window.WX_STATIC = {json.dumps(config, ensure_ascii=False)};</script>
{scripts}
"""
    out = BUILD / "index.html"
    out.write_text(page, encoding="utf-8")
    data_files = sorted(p.name for p in (BUILD / "data").glob("*.json"))
    print(f"頁面 {len(page.encode('utf-8')) // 1024} KB，資料檔 {len(data_files)} 個：{', '.join(data_files)}")


if __name__ == "__main__":
    main()
