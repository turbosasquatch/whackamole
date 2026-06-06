FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV WHACKAMOLE_CONFIG_DIR=/config

WORKDIR /app

COPY requirements.txt .
RUN apt-get update \
  && apt-get install -y --no-install-recommends mediainfo \
  && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

VOLUME ["/config", "/data/torrents", "/ua-tmp"]
EXPOSE 8383

HEALTHCHECK --interval=60s --timeout=10s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8383/api/status', timeout=5)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8383"]
