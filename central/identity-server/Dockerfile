# catfish-identity 生产镜像 (五一 sprint 5/2 收尾 BL-F7).
#
# 跟 gateway 同模式: builder + runtime 两阶段, 非 root, healthz, alembic 启动建表.

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


# ── runtime ──
FROM python:3.12-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --shell /bin/bash --uid 1000 catfish

# P3.5.80 (7/28 鸿波达华现场): 预建数据目录并 chown.
# docker-compose 把命名卷挂到 /home/catfish/.catfish —— 若该路径在镜像里
# **不存在**, Docker 会新建目录且属主为 root:root, 而容器跑的是 USER catfish,
# 写不进去. 挂载点在镜像里存在时, 空卷会继承该目录的属主与权限, 才是对的.
RUN mkdir -p /home/catfish/.catfish \
    && chown -R catfish:catfish /home/catfish/.catfish

COPY --from=builder /opt/venv /opt/venv
COPY --chown=catfish:catfish src /app/src
COPY --chown=catfish:catfish alembic.ini /app/
COPY --chown=catfish:catfish alembic /app/alembic
COPY --chown=catfish:catfish config /app/config

ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONPATH=/app/src
ENV PORT=8998
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV CATFISH_IDENTITY_PORT=8998

WORKDIR /app
USER catfish

EXPOSE 8998

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:${PORT}/.well-known/openid-configuration || exit 1

CMD ["sh", "-c", "alembic upgrade head && python -m catfish_identity.app"]
