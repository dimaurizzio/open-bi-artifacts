# syntax=docker/dockerfile:1.7
#
# Imagen de producción: frontend compilado + backend FastAPI, como usuario sin
# privilegios. Los datos (sesiones, artifacts, knowledge.db, bundle OKF) van en
# el volumen /data. Ver docs/despliegue.md.

ARG PYTHON_IMAGE=python:3.13.13-slim-bookworm
ARG NODE_IMAGE=node:22.22-bookworm-slim

# ---- 1. Frontend (React + Vite) ---------------------------------------------------
FROM ${NODE_IMAGE} AS web
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY web/ ./
# vite.config.ts escribe el build en ../server/static/app
RUN mkdir -p ../server/static && npm run build

# ---- 2. Dependencias de Python en un venv ---------------------------------------------
FROM ${PYTHON_IMAGE} AS deps
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
COPY requirements.txt /tmp/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip pip install -r /tmp/requirements.txt

# ---- 3. Imagen final ------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime

ARG APP_UID=10001
ARG APP_VERSION=dev
LABEL org.opencontainers.image.title="open-bi-artifacts" \
      org.opencontainers.image.description="Open BI Artifacts: análisis de datos y dashboards con IA sobre BigQuery" \
      org.opencontainers.image.version="${APP_VERSION}"

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/data \
    KNOWLEDGE_OKF_DIR=/data/knowledge-okf \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache

RUN groupadd --system --gid ${APP_UID} app \
 && useradd --system --uid ${APP_UID} --gid app --no-create-home --shell /usr/sbin/nologin app \
 && mkdir -p /data \
 && chown app:app /data

WORKDIR /app
COPY --from=deps /opt/venv /opt/venv
# Solo lo que corre en producción (sin tests, docs ni fuentes del front).
COPY __init__.py agent.py config.py models.py routing.py user_context.py agent_dashboards/
COPY artifacts/ agent_dashboards/artifacts/
COPY auth/ agent_dashboards/auth/
COPY demo/ agent_dashboards/demo/
COPY knowledge/ agent_dashboards/knowledge/
COPY server/ agent_dashboards/server/
COPY skills/ agent_dashboards/skills/
COPY warehouse/ agent_dashboards/warehouse/
COPY --from=web /src/server/static/app/ agent_dashboards/server/static/app/
# El código queda de root y de solo lectura; se compila acá porque en runtime no se escribe.
RUN python -m compileall -q agent_dashboards

USER app
VOLUME ["/data"]
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=4).status == 200 else 1)"]

# Un solo worker: los turnos en curso viven en memoria del proceso (server/runs.py).
# --forwarded-allow-ips '*' es seguro porque el puerto no se publica: solo llega cloudflared.
CMD ["uvicorn", "agent_dashboards.server.app:app", \
     "--host", "0.0.0.0", "--port", "8765", "--workers", "1", \
     "--proxy-headers", "--forwarded-allow-ips", "*", \
     "--no-server-header", "--timeout-graceful-shutdown", "20"]
