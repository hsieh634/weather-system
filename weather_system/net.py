"""帶重試、磁碟快取與額度保護的 HTTP 工具。

Open-Meteo 免費版限制：每分鐘 600、每小時 5,000、每天 10,000 次（多地點或多變數的請求算多次）。
碰到 429（額度用完）時：
1. 有舊快取 → 直接用舊資料（寧可稍舊，也不要整頁壞掉）
2. 每分鐘上限 → 自動等一分鐘再試一次
3. 每小時／每天上限 → 拋出中文說明的 RateLimited 錯誤
"""
import hashlib
import json
import threading
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import CACHE_DIR, USER_AGENT

_session = requests.Session()
_session.headers["User-Agent"] = USER_AGENT
# 429 不交給自動重試（會連續狂打 API 反而更快用完額度），由下面自行處理
_retry = Retry(total=3, backoff_factor=1.0, status_forcelist=[500, 502, 503, 504], allowed_methods=None)
_session.mount("https://", HTTPAdapter(max_retries=_retry))
_wait_lock = threading.Lock()


class RateLimited(RuntimeError):
    pass


def _limit_message(reason: str) -> str:
    r = (reason or "").lower()
    if "daily" in r:
        return "Open-Meteo 免費額度今天已用完（每天 10,000 次），請明天再試，或改用已下載過的地點"
    if "hourly" in r:
        return "Open-Meteo 免費額度這一小時已用完（每小時 5,000 次），請約一小時後再試"
    return "Open-Meteo 請求太頻繁（每分鐘上限 600 次），請稍等一分鐘再試"


def _cache_path(key: str):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / (hashlib.sha1(key.encode("utf-8")).hexdigest() + ".json")


def _fetch_json(send, key: str, ttl: int):
    path = _cache_path(key)
    if ttl > 0 and path.exists() and time.time() - path.stat().st_mtime < ttl:
        return json.loads(path.read_text(encoding="utf-8"))
    for attempt in range(2):
        resp = send()
        if resp.status_code != 429:
            break
        try:
            reason = resp.json().get("reason", "")
        except ValueError:
            reason = ""
        if path.exists():  # 額度用完時退回舊資料
            return json.loads(path.read_text(encoding="utf-8"))
        if attempt == 0 and "daily" not in reason.lower() and "hourly" not in reason.lower():
            with _wait_lock:  # 每分鐘上限：所有請求一起等一分鐘
                time.sleep(61)
            continue
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
    key = url + "?" + json.dumps(params or {}, sort_keys=True, ensure_ascii=False)
    return _fetch_json(lambda: _session.get(url, params=params, timeout=timeout), key, ttl)


def post_json(url: str, data: dict, ttl: int = 1800, timeout: int = 120):
    """POST 表單並解析 JSON（多地點查詢的網址太長，需用 POST）；同樣有磁碟快取。"""
    key = "POST " + url + "?" + json.dumps(data, sort_keys=True, ensure_ascii=False)
    return _fetch_json(lambda: _session.post(url, data=data, timeout=timeout), key, ttl)


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
