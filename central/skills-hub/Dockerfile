# catfish-skills-hub 生产镜像 (P39-central-fix2 7/14).

FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir --upgrade pip && \
    /opt/venv/bin/pip install --no-cache-dir .


FROM python:3.12-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --shell /bin/bash --uid 1000 catfish

COPY --from=builder /opt/venv /opt/venv
COPY --chown=catfish:catfish src /app/src
COPY --chown=catfish:catfish alembic.ini /app/
COPY --chown=catfish:catfish alembic /app/alembic

RUN mkdir -p /data/hub && chown -R catfish:catfish /data/hub

ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONPATH=/app/src
ENV PORT=8997
ENV HOST=0.0.0.0
ENV CATFISH_HUB_ROOT=/data/hub
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app
USER catfish

EXPOSE 8997

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:${PORT}/healthz || exit 1

CMD ["sh", "-c", "alembic upgrade head 2>&1 | grep -v 'CATFISH_DB_URL' || true; python -m catfish_skills_hub.app"]
