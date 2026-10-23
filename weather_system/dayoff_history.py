"""行政院人事行政總處「歷次天然災害停止上班上課訊息」爬取與解析。

每筆紀錄附有當時的公告頁面快照（nds.html），列出各縣市的停班停課狀態。
整理成：日期 × 縣市 → 全縣市停班停課 / 部分鄉鎮 / 照常。

  python -m weather_system.dayoff_history      爬取並存成 cache/dayoff_records.json
"""
import html
import json
import re
import ssl
import sys
import time
from datetime import date, datetime, timedelta

import requests

try:  # 政府網站的憑證鏈需要作業系統的憑證庫才能驗證（Python 內建清單缺少中繼憑證）
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

from .config import CACHE_DIR

BASE = "https://www.dgpa.gov.tw"
LIST_URL = BASE + "/informationlist?uid=374&page={page}"
OUT = CACHE_DIR / "dayoff_records.json"
RAW = CACHE_DIR / "dgpa"
COUNTIES = ["基隆市", "臺北市", "新北市", "桃園市", "新竹市", "新竹縣", "苗栗縣", "臺中市", "彰化縣", "雲林縣",
            "南投縣", "嘉義市", "嘉義縣", "臺南市", "高雄市", "屏東縣", "宜蘭縣", "花蓮縣", "臺東縣", "澎湖縣",
            "連江縣", "金門縣"]
# 縣市合併前的舊名（2010 年以前）不在 500 筆範圍內，但保留對照以防萬一
ALIASES = {"台北市": "臺北市", "台中市": "臺中市", "台南市": "臺南市", "台東縣": "臺東縣", "台北縣": "新北市", "臺北縣": "新北市"}

class _GovTLSAdapter(requests.adapters.HTTPAdapter):
    """台灣政府憑證（GRCA）缺少 Subject Key Identifier，Python 3.13 起的嚴格模式會拒絕；
    這裡只關掉 VERIFY_X509_STRICT 這項格式檢查，憑證鏈與網域名稱仍完整驗證。"""

    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        if hasattr(ssl, "VERIFY_X509_STRICT"):
            ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


_s = requests.Session()
_s.headers["User-Agent"] = "Mozilla/5.0 (weather-system research; polite crawler)"
_s.mount(BASE, _GovTLSAdapter())


def _get(url: str, cache_name: str) -> str:
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / cache_name
    if path.exists():
        return path.read_text(encoding="utf-8")
    for attempt in range(3):
        try:
            r = _s.get(url, timeout=30)
            r.raise_for_status()
            raw = r.content
            break
        except requests.RequestException:
            time.sleep(3 * (attempt + 1))
    else:
        raise RuntimeError(f"下載失敗：{url}")
    for enc in ("utf-8", "cp950", "big5"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", "replace")
    path.write_text(text, encoding="utf-8")
    time.sleep(0.8)  # 對政府網站保持禮貌
    return text


_live_cache: dict[str, tuple[float, str]] = {}


def fetch_live(url: str, ttl: int = 600) -> str:
    """抓即時公告頁（不寫入磁碟快取，記憶體快取 10 分鐘）；自行判斷 UTF-8 / Big5 編碼。"""
    hit = _live_cache.get(url)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    raw = _s.get(url, timeout=30).content
    for enc in ("utf-8", "cp950", "big5"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", "replace")
    _live_cache[url] = (time.time(), text)
    return text


def _text(s: str) -> str:
    s = re.sub(r"<script.*?</script>|<style.*?</style>", "", s, flags=re.S)
    s = html.unescape(re.sub(r"<[^>]+>", "\n", s))
    return "\n".join(ln.strip() for ln in s.splitlines() if ln.strip())


def crawl_index(max_pages: int = 50) -> list[dict]:
    items = []
    for page in range(1, max_pages + 1):
        s = _get(LIST_URL.format(page=page), f"list_{page}.html")
        found = re.findall(r'href="/?information\?uid=374&(?:amp;)?pid=(\d+)"[^>]*>(.*?)</a>', s, re.S)
        for pid, inner in found:
            title = _text(inner).replace("\n", " ")
            m = re.search(r"(\d{2,3})年(\d{1,2})月(\d{1,2})日", title)
            if m:
                y, mo, d = map(int, m.groups())
                items.append({"pid": pid, "title": title, "date": date(y + 1911, mo, d).isoformat()})
        if not found:
            break
    uniq = {it["pid"]: it for it in items}
    return sorted(uniq.values(), key=lambda x: x["date"])


def parse_snapshot(page: str) -> dict:
    """解析 nds.html：回傳 {ref_date, counties: {縣市: [狀態文字...]}}"""
    t = _text(page)
    m = re.search(r"更新時間：\s*(\d{4})/(\d{1,2})/(\d{1,2})", t)
    ref = date(*map(int, m.groups())) if m else None
    body = t.split("資料來源：各縣市政府", 1)[-1].split("備註", 1)[0]
    body = body.split("1.若欲進入本總處網站", 1)[0]
    lines = body.splitlines()
    counties, current = {}, None
    for ln in lines:
        name = ALIASES.get(ln, ln)
        if name in COUNTIES:
            current = name
            counties.setdefault(current, [])
        elif current and ("上班" in ln or "上課" in ln):
            counties[current].append(ln)
    return {"ref_date": ref.isoformat() if ref else None, "counties": counties}


def classify(lines: list[str], ref: date) -> dict[str, str]:
    """把一個縣市的公告文字轉成 {日期: 'full'|'half'|'partial'|'normal'}。"""
    out = {}
    for ln in lines:
        for seg in re.split(r"[。；]", ln):
            seg = seg.strip()
            if not seg:
                continue
            day = ref + timedelta(days=1) if "明天" in seg else ref if "今天" in seg else None
            if day is None:
                continue
            local = re.match(r"^[^:：]{1,40}[:：]", seg)  # 「某某鄉某村：今天停止…」= 只有部分地區
            stop = "停止上班" in seg or "停止上課" in seg
            if not stop:
                status = "normal"
            elif local:
                status = "partial"
            elif "上午" in seg or "下午" in seg or "晚上" in seg or "晚間" in seg:
                status = "half"
            else:
                status = "full"
            key = day.isoformat()
            rank = {"normal": 0, "partial": 1, "half": 2, "full": 3}
            if rank[status] > rank.get(out.get(key, "normal"), 0) or key not in out:
                out[key] = status
    return out


def build(max_pages: int = 50, log=print) -> list[dict]:
    index = crawl_index(max_pages)
    log(f"共 {len(index)} 筆公告紀錄（{index[0]['date']} ~ {index[-1]['date']}）")
    by_day: dict[str, dict[str, str]] = {}
    for i, it in enumerate(index, 1):
        page = _get(f"{BASE}/information?uid=374&pid={it['pid']}", f"rec_{it['pid']}.html")
        att = re.search(r'href="(/FileConversion\?filename=[^"]+?name=nds\.html)"', page)
        if not att:
            continue
        snap = parse_snapshot(_get(BASE + html.unescape(att.group(1)), f"nds_{it['pid']}.html"))
        ref = date.fromisoformat(snap["ref_date"] or it["date"])
        for county, lines in snap["counties"].items():
            for day, status in classify(lines, ref).items():
                prev = by_day.setdefault(day, {}).get(county)
                rank = {"normal": 0, "partial": 1, "half": 2, "full": 3}
                if prev is None or rank[status] > rank[prev]:
                    by_day[day][county] = status
        if i % 25 == 0:
            log(f"  已處理 {i}/{len(index)}")
    # 有公告的日子裡，沒有列出的縣市 = 照常上班上課
    records = []
    for day in sorted(by_day):
        row = {c: by_day[day].get(c, "normal") for c in COUNTIES}
        if any(v != "normal" for v in row.values()):
            records.append({"date": day, "status": row})
    OUT.write_text(json.dumps({"built": datetime.now().isoformat(timespec="minutes"), "records": records},
                              ensure_ascii=False, indent=0), encoding="utf-8")
    log(f"完成：{len(records)} 個有停班停課的日子 → {OUT}")
    return records


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    build(int(sys.argv[1]) if len(sys.argv) > 1 else 50)
