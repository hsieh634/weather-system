"""聖嬰／反聖嬰（ENSO）監測：NOAA 氣候預測中心 Niño 3.4 指數。"""
import re

from .net import get_text

ONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"
WEEKLY_URL = "https://www.cpc.ncep.noaa.gov/data/indices/wksst9120.for"


def _phase(anom: float) -> tuple[str, str]:
    if anom >= 0.5:
        strength = "非常強" if anom >= 2.0 else "強" if anom >= 1.5 else "中等" if anom >= 1.0 else "弱"
        return "el_nino", f"{strength}聖嬰（El Niño）"
    if anom <= -0.5:
        a = -anom
        strength = "強" if a >= 1.5 else "中等" if a >= 1.0 else "弱"
        return "la_nina", f"{strength}反聖嬰（La Niña）"
    return "neutral", "中性（無聖嬰／反聖嬰）"


IMPACTS = {
    "el_nino": [
        "台灣：冬末至春季降雨常偏多；颱風生成位置偏東南、強颱比例可能增加",
        "東南亞、澳洲東部、印度：偏乾，乾旱與森林火災風險上升",
        "秘魯、厄瓜多沿岸：降雨偏多、洪水風險上升",
        "美國南部冬季偏濕；北美北部冬季偏暖",
        "全球平均氣溫通常在聖嬰隔年創新高",
    ],
    "la_nina": [
        "台灣：秋冬降雨常偏少，隔年春季可能乾旱；冬季寒流機會略增",
        "東南亞、澳洲東部：降雨偏多、洪水風險上升",
        "美國南部冬季偏乾；大西洋颶風季通常較活躍",
        "南美西岸偏乾",
    ],
    "neutral": ["目前沒有明顯的聖嬰或反聖嬰訊號，季節氣候主要受其他因素影響"],
}


def status() -> dict:
    oni_rows = re.findall(r"^\s*([A-Z]{3})\s+(\d{4})\s+([\d.]+)\s+(-?[\d.]+)\s*$", get_text(ONI_URL), re.M)
    history = [{"season": s, "year": int(y), "anomaly": float(a)} for s, y, _, a in oni_rows][-12:]
    latest_oni = history[-1]

    weekly = None
    lines = [ln for ln in get_text(WEEKLY_URL).splitlines() if re.match(r"^\s*\d{2}[A-Z]{3}\d{4}", ln)]
    if lines:
        nums = re.findall(r"-?\d+\.\d", lines[-1])
        # 欄位：Niño1+2 SST/SSTA, Niño3, Niño3.4, Niño4
        weekly = {"week": lines[-1].split()[0], "nino34_sst": float(nums[4]), "nino34_anomaly": float(nums[5])}

    current = weekly["nino34_anomaly"] if weekly else latest_oni["anomaly"]
    phase, label = _phase(latest_oni["anomaly"])
    trend = history[-1]["anomaly"] - history[-4]["anomaly"] if len(history) >= 4 else 0.0
    return {
        "phase": phase,
        "label": label,
        "oni": latest_oni,
        "weekly": weekly,
        "weekly_label": _phase(current)[1],
        "trend": "增強中" if trend > 0.2 else "減弱中" if trend < -0.2 else "持平",
        "history": history,
        "impacts": IMPACTS[phase],
        "note": "ONI 為三個月滑動平均，官方以連續 5 季 ≥ +0.5°C 認定聖嬰事件；每週數值變化較快，僅供參考。",
    }
