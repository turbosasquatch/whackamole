FROM python:3.12.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV WHACKAMOLE_CONFIG_DIR=/config
ENV WHACKAMOLE_ALLOWED_MEDIA_ROOTS=/data/torrents,/media/torrents,/ua-tmp
ENV WHACKAMOLE_MEDIAINFO_BINARY=/usr/bin/mediainfo

WORKDIR /app

COPY requirements.txt .
RUN apt-get update \
  && apt-get install -y --no-install-recommends gosu mediainfo \
  && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r requirements.txt

RUN addgroup --system --gid 10001 whackamole \
  && adduser --system --uid 10001 --ingroup whackamole --home /app whackamole \
  && mkdir -p /config /data/torrents /ua-tmp \
  && chown -R 10001:10001 /app /config /data/torrents /ua-tmp

COPY --chown=10001:10001 app ./app
COPY --chown=10001:10001 tools/admin_credentials.py ./tools/admin_credentials.py
COPY --chown=0:0 docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 0755 /usr/local/bin/docker-entrypoint.sh

VOLUME ["/config", "/data/torrents", "/ua-tmp"]
EXPOSE 8383

HEALTHCHECK --interval=60s --timeout=10s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8383/api/status', timeout=5)"

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8383", "--no-proxy-headers"]
