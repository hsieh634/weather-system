"""把訓練時在『考卷期』算出的成績整理成報表。

所有數字都是模型沒看過的資料上、用 N 天前真正發出的預報算出來的，
可以直接解讀為『這個系統在這個地點，預報 N 天後的實際準確率』。
"""
from .config import MODELS

METHOD_NAMES = {
    "best_single": "最佳單一模式",
    "weighted_blend": "多模式加權融合",
    "model_vote": "多模式投票",
    "ml_corrected": "機器學習修正",
    "system": "本系統（自動選用）",
}
VAR_NAMES = {
    "temperature_2m_max": "最高溫",
    "temperature_2m_min": "最低溫",
    "precipitation_sum": "雨量",
    "wind_speed_10m_max": "最大風速",
    "rain": "會不會下雨",
}
TRUTH_NAMES = {"station": "氣象站實測", "era5": "ERA5 再分析"}


def accuracy_by_lead(state: dict) -> dict[int, dict]:
    """每個預報天數的重點準確率（本系統）。"""
    m = state["metrics"]
    out = {}
    for lead in m["temperature_2m_max"]:
        tx = m["temperature_2m_max"][lead]["system"]
        tn = m["temperature_2m_min"][lead]["system"]
        r = m["rain"][lead]["system"]
        w = m["wind_speed_10m_max"][lead]["system"]
        out[int(lead)] = {
            "tmax_within_2": tx.get("within_2"), "tmax_mae": tx.get("mae"),
            "tmin_within_2": tn.get("within_2"), "tmin_mae": tn.get("mae"),
            "rain_accuracy": r.get("accuracy"), "rain_pod": r.get("pod"), "rain_far": r.get("far"),
            "rain_brier": r.get("brier"), "wind_mae": w.get("mae"),
        }
    return out


def report(state: dict) -> dict:
    m = state["metrics"]
    tables = {}
    for var, per_lead in m.items():
        tables[var] = {
            "name": VAR_NAMES.get(var, var),
            "truth": TRUTH_NAMES[state["truth_source"].get(var, state["truth_source"]["precipitation_sum"])],
            "selected": METHOD_NAMES[state["selected"][var]],
            "leads": {int(lead): {METHOD_NAMES[k]: v for k, v in row.items()} for lead, row in per_lead.items()},
        }
    return {
        "trained_at": state["trained_at"],
        "training_period": state["period"],
        "test_period": state["test_period"],
        "station": state["station"],
        "models": {k: MODELS[k] for k in state["models"]},
        "by_lead": accuracy_by_lead(state),
        "tables": tables,
        "explanation": [
            "考卷期資料完全沒有參與訓練，且使用 N 天前真正發出的預報，數字代表實際可期待的準確率。",
            "「±2°C 命中率」= 預報溫度與實際溫度相差 2°C 以內的天數比例。",
            "「降雨判斷正確率」= 預報有雨（機率 ≥ 50%）/無雨，與實際日雨量是否 ≥ 1 mm 相符的比例。",
            "ERA5 是約 25 km 格點的再分析資料，與單一地點的實際感受可能略有差異；"
            "附近有公開氣象站時系統會優先使用測站實測值。",
        ],
    }


def format_text(rep: dict) -> str:
    lines = [f"驗證期間：{rep['test_period'][0]} ~ {rep['test_period'][1]}（模型未看過的資料）"]
    if rep["station"]:
        s = rep["station"]
        lines.append(f"正確答案：氣象站 {s['name']}（距離 {s['distance_km']} km）")
    else:
        lines.append("正確答案：ERA5 再分析（附近無公開氣象站資料）")
    lines.append("")
    lines.append("預報天數 │ 最高溫±2°C │ 最低溫±2°C │ 降雨判斷 │ 最高溫誤差")
    for lead, a in rep["by_lead"].items():
        lines.append(f"  第 {lead} 天 │ {a['tmax_within_2']:>9.0%} │ {a['tmin_within_2']:>9.0%} │"
                     f" {a['rain_accuracy']:>7.0%} │ {a['tmax_mae']:.2f}°C")
    return "\n".join(lines)
