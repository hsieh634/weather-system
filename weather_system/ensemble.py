"""多模式加權融合：過去表現越好的模式權重越高。"""
import numpy as np
import pandas as pd


def skill_weights(model_df: pd.DataFrame, truth: pd.Series, power: float = 2.0) -> dict[str, float]:
    """以各模式歷史平均絕對誤差的倒數次方作為權重（誤差越小權重越大）。"""
    weights = {}
    for m in model_df.columns:
        err = (model_df[m] - truth).abs().dropna()
        if len(err) >= 30:
            weights[m] = 1.0 / max(err.mean(), 1e-3) ** power
    total = sum(weights.values())
    return {m: w / total for m, w in weights.items()} if total else {}


def blend(model_df: pd.DataFrame, weights: dict[str, float] | None = None) -> pd.Series:
    """加權平均；某些模式缺值（超出其預報範圍）時，用剩下模式重新正規化權重。"""
    if model_df.empty:
        return pd.Series(dtype=float)
    if not weights:
        return model_df.mean(axis=1)
    cols = [m for m in model_df.columns if m in weights]
    if not cols:
        return model_df.mean(axis=1)
    vals = model_df[cols].to_numpy(dtype=float)
    w = np.array([weights[m] for m in cols])
    mask = ~np.isnan(vals)
    wsum = (mask * w).sum(axis=1)
    out = np.where(wsum > 0, np.nansum(np.nan_to_num(vals) * w, axis=1) / np.where(wsum > 0, wsum, 1), np.nan)
    return pd.Series(out, index=model_df.index)


def spread(model_df: pd.DataFrame) -> pd.Series:
    """模式間標準差：各家模式意見越分歧，預報越不確定。"""
    return model_df.std(axis=1)
