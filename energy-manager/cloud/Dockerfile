# Energy Manager Cloud — API + portals (amd64/arm64). Runs behind a TLS reverse proxy (see docker-compose.yml).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml ./
COPY emcloud ./emcloud
RUN pip install ".[postgres]" \
 && useradd --system --uid 1000 --home /app --shell /usr/sbin/nologin emcloud

USER emcloud
EXPOSE 8800
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8800/api/v1/health', timeout=4).status == 200 else 1)"
STOPSIGNAL SIGTERM
CMD ["uvicorn", "emcloud.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8800", "--proxy-headers", "--forwarded-allow-ips", "*"]
