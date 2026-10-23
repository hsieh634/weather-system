"""停班停課決策模型：用歷年實際公告校正「達標機率」，並估計各縣市長的放假風格（娛樂用途）。

資料：
- 人事行政總處歷年公告（dayoff_history.py）：哪天哪個縣市停班停課
- 同一天各縣市代表點的 ERA5 實際風雨（最大陣風、最大平均風、日雨量）

模型（邏輯斯迴歸，L2 正則化讓資料少的縣市長自動往平均靠攏）：
- 法規標準：只看是否達到風力／雨量門檻（原本系統的做法）
- 氣象校正：加入風雨強度、全台最大陣風、全台達標縣市比例（大颱風時大家一起放）
- 縣市長風格：再加入縣市與縣市長的個別傾向

用「一次颱風事件整個拿掉」的交叉驗證比較三者，數字才不會偷看答案。
ERA5 是事後的實際天氣，而縣市政府是依前一晚的預報決定，兩者有落差，這也會反映在驗證成績裡。
"""
import json
import time
from pathlib import Path
from datetime import date, timedelta

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression

from .config import MODEL_DIR
from .data_sources import ARCHIVE_URL
from .dayoff_history import COUNTIES as HIST_COUNTIES, OUT as RECORDS_PATH
from .mayors import mayor_on
from .net import NeedClientFetch, post_json
from .typhoon_event import COUNTIES as EVENT_COUNTIES

MODEL_PATH = MODEL_DIR / "dayoff_model.joblib"
OUTER = {"澎湖縣", "金門縣", "連江縣"}
TO_HIST = {v[0]: v[0].replace("台", "臺") for v in EVENT_COUNTIES.values()}   # 台北市 → 臺北市
ID_OF = {TO_HIST[v[0]]: k for k, v in EVENT_COUNTIES.items()}                 # 臺北市 → A
STYLE_LABELS = [(0.5, "很愛放假"), (0.2, "偏積極放假"), (-0.2, "大致依標準"), (-0.5, "偏謹慎"), (-9, "很少放假")]


# ---------------------------------------------------------------- 資料
def load_records() -> list[dict]:
    return json.loads(RECORDS_PATH.read_text(encoding="utf-8"))["records"]


def _ranges(days: list[date], gap: int = 2):
    days = sorted(set(days))
    start = prev = days[0]
    for d in days[1:]:
        if (d - prev).days > gap:
            yield start, prev
            start = d
        prev = d
    yield start, prev


def fetch_weather(days: list[date], log=print) -> dict:
    """各縣市代表點在指定日期的 ERA5 實際風雨 → {(日期, 縣市): (陣風, 平均風, 雨量)}"""
    names = [TO_HIST[EVENT_COUNTIES[i][0]] for i in EVENT_COUNTIES]
    lats = ",".join(str(EVENT_COUNTIES[i][1]) for i in EVENT_COUNTIES)
    lons = ",".join(str(EVENT_COUNTIES[i][2]) for i in EVENT_COUNTIES)
    out = {}
    ranges = list(_ranges(days))
    params = {"latitude": lats, "longitude": lons, "daily": "wind_gusts_10m_max,wind_speed_10m_max,precipitation_sum",
              "timezone": "Asia/Taipei", "wind_speed_unit": "kmh"}
    for k, (a, b) in enumerate(ranges, 1):
        req = {**params, "start_date": a.isoformat(), "end_date": b.isoformat()}
        for attempt in range(4):
            try:
                data = post_json(ARCHIVE_URL, req, ttl=86400 * 365, timeout=180)
                break
            except NeedClientFetch:  # 離線訓練：碰到每分鐘上限就等一分鐘再試
                log("  達到每分鐘上限，等待 65 秒…")
                time.sleep(65)
        else:
            raise RuntimeError("ERA5 資料下載多次失敗，請稍後再試")
        for name, loc in zip(names, data):
            dl = loc["daily"]
            for t, g, w, r in zip(dl["time"], dl["wind_gusts_10m_max"], dl["wind_speed_10m_max"], dl["precipitation_sum"]):
                if None not in (g, w, r):
                    out[(t, name)] = (float(g), float(w), float(r))
        if k % 20 == 0:
            log(f"  ERA5 {k}/{len(ranges)}")
    return out


def features(g, w, r, island_g, frac_crit):
    """單一縣市的氣象特徵（陣風、平均風單位 km/h，雨量 mm）。"""
    crit = (w >= 50) | (g >= 89) | (r >= 200)
    return np.column_stack([crit.astype(float), g / 100, w / 50, np.log1p(r) / np.log1p(200),
                            island_g / 100, frac_crit])


def build_dataset(log=print):
    recs = load_records()
    days = [date.fromisoformat(r["date"]) for r in recs]
    wx = fetch_weather(days, log)
    rows = []
    for r in recs:
        d = r["date"]
        vals = {c: wx.get((d, c)) for c in HIST_COUNTIES}
        if sum(v is not None for v in vals.values()) < 18:
            continue
        crit = [(v[1] >= 50 or v[0] >= 89 or v[2] >= 200) for v in vals.values() if v]
        island_g = max(v[0] for c, v in vals.items() if v and c not in OUTER)
        frac = float(np.mean(crit))
        for c, v in vals.items():
            if v is None:
                continue
            st = r["status"][c]
            rows.append({"date": d, "county": c, "mayor": mayor_on(c, date.fromisoformat(d)),
                         "g": v[0], "w": v[1], "r": v[2], "island_g": island_g, "frac": frac,
                         "y": 1 if st in ("full", "half") else 0, "status": st})
    return rows


# ---------------------------------------------------------------- 模型
class DayoffModel:
    def __init__(self):
        self.counties = list(HIST_COUNTIES)
        self.mayors: list[str] = []
        self.m_weather = self.m_style = None
        self.report: dict = {}

    def _X(self, rows_or_feats, counties, mayors, kind):
        base = rows_or_feats
        if kind == "weather":
            return base
        oc = np.array([[1.0 if c == k else 0.0 for k in self.counties] for c in counties])
        om = np.array([[1.0 if m == k else 0.0 for k in self.mayors] for m in mayors])
        return np.hstack([base, oc, om])

    @staticmethod
    def _base(rows):
        g = np.array([x["g"] for x in rows]); w = np.array([x["w"] for x in rows]); r = np.array([x["r"] for x in rows])
        return features(g, w, r, np.array([x["island_g"] for x in rows]), np.array([x["frac"] for x in rows]))

    def fit(self, rows, C=0.6):
        self.mayors = sorted({f'{x["county"]}|{x["mayor"]}' for x in rows})
        base, y = self._base(rows), np.array([x["y"] for x in rows])
        keys = [f'{x["county"]}|{x["mayor"]}' for x in rows]
        self.m_weather = LogisticRegression(C=2.0, max_iter=3000).fit(base, y)
        self.m_style = LogisticRegression(C=C, max_iter=3000).fit(
            self._X(base, [x["county"] for x in rows], keys, "style"), y)
        self.frac_mean = float(np.mean([x["frac"] for x in rows]))
        return self

    def adjust(self, p_rule, county: str, mayor_key: str, frac):
        """縣市長風格：以「預報達標機率」為基礎，加上歷史學到的縣市／縣市長放假傾向，
        以及大颱風時「全台多數縣市達標 → 大家一起放」的效應（都在對數勝算上相加）。"""
        k = len(self.counties)
        coef = self.m_style.coef_[0]
        c_off = dict(zip(self.counties, coef[6:6 + k])).get(county, 0.0)
        m_off = dict(zip(self.mayors, coef[6 + k:])).get(mayor_key, 0.0)
        p = np.clip(np.asarray(p_rule, float), 0.03, 0.97)
        z = np.log(p / (1 - p)) + c_off + m_off + coef[5] * (np.asarray(frac, float) - self.frac_mean)
        return 1 / (1 + np.exp(-z))

    def predict(self, base, county, mayor_key):
        n = len(base)
        p_w = self.m_weather.predict_proba(base)[:, 1]
        p_s = self.m_style.predict_proba(self._X(base, [county] * n, [mayor_key] * n, "style"))[:, 1]
        return p_w, p_s

    def style_offsets(self):
        """每位縣市長的放假傾向：在相同天氣下，相對於全體平均的對數勝算差。"""
        k = len(self.counties)
        coef = self.m_style.coef_[0]
        c_coef = dict(zip(self.counties, coef[6:6 + k]))
        m_coef = dict(zip(self.mayors, coef[6 + k:]))
        return {key: float(c_coef[key.split("|")[0]] + v) for key, v in m_coef.items()}


def _events(rows):
    """把連續日期的公告歸為同一事件（同一個颱風），交叉驗證時整個事件一起拿掉。"""
    days = sorted({x["date"] for x in rows})
    ev, eid, prev = {}, 0, None
    for d in days:
        cur = date.fromisoformat(d)
        if prev and (cur - prev).days > 2:
            eid += 1
        ev[d] = eid
        prev = cur
    return np.array([ev[x["date"]] for x in rows])


def _metrics(p, y):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return {"accuracy": float(np.mean((p >= 0.5) == y)), "brier": float(np.mean((p - y) ** 2)),
            "logloss": float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))),
            "recall": float(np.mean(p[y == 1] >= 0.5)) if (y == 1).any() else None,
            "false_alarm": float(np.mean(y[p >= 0.5] == 0)) if (p >= 0.5).any() else None}


def train(log=print) -> DayoffModel:
    rows = build_dataset(log)
    y = np.array([x["y"] for x in rows])
    groups = _events(rows)
    log(f"樣本 {len(rows)} 筆（{len(set(groups))} 次事件，放假 {int(y.sum())} 筆）")

    # 交叉驗證：每次拿掉一部分颱風事件，用其餘事件訓練
    uniq = np.unique(groups)
    folds = np.array_split(np.random.default_rng(0).permutation(uniq), min(10, len(uniq)))
    preds = {"rule": np.zeros(len(rows)), "weather": np.zeros(len(rows)), "style": np.zeros(len(rows))}
    base_all = DayoffModel._base(rows)
    preds["rule"] = np.where(base_all[:, 0] > 0, 0.85, 0.15)  # 歷史上只知道是否達標，給 85% / 15%
    for f in folds:
        te = np.isin(groups, f)
        tr_rows = [x for x, t in zip(rows, te) if not t]
        m = DayoffModel().fit(tr_rows)
        for i in np.where(te)[0]:
            x = rows[i]
            key = f'{x["county"]}|{x["mayor"]}'
            pw, _ = m.predict(base_all[i:i + 1], x["county"], key)
            preds["weather"][i] = pw[0]
            preds["style"][i] = m.adjust(preds["rule"][i], x["county"], key, x["frac"])
    cv = {k: _metrics(v, y) for k, v in preds.items()}
    for k, v in cv.items():
        log(f"  {k:8s} 正確率 {v['accuracy']:.1%}  Brier {v['brier']:.3f}  抓到放假 {v['recall']:.1%}")

    model = DayoffModel().fit(rows)
    offsets = model.style_offsets()
    stats = {}
    for key in model.mayors:
        county, mayor = key.split("|")
        sub = [x for x in rows if x["county"] == county and x["mayor"] == mayor]
        crit = [x for x in sub if (x["w"] >= 50 or x["g"] >= 89 or x["r"] >= 200)]
        ncrit = [x for x in sub if x not in crit]
        off = offsets[key]
        stats[key] = {
            "county": county, "mayor": mayor, "offset": round(off, 3), "odds_ratio": round(float(np.exp(off)), 2),
            "label": next(lbl for t, lbl in STYLE_LABELS if off >= t),
            "days": len(sub), "dayoffs": sum(x["y"] for x in sub),
            "met_criteria": len(crit), "dayoff_when_met": sum(x["y"] for x in crit),
            "dayoff_when_not_met": sum(x["y"] for x in ncrit), "not_met": len(ncrit),
            "first": min(x["date"] for x in sub), "last": max(x["date"] for x in sub),
        }
    model.report = {"cv": cv, "samples": len(rows), "events": int(len(set(groups))),
                    "period": [min(x["date"] for x in rows), max(x["date"] for x in rows)], "mayors": stats}
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, MODEL_PATH)
    log(f"已儲存 {MODEL_PATH}")
    return model


BUNDLED = Path(__file__).parent / "data" / "dayoff_model.joblib"   # 隨程式發佈的訓練結果（雲端主機不必重新爬資料）


def load() -> DayoffModel | None:
    for path in (MODEL_PATH, BUNDLED):
        if path.exists():
            return joblib.load(path)
    return None


def predict_event(model: DayoffModel, members: dict[str, dict], day: date) -> dict[str, dict]:
    """members：{縣市 id: {'g','w','r': 各系集成員陣列}}（成員順序在各縣市一致）
    回傳 {縣市 id: {p_weather, p_style, mayor, style}}，機率為各成員預測的平均。"""
    hist = {cid: TO_HIST[EVENT_COUNTIES[cid][0]] for cid in members}
    n = min(len(v["g"]) for v in members.values())
    g = {c: np.asarray(v["g"][:n], float) for c, v in members.items()}
    w = {c: np.asarray(v["w"][:n], float) for c, v in members.items()}
    r = {c: np.asarray(v["r"][:n], float) for c, v in members.items()}
    island = np.max([g[c] for c in members if hist[c] not in OUTER], axis=0)
    met = {c: (w[c] >= 50) | (g[c] >= 89) | (r[c] >= 200) for c in members}
    crit = np.mean(list(met.values()), axis=0)          # 每個系集成員中，全台達標縣市的比例
    out = {}
    for c in members:
        base = features(g[c], w[c], r[c], island, crit)
        mayor = mayor_on(hist[c], day)
        key = f"{hist[c]}|{mayor}"
        pw, _ = model.predict(base, hist[c], key if key in model.mayors else "")
        ps = model.adjust(float(np.mean(met[c])), hist[c], key, float(np.mean(crit)))
        out[c] = {"p_weather": round(float(np.mean(pw)), 3), "p_style": round(float(ps), 3),
                  "mayor": mayor, "style": model.report["mayors"].get(key)}
    return out


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    # 透過套件路徑呼叫，模型才會以 weather_system.dayoff_model.DayoffModel 儲存（直接執行時會存成 __main__）
    from weather_system import dayoff_model
    dayoff_model.train()
