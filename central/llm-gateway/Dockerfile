# catfish-gateway 生产镜像 (五一 sprint 5/2 收尾 BL-F7).
#
# 多阶段构建 — builder 装依赖, runtime 只带最终 venv + 源码.
# Python 3.12, alpine 太瘦 (litellm/cryptography 编译麻烦), 用 slim.
# 跟客户 IT 演 demo: docker pull catfish-gateway:0.1.0 → docker run, 一句话启.

FROM python:3.12-slim AS builder

WORKDIR /build

# 系统依赖 (psycopg / cryptography 编译用)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 拷源码先 (利用 docker layer cache)
COPY pyproject.toml README.md ./
COPY src ./src

# 装到 /opt/venv (干净虚拟环境, 跟系统 Python 隔离)
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir --upgrade pip && \
    /opt/venv/bin/pip install --no-cache-dir .


# ── runtime ──
FROM python:3.12-slim AS runtime

# 运行时依赖 (psycopg 二进制, ca-certificates 给 https 给 LLM API, curl 给 healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

# 非 root 用户 (生产最佳实践, 防容器逃逸提权)
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
ENV PORT=8999
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app
USER catfish

EXPOSE 8999

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:${PORT}/healthz || exit 1

# 生产启动: 先 alembic upgrade head 建表, 再启 gateway.
# 容器入口 用 sh -c 串两个命令, 失败 (alembic 错) container 退出, k8s/docker-compose 重启.
CMD ["sh", "-c", "alembic upgrade head && python -m catfish_gateway.app"]
