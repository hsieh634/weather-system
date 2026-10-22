# 全球氣象預測系統：網頁 + API
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY weather_system ./weather_system
COPY static ./static
COPY app.py cli.py README.md ./

# 快取與訓練好的模型寫在容器內；雲端平台重啟後會清空，系統會自動重新下載與訓練
RUN useradd --create-home app && mkdir -p cache trained_models && chown -R app /app
USER app

EXPOSE 8000
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
