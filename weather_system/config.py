"""全系統共用設定。"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = BASE_DIR / "cache"
MODEL_DIR = BASE_DIR / "trained_models"

# 參與融合的全球數值天氣模式（Open-Meteo 名稱 → 顯示名稱）
MODELS = {
    "ecmwf_ifs025": "ECMWF 歐洲中期預報中心",
    "gfs_seamless": "GFS 美國 NOAA",
    "icon_seamless": "ICON 德國氣象局",
    "jma_seamless": "JMA 日本氣象廳",
    "meteofrance_seamless": "ARPEGE 法國氣象局",
    "gem_seamless": "GEM 加拿大環境部",
    "ukmo_seamless": "UKMO 英國氣象局",
}

# 用來訓練/修正的每日變數
DAILY_TARGETS = [
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "wind_speed_10m_max",
]

# 預報時另外抓取（不做機器學習修正，只做多模式加權）的每日變數
DAILY_EXTRA = [
    "snowfall_sum",
    "wind_gusts_10m_max",
    "precipitation_hours",
    "relative_humidity_2m_mean",
    "weather_code",
]

HOURLY_VARS = [
    "temperature_2m",
    "relative_humidity_2m",
    "precipitation",
    "snowfall",
    "wind_speed_10m",
    "wind_gusts_10m",
    "wind_direction_10m",
    "pressure_msl",
    "weather_code",
]

# 劇烈天氣診斷用（取 best_match 單一最佳模式，含高空資料）
CONVECTIVE_VARS = [
    "cape",
    "lifted_index",
    "convective_inhibition",
    "freezing_level_height",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_speed_850hPa",
    "wind_direction_850hPa",
    "wind_speed_500hPa",
    "wind_direction_500hPa",
    "weather_code",
    "visibility",
    "precipitation",
    "precipitation_probability",
    "cloud_cover",
    "is_day",
]

RAIN_THRESHOLD_MM = 1.0       # 「有下雨」的門檻（日累積雨量）
TRAIN_DAYS = 700              # 訓練用歷史天數（歷次預報存檔約從 2024-11 起）
VERIFY_LEADS = range(1, 8)    # 驗證第 1~7 天的預報
OBS_LAG_DAYS = 7              # ERA5 實測資料約延遲 5 天，保守取 7
MODEL_MAX_AGE_DAYS = 30       # 修正模型超過幾天自動重新訓練
FORECAST_DAYS = 16

USER_AGENT = "weather-forecast-system/1.0 (personal use)"
CWA_API_KEY_ENV = "CWA_API_KEY"  # 中央氣象署開放資料金鑰（選用）
