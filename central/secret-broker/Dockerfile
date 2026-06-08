# catfish-secret-broker 生产镜像 (6/9 BL-BROKER-DEPLOY 集成进 prod stack).
#
# 跟 identity-server / mcp-registry 同模式: builder + runtime 两阶段, 非 root,
# healthz, AES-256-GCM PG 加密后端. 端口 8995, **只内网** (mcp-registry / gateway
# 调, 不暴露给员工浏览器).
#
# !! 不打 keyring backend 系统依赖 (dbus / libsecret) — docker container 没桌面
# session, keyring 起不来. prod 必须 CATFISH_SECRET_BROKER_BACKEND=pg.

FROM python:3.12-slim AS builder

WORKDIR /build

# 6/9 BL-F10 撞过: Clash 拦 apt 默认 mirror.
RUN set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i 's|http://deb.debian.org|https://mirrors.tuna.tsinghua.edu.cn|g' \
            /etc/apt/sources.list.d/debian.sources; \
    fi

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir --upgrade pip && \
    /opt/venv/bin/pip install --no-cache-dir .


# ── runtime ──
FROM python:3.12-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --shell /bin/bash --uid 1000 catfish

COPY --from=builder /opt/venv /opt/venv
COPY --chown=catfish:catfish src /app/src

ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONPATH=/app/src
ENV PORT=8995
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# prod 默认走 pg 加密后端 (docker container 没桌面, keyring 起不来)
ENV CATFISH_SECRET_BROKER_BACKEND=pg

WORKDIR /app
USER catfish

EXPOSE 8995

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:${PORT}/health || exit 1

CMD ["python", "-m", "catfish_secret_broker.app"]
