# Сборка: meshctrl.js и его зависимости берём из того же образа, что и сервер,
# чтобы CLI всегда совпадал по версии с MeshCentral.
FROM ghcr.io/ylianst/meshcentral:latest AS meshcentral

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp/monitor-home

RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs tzdata \
    && rm -rf /var/lib/apt/lists/*

# meshctrl.js ищет minimist/ws рядом с собой (node_modules в /opt/meshctrl).
COPY --from=meshcentral /opt/meshcentral/meshcentral/meshctrl.js /opt/meshctrl/meshctrl.js
COPY --from=meshcentral /opt/meshcentral/meshcentral/node_modules/minimist /opt/meshctrl/node_modules/minimist
COPY --from=meshcentral /opt/meshcentral/meshcentral/node_modules/ws /opt/meshctrl/node_modules/ws

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN useradd --uid 1000 --user-group --create-home --home-dir /home/monitor monitor \
    && mkdir -p /data && chown monitor:monitor /data

USER monitor
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)" || exit 1

CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
