# Energy Manager — EMS server image (amd64 + arm64 / Raspberry Pi 4/5, 64-bit OS)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY backend ./backend
RUN pip install ".[postgres]" \
 && useradd --system --uid 1000 --home /data --shell /usr/sbin/nologin ems \
 && mkdir -p /data && chown ems:ems /data

USER ems
ENV EMS_DATA_DIR=/data EMS_HOST=0.0.0.0 EMS_PORT=8080 EMS_LOG_JSON=1
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import os,sys,urllib.request; p=os.environ.get('EMS_PORT','8080'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{p}/healthz', timeout=4).status == 200 else 1)"

STOPSIGNAL SIGTERM
CMD ["ems", "serve"]
