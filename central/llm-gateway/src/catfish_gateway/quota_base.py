"""配额的地基 —— 后端选择 / 连接 / 路径 / 表结构。

2026-08-15 从 quota.py 切出来 (1528 行超限)。纯搬迁, 逻辑一行未改。

单独一层是为了让箭头单向。quota.py 末尾要 re-export config/audit 的符号
(app.py 全仓 26 处走 `quota.X`), 如果 config/audit 反过来 import quota,
就是循环 —— metrics.py 那次就这么炸过, 而且是"换个 import 顺序才炸"。
现在是: quota_base ← quota_config / quota_audit / quota, 没有反向。

⚠ _quota_db_path / _quota_config_path **每次调用都读 env**, 没有模块级缓存。
测试靠 CATFISH_QUOTA_DB / CATFISH_QUOTAS_PATH 两个 env 隔离 (见
tests/test_quota.py 的 isolated_db_and_config), 不是打桩模块全局 —— 所以搬
过来不影响它们。(metrics.py 那次翻车就是因为 _audit_path 是模块缓存, 一搬
测试的 monkeypatch 就打空了。)
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from ruamel.yaml import YAML

import logging

logger = logging.getLogger("catfish.gateway.quota")

_YAML_RT = YAML(typ="rt")
_YAML_RT.preserve_quotes = True
_YAML_RT.indent(mapping=2, sequence=4, offset=2)


# ── Backend 选择 (生产 PG-only; 单测可 sqlite override) ───────
#
# BL-QUOTA-SQLITE-DEPRECATE + BL-CENTRAL-EDGE-BOUNDARY (5/17 鸿波):
#   规则: 中央端不许碰用户本机文件 (包括 ~/.catfish/quota.db). 生产必走 PG.  # noqa: BOUNDARY
#   单测可以用 sqlite tmp 文件 (test fixture 设 CATFISH_QUOTA_DB 显式 override).
#
#   老逻辑: PG 写失败 → fallback sqlite (双写). 这违反规则因为 sqlite 落在员工
#   Mac. 删 fallback — PG 写失败 = log warning + 丢这条 audit (audit 不是关键
#   路径, 丢一条比写员工本机好).


def _use_pg() -> bool:
    """生产 PG, 单测 sqlite (test fixture 通过 CATFISH_QUOTA_DB env override).

    返回:
      True  — 走 PG (CATFISH_DB_URL 配了 — 生产默认)
      False — 走 sqlite (CATFISH_QUOTA_DB 显式配了, 单测路径)

    BL-CENTRAL-EDGE-BOUNDARY (5/17): 两个 env 都没配 = 配置错误, 不再静默走
    默认 ~/.catfish/quota.db (员工本机违规). 返 True 让调用方走 PG 路径 +  # noqa: BOUNDARY
    在 _pg_conn 时撞 connect 错 fail-loud, 显式而不是默默写错地方.
    """
    if os.environ.get("CATFISH_QUOTA_DB"):
        return False  # 单测显式 sqlite 路径
    return True  # PG mode — 没配 CATFISH_DB_URL 也走 PG 路径, _pg_conn 时报错


def _audit_backend_configured() -> bool:
    """有任一 audit backend 配了 (PG 或 test sqlite). 没配 → 生产模式该报警."""
    return bool(os.environ.get("CATFISH_DB_URL", "").strip()) or bool(
        os.environ.get("CATFISH_QUOTA_DB")
    )


_PG_CONN_INFO: str | None = None  # 缓存连接字符串, 避免重读 env


def _pg_conninfo() -> str:
    """psycopg 连接字符串. lazy 缓存."""
    global _PG_CONN_INFO
    if _PG_CONN_INFO is not None:
        return _PG_CONN_INFO
    url = os.environ.get("CATFISH_DB_URL", "").strip()
    _PG_CONN_INFO = url
    return url


def _pg_conn():
    """开一个 psycopg sync 连接. 一次性, caller close.

    不用池: quota 写量不大 (每个 LLM 请求 1 次 INSERT), 连接开销可接受.
    后续要池化加 psycopg_pool.
    """
    import psycopg  # 懒 import, 没装 PG 也能跑 sqlite mode
    return psycopg.connect(_pg_conninfo())


def _quota_db_path() -> Path:
    """sqlite quota.db 路径 (只单元测试用, CATFISH_QUOTA_DB env 显式 override).

    BL-QUOTA-SQLITE-DEPRECATE (5/17) + BL-CENTRAL-EDGE-BOUNDARY:
      生产模式必走 PG (CATFISH_DB_URL). 此函数只在 _use_pg() == False 时调,
      也就是单测路径. 单测 fixture 必须设 CATFISH_QUOTA_DB 显式指 tmp_path.
      不再有 ~/.catfish/quota.db 默认 fallback (员工本机违反 boundary).  # noqa: BOUNDARY

    调用 unreachable 在生产 — 但代码层强校验, 没设环境变量 fail-loud,
    防开发期"忘了 setup" 静默写到 home dir.
    """
    custom = os.environ.get("CATFISH_QUOTA_DB")
    if custom:
        return Path(custom).expanduser()
    raise RuntimeError(
        "CATFISH_QUOTA_DB env 未设, 但 _use_pg() 返回 False 走到 sqlite 分支. "
        "BL-CENTRAL-EDGE-BOUNDARY 不再 fallback ~/.catfish/quota.db. "  # noqa: BOUNDARY
        "生产应配 CATFISH_DB_URL 走 PG; 单测应在 fixture 设 CATFISH_QUOTA_DB tmp 路径."
    )


def _quota_config_path() -> Path:
    """quotas.yaml 路径. 优先级 (高→低):
        1. CATFISH_QUOTAS_PATH env (单文件 override)
        2. CATFISH_GATEWAY_CONFIG_PATH/quotas.yaml (P26: 统一目录 env)
        3. <repo>/central/llm-gateway/config/quotas.yaml (默认)
    """
    custom = os.environ.get("CATFISH_QUOTAS_PATH", "").strip()
    if custom:
        return Path(custom).expanduser()
    env_dir = os.environ.get("CATFISH_GATEWAY_CONFIG_PATH", "").strip()
    if env_dir:
        return Path(env_dir).expanduser() / "quotas.yaml"
    # quota.py → catfish_gateway/ → src/ → llm-gateway/  (3 个 parent)
    pkg_root = Path(__file__).resolve().parent.parent.parent
    return pkg_root / "config" / "quotas.yaml"




_SCHEMA = """
CREATE TABLE IF NOT EXISTS quota_events (
    ts INTEGER NOT NULL,
    user_email TEXT NOT NULL,
    department TEXT NOT NULL,
    model TEXT NOT NULL,
    tokens_in INTEGER NOT NULL,
    tokens_out INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quota_ts_user ON quota_events(ts, user_email);
CREATE INDEX IF NOT EXISTS idx_quota_ts_model ON quota_events(ts, model);
CREATE INDEX IF NOT EXISTS idx_quota_ts_dept ON quota_events(ts, department);
"""


def _get_conn() -> sqlite3.Connection:
    p = _quota_db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=2.0)
    conn.executescript(_SCHEMA)
    return conn
