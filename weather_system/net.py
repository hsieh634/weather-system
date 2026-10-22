"""帶重試、磁碟快取與額度保護的 HTTP 工具。

Open-Meteo 免費版依「來源 IP」計算額度：每分鐘 600、每小時 5,000、每天 10,000 次。
雲端主機（如 Render 免費方案）的對外 IP 由很多用戶共用，常常一開機額度就已被別人用完。
碰到 429（額度用完）時依序：
1. 有舊快取 → 直接用舊資料
2. Open-Meteo 的請求 → 拋出 NeedClientFetch，請訪客的瀏覽器代為下載
   （瀏覽器用訪客自己的 IP 額度），下載後回傳 /api/relay 存進快取，再重新計算
3. 其他來源 → 拋出中文說明的 RateLimited 錯誤

若有 Open-Meteo 付費金鑰，設定環境變數 OPEN_METEO_API_KEY 即改走專屬額度。
"""
import hashlib
import json
import os
import threading
import time
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import CACHE_DIR, USER_AGENT

_session = requests.Session()
_session.headers["User-Agent"] = USER_AGENT
# 429 不交給自動重試（會連續狂打 API 反而更快用完額度），由下面自行處理
_retry = Retry(total=3, backoff_factor=1.0, status_forcelist=[500, 502, 503, 504], allowed_methods=None)
_session.mount("https://", HTTPAdapter(max_retries=_retry))

API_KEY = os.environ.get("OPEN_METEO_API_KEY", "").strip()
FORCE_RELAY = os.environ.get("FORCE_RELAY") == "1"   # 測試用：假裝伺服器的 Open-Meteo 額度已用完
RELAY_TTL = 900                                       # 等待瀏覽器回傳的期限（秒）
_pending: dict[str, tuple[str, int, float]] = {}      # id → (快取鍵, ttl, 期限)
_pending_lock = threading.Lock()


class RateLimited(RuntimeError):
    pass


class NeedClientFetch(Exception):
    """伺服器拿不到資料，需要瀏覽器代為下載；specs 為要下載的請求清單。"""

    def __init__(self, specs: list[dict]):
        super().__init__(f"需要瀏覽器代為下載 {len(specs)} 筆資料")
        self.specs = specs


def gather(*fns):
    """依序執行多個下載函式，把所有需要瀏覽器代抓的請求合併成一次，減少來回次數。"""
    results, specs = [], []
    for fn in fns:
        try:
            results.append(fn())
        except NeedClientFetch as e:
            specs.extend(e.specs)
            results.append(None)
    if specs:
        raise NeedClientFetch(_dedupe(specs))
    return results


def _dedupe(specs):
    seen, out = set(), []
    for s in specs:
        if s["id"] not in seen:
            seen.add(s["id"])
            out.append(s)
    return out


def _limit_message(reason: str) -> str:
    r = (reason or "").lower()
    if "daily" in r:
        return "資料來源今天的免費額度已用完，請明天再試"
    if "hourly" in r:
        return "資料來源這一小時的免費額度已用完，請約一小時後再試"
    return "資料來源請求太頻繁，請稍等一分鐘再試"


def _is_open_meteo(url: str) -> bool:
    return urlparse(url).hostname.endswith("open-meteo.com")


def _with_key(url: str, params: dict) -> tuple[str, dict]:
    """有付費金鑰時改用 customer-*.open-meteo.com 並附上 apikey。"""
    if not API_KEY or not _is_open_meteo(url):
        return url, params
    host = urlparse(url).hostname
    return url.replace(host, "customer-" + host, 1), {**params, "apikey": API_KEY}


def _cache_path(key: str):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / (hashlib.sha1(key.encode("utf-8")).hexdigest() + ".json")


def _relay_spec(method: str, url: str, params: dict, key: str, ttl: int) -> dict:
    rid = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
    with _pending_lock:
        now = time.time()
        for k in [k for k, v in _pending.items() if v[2] < now]:
            del _pending[k]
        _pending[rid] = (key, ttl, now + RELAY_TTL)
    return {"id": rid, "method": method, "url": url, "params": {k: str(v) for k, v in params.items()}}


def accept_relay(rid: str, data) -> bool:
    """收下瀏覽器代為下載的資料：只接受伺服器自己發出、還沒過期的請求。"""
    with _pending_lock:
        item = _pending.pop(rid, None)
    if not item or item[2] < time.time():
        return False
    key, ttl, _ = item
    if not isinstance(data, (dict, list)) or (isinstance(data, dict) and data.get("error")):
        return False
    blocks = data if isinstance(data, list) else [data]
    if not all(isinstance(b, dict) and ("latitude" in b or "results" in b or "generationtime_ms" in b) for b in blocks):
        return False
    _cache_path(key).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return True


def _fetch_json(method: str, url: str, params: dict, ttl: int, timeout: int):
    key = ("POST " if method == "POST" else "") + url + "?" + json.dumps(params, sort_keys=True, ensure_ascii=False)
    path = _cache_path(key)
    if ttl > 0 and path.exists() and time.time() - path.stat().st_mtime < ttl:
        return json.loads(path.read_text(encoding="utf-8"))
    real_url, real_params = _with_key(url, params)
    if FORCE_RELAY and _is_open_meteo(url) and not API_KEY:
        status, reason, resp = 429, "Daily API request limit exceeded (simulated)", None
    else:
        if method == "POST":
            resp = _session.post(real_url, data=real_params, timeout=timeout)
        else:
            resp = _session.get(real_url, params=real_params, timeout=timeout)
        status = resp.status_code
        reason = ""
        if status == 429:
            try:
                reason = resp.json().get("reason", "")
            except ValueError:
                pass
    if status == 429:
        if path.exists():  # 額度用完時退回舊資料
            return json.loads(path.read_text(encoding="utf-8"))
        if _is_open_meteo(url) and not API_KEY:
            raise NeedClientFetch([_relay_spec(method, url, params, key, ttl)])
        raise RateLimited(_limit_message(reason))
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(f"API 錯誤：{data.get('reason')}")
    if ttl > 0:
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def get_json(url: str, params: dict | None = None, ttl: int = 1800, timeout: int = 60):
    """GET 並解析 JSON；ttl 秒內重複請求直接讀快取。"""
    return _fetch_json("GET", url, params or {}, ttl, timeout)


def post_json(url: str, data: dict, ttl: int = 1800, timeout: int = 120):
    """POST 表單並解析 JSON（多地點查詢的網址太長，需用 POST）；同樣有磁碟快取。"""
    return _fetch_json("POST", url, data, ttl, timeout)


def get_text(url: str, ttl: int = 21600, timeout: int = 60) -> str:
    path = _cache_path(url)
    if ttl > 0 and path.exists() and time.time() - path.stat().st_mtime < ttl:
        return json.loads(path.read_text(encoding="utf-8"))
    try:
        resp = _session.get(url, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        raise
    if ttl > 0:
        path.write_text(json.dumps(resp.text), encoding="utf-8")
    return resp.text
