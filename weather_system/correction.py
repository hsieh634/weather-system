"""機器學習在地化修正（Model Output Statistics）與誠實驗證。

做法：
1. 拿過去約兩年『各模式在前一天真正發出的預報』對照『實際發生的天氣』
   （附近有氣象站用測站實測，否則用 ERA5 再分析），
   學出這個地點各模式的系統性偏差，再套用到今天的預報。
2. 時間切成三段，模型永遠看不到它被考的資料：
     [ 訓練 A 60% | 選方法 B 20% | 考卷 C 20% ]
   在 B 段決定每個變數該用「最佳單一模式 / 加權融合 / 機器學習」哪一種，
   在 C 段對第 1~7 天的預報計分，得到每一天的真實準確率。
3. 正式使用的模型最後用全部資料重新訓練。
"""
import threading
from datetime import datetime, timezone

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor

from .config import (
    DAILY_TARGETS, MODEL_DIR, MODEL_MAX_AGE_DAYS, OBS_LAG_DAYS, RAIN_THRESHOLD_MM, TRAIN_DAYS,
    VERIFY_LEADS,
)
from .data_sources import fetch_observations, fetch_previous_runs_daily, training_window
from .ensemble import blend, skill_weights
from .net import gather
from .stations import find_station_obs

VERSION = 2
LOG_VARS = {"precipitation_sum"}   # 雨量偏態大，在 log 空間學習
REG_METHODS = ("best_single", "weighted_blend", "ml_corrected")
RAIN_METHODS = ("best_single", "model_vote", "ml_corrected")
# 同一時間只訓練一個地點：免費主機記憶體小，且訓練會大量使用 API 額度
_TRAIN_LOCK = threading.Lock()


def _regressor():
    return HistGradientBoostingRegressor(
        max_iter=300, learning_rate=0.04, max_leaf_nodes=15, min_samples_leaf=20,
        l2_regularization=1.0, random_state=0)


def _classifier():
    return HistGradientBoostingClassifier(
        max_iter=300, learning_rate=0.04, max_leaf_nodes=15, min_samples_leaf=20,
        l2_regularization=1.0, random_state=0)


def _to_space(var, v):
    return np.log1p(np.clip(v, 0, None)) if var in LOG_VARS else v


def _from_space(var, v):
    return np.clip(np.expm1(v), 0, None) if var in LOG_VARS else v


def build_features(model_df: pd.DataFrame, models: list[str], var: str,
                   blended: pd.Series) -> pd.DataFrame:
    X = model_df.reindex(columns=models).astype(float)
    row_mean = X.mean(axis=1)
    X = X.where(X.notna(), row_mean, axis=0)  # 超出某模式預報範圍時以其他模式平均補上
    feats = X.add_prefix("m:")
    feats["blend"] = blended
    feats["std"] = X.std(axis=1)
    feats["range"] = X.max(axis=1) - X.min(axis=1)
    doy = model_df.index.dayofyear.to_numpy()
    feats["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
    feats["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)
    if var == "precipitation_sum":
        feats["frac_rain"] = (X >= RAIN_THRESHOLD_MM).mean(axis=1)
        feats["frac_heavy"] = (X >= 10).mean(axis=1)
    return feats


# ---------------------------------------------------------------- 擬合 / 預測（單一變數）
def _fit(df: pd.DataFrame, y: pd.Series, var: str, models: list[str]) -> dict:
    w = skill_weights(df, y)
    b = blend(df, w)
    X = build_features(df, models, var, b)
    fit = {
        "weights": w,
        "best": min(models, key=lambda m: (df[m] - y).abs().mean()),
        "reg": _regressor().fit(X, _to_space(var, y) - _to_space(var, b)),
    }
    if var == "precipitation_sum":
        fit["clf"] = _classifier().fit(X, (y >= RAIN_THRESHOLD_MM).astype(int))
    return fit


def _predict(fit: dict, df: pd.DataFrame, var: str, models: list[str]) -> pd.DataFrame:
    df = df.reindex(columns=models)
    b = blend(df, fit["weights"])
    X = build_features(df, models, var, b)
    ok = b.notna().to_numpy()
    out = pd.DataFrame(index=df.index)
    out["best_single"] = df[fit["best"]].fillna(b)
    out["weighted_blend"] = b
    ml = np.full(len(df), np.nan)
    if ok.any():
        ml[ok] = _from_space(var, _to_space(var, b.to_numpy()[ok]) + fit["reg"].predict(X[ok]))
    out["ml_corrected"] = ml
    if var == "precipitation_sum":
        out["p_best_single"] = (out["best_single"] >= RAIN_THRESHOLD_MM).astype(float).where(ok)
        out["p_model_vote"] = (df >= RAIN_THRESHOLD_MM).sum(axis=1) / df.notna().sum(axis=1)
        p = np.full(len(df), np.nan)
        if ok.any():
            p[ok] = fit["clf"].predict_proba(X[ok])[:, 1]
        out["p_ml_corrected"] = p
    return out


# ---------------------------------------------------------------- 評分
def reg_metrics(var: str, pred, truth) -> dict:
    pred, truth = np.asarray(pred, float), np.asarray(truth, float)
    m = ~np.isnan(pred) & ~np.isnan(truth)
    err = pred[m] - truth[m]
    if not len(err):
        return {}
    out = {"n": int(len(err)), "mae": float(np.mean(np.abs(err))),
           "rmse": float(np.sqrt(np.mean(err ** 2))), "bias": float(np.mean(err))}
    if var.startswith("temperature"):
        out["within_1"] = float(np.mean(np.abs(err) <= 1.0))
        out["within_2"] = float(np.mean(np.abs(err) <= 2.0))
        out["within_3"] = float(np.mean(np.abs(err) <= 3.0))
    elif var.startswith("wind"):
        out["within_5"] = float(np.mean(np.abs(err) <= 5.0))
        out["within_10"] = float(np.mean(np.abs(err) <= 10.0))
    else:
        out["within_5"] = float(np.mean(np.abs(err) <= 5.0))
    return out


def rain_metrics(prob, truth_mm) -> dict:
    prob, truth_mm = np.asarray(prob, float), np.asarray(truth_mm, float)
    m = ~np.isnan(prob) & ~np.isnan(truth_mm)
    prob, obs = prob[m], truth_mm[m] >= RAIN_THRESHOLD_MM
    if not len(prob):
        return {}
    yes = prob >= 0.5
    hits, misses = np.sum(yes & obs), np.sum(~yes & obs)
    false_alarms = np.sum(yes & ~obs)
    return {
        "n": int(len(prob)),
        "accuracy": float(np.mean(yes == obs)),                     # 有雨/無雨判斷正確率
        "pod": float(hits / max(hits + misses, 1)),                # 真的下雨時有報到的比例
        "far": float(false_alarms / max(hits + false_alarms, 1)),  # 報有雨卻沒下的比例
        "brier": float(np.mean((prob - obs) ** 2)),                # 機率預報誤差（越小越好）
        "base_rate": float(np.mean(obs)),                          # 這段期間的下雨天比例
    }


# ---------------------------------------------------------------- 主類別
class LocationCorrector:
    def __init__(self, lat: float, lon: float):
        self.lat, self.lon = round(lat, 2), round(lon, 2)
        self.path = MODEL_DIR / f"mos_{self.lat:+.2f}_{self.lon:+.2f}.joblib"
        self.state: dict | None = None

    def load_or_train(self, tz: str = "auto", force: bool = False, log=print) -> "LocationCorrector":
        if not force and self.path.exists():
            state = joblib.load(self.path)
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(state["trained_at"])).days
            if state.get("version") == VERSION and age <= MODEL_MAX_AGE_DAYS:
                self.state = state
                return self
        return self.train(tz, log=log)

    def train(self, tz: str = "auto", log=print) -> "LocationCorrector":
        with _TRAIN_LOCK:
            # 排隊期間別人可能已經訓練好同一個地點
            if self.path.exists():
                state = joblib.load(self.path)
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(state["trained_at"])).total_seconds()
                if state.get("version") == VERSION and age < 600:
                    self.state = state
                    return self
            return self._train(tz, log)

    def _train(self, tz: str, log) -> "LocationCorrector":
        start, end = training_window(TRAIN_DAYS, OBS_LAG_DAYS)
        log(f"下載 {start} ~ {end} 各模式前一天的預報與實際觀測…")
        fc, obs = gather(lambda: fetch_previous_runs_daily(self.lat, self.lon, start, end, lead=1, tz=tz),
                         lambda: fetch_observations(self.lat, self.lon, start, end, tz))
        truth_source = {v: "era5" for v in DAILY_TARGETS}
        station, st_obs = find_station_obs(self.lat, self.lon, start, end, log=log)
        if station:
            for var in st_obs.columns:
                s = st_obs[var].reindex(obs.index)
                # 測站該項目資料要夠完整（且最近半年也有資料）才採用，否則該項目維持 ERA5
                recent = s.iloc[-len(s) // 4:]
                if s.notna().mean() >= 0.7 and recent.notna().mean() >= 0.7:
                    obs[var] = s
                    truth_source[var] = "station"
                else:
                    log(f"  測站 {var} 資料不足，此項改用 ERA5")
        else:
            log("附近沒有公開的氣象站資料，改用 ERA5 再分析作為正確答案")

        models = None
        for var in DAILY_TARGETS:
            ok = set(fc[var].columns[fc[var].notna().mean() >= 0.7])
            models = ok if models is None else models & ok
        models = sorted(models or [])
        if len(models) < 2:
            raise RuntimeError("這個地點可用的歷史模式資料不足，無法訓練修正模型")
        log(f"使用模式：{', '.join(models)}")

        # 共同的時間切分（所有變數用同一組日期，方便比較）
        valid = pd.Series(True, index=fc[DAILY_TARGETS[0]].index)
        for var in DAILY_TARGETS:
            valid &= fc[var][models].notna().sum(axis=1).ge(2)
        dates = valid[valid].index
        n = len(dates)
        a_end, b_end = int(n * 0.6), int(n * 0.8)
        d_a, d_b, d_c = dates[:a_end], dates[a_end:b_end], dates[b_end:]
        log(f"訓練 {len(d_a)} 天、選方法 {len(d_b)} 天、考卷 {len(d_c)} 天"
            f"（{d_c[0].date()} ~ {d_c[-1].date()}）")

        # 考卷期另外下載第 2~7 天前的預報
        other = [lead for lead in VERIFY_LEADS if lead != 1]
        fetched = gather(*[(lambda L=lead: fetch_previous_runs_daily(
            self.lat, self.lon, d_c[0].date(), d_c[-1].date(), lead=L, tz=tz)) for lead in other])
        leads_fc = {1: fc, **dict(zip(other, fetched))}

        state = {"version": VERSION, "trained_at": datetime.now(timezone.utc).isoformat(),
                 "lat": self.lat, "lon": self.lon, "models": models,
                 "period": [start.isoformat(), end.isoformat()],
                 "test_period": [str(d_c[0].date()), str(d_c[-1].date())],
                 "station": station, "truth_source": truth_source,
                 "fits": {}, "selected": {}, "metrics": {}}

        for var in DAILY_TARGETS:
            y = obs[var]
            df = fc[var][models]

            def subset(d):
                d = d[y.reindex(d).notna().to_numpy()]
                return df.loc[d], y.loc[d]

            # --- 在 B 段決定用哪種方法
            fit_a = _fit(*subset(d_a), var, models)
            df_b, y_b = subset(d_b)
            pred_b = _predict(fit_a, df_b, var, models)
            scores = {m: reg_metrics(var, pred_b[m], y_b)["mae"] for m in REG_METHODS}
            selected = min(scores, key=scores.get)
            if var == "precipitation_sum":
                rscores = {m: rain_metrics(pred_b["p_" + m], y_b)["brier"] for m in RAIN_METHODS}
                state["selected"]["rain"] = min(rscores, key=rscores.get)
            state["selected"][var] = selected

            # --- 用 A+B 訓練，在 C 段對第 1~7 天計分
            fit_ab = _fit(*subset(d_a.append(d_b)), var, models)
            per_lead = {}
            rain_lead = {}
            for lead, lfc in leads_fc.items():
                ldf = lfc[var].reindex(index=d_c, columns=models)
                yc = y.reindex(d_c)
                pred = _predict(fit_ab, ldf, var, models)
                row = {m: reg_metrics(var, pred[m], yc) for m in REG_METHODS}
                row["system"] = row[selected]
                per_lead[lead] = row
                if var == "precipitation_sum":
                    r = {m: rain_metrics(pred["p_" + m], yc) for m in RAIN_METHODS}
                    r["system"] = r[state["selected"]["rain"]]
                    rain_lead[lead] = r
            state["metrics"][var] = per_lead
            if rain_lead:
                state["metrics"]["rain"] = rain_lead

            # --- 正式使用：全部資料重新訓練
            state["fits"][var] = _fit(*subset(dates), var, models)

            d1 = per_lead[1]
            log(f"  {var}: 明天預報 MAE 最佳單一 {d1['best_single']['mae']:.2f} / "
                f"加權 {d1['weighted_blend']['mae']:.2f} / 機器學習 {d1['ml_corrected']['mae']:.2f}"
                f" → 採用 {selected}")

        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        joblib.dump(state, self.path)
        self.state = state
        return self

    # ------------------------------------------------ 套用到即時預報
    def correct(self, daily: dict[str, pd.DataFrame]) -> pd.DataFrame:
        s = self.state
        out = {}
        for var in DAILY_TARGETS:
            pred = _predict(s["fits"][var], daily[var], var, s["models"])
            out[var] = pred[s["selected"][var]]
            for m in REG_METHODS:
                out[f"{var}__{m}"] = pred[m]
            if var == "precipitation_sum":
                out["rain_prob"] = pred["p_" + s["selected"]["rain"]]
        return pd.DataFrame(out)

    @property
    def weights(self) -> dict:
        return {v: f["weights"] for v, f in self.state["fits"].items()}
