# catfish-skills-hub 生产镜像 (P39-central-fix2 7/14).

FROM python:3.12-slim AS builder

WORKDIR /build

# 10/6: arm64 交付包首次构建 (没缓存) 卡在这一步 22 分钟 —— deb.debian.org 经
# Docker Desktop 代理 ~50kB/s。跟 mcp-registry/Dockerfile (6/9 BL-F10) 同款换清华源。
RUN set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i 's|http://deb.debian.org|https://mirrors.tuna.tsinghua.edu.cn|g' \
            /etc/apt/sources.list.d/debian.sources; \
    fi
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src

# pip 走清华 mirror (同 mcp-registry; /etc/pip.conf 对 venv 里的 pip 也生效)
RUN pip config --global set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir --upgrade pip && \
    /opt/venv/bin/pip install --no-cache-dir .


FROM python:3.12-slim AS runtime

# 10/6: arm64 交付包首次构建 (没缓存) 卡在这一步 22 分钟 —— deb.debian.org 经
# Docker Desktop 代理 ~50kB/s。跟 mcp-registry/Dockerfile (6/9 BL-F10) 同款换清华源。
RUN set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i 's|http://deb.debian.org|https://mirrors.tuna.tsinghua.edu.cn|g' \
            /etc/apt/sources.list.d/debian.sources; \
    fi
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
