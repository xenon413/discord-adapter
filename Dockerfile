FROM python:3.12-slim AS builder
ARG ENABLE_VOICE=false
WORKDIR /build
COPY pyproject.toml ./
COPY app ./app
RUN if [ "$ENABLE_VOICE" = "true" ]; then \
      python -m pip wheel --no-cache-dir --wheel-dir /wheels '.[voice]'; \
    else \
      python -m pip wheel --no-cache-dir --wheel-dir /wheels .; \
    fi

FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOST=0.0.0.0 PORT=8000
RUN apt-get update \
    && apt-get install -y --no-install-recommends libopus0 libsodium23 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 adapter \
    && useradd --uid 10001 --gid adapter --create-home adapter
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir --no-index /wheels/* && rm -rf /wheels
WORKDIR /app
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import json,os,urllib.request; r=urllib.request.Request('http://127.0.0.1:8000/health',headers={'X-API-Key':os.environ['API_SECRET_KEY']}); data=json.load(urllib.request.urlopen(r,timeout=3)); raise SystemExit(0 if data['discord_connected'] else 1)"
CMD ["python", "-m", "app.main"]
