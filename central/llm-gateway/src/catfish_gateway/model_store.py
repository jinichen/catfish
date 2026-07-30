"""模型配置的库存储 (7/30).

## 这一层解决什么

在这之前, 模型只能改 models.yaml 再重启 gateway。而 models.yaml 在容器里是
**只读挂载**, 所以"在界面上改模型参数"这条路根本不存在。

## yaml 和库是什么关系

    models.yaml   出厂默认 / 种子。交付包里带的那份, 决定客户装完开箱有哪些模型。
    库            运行时事实源。一旦播种过, 读配置只看库。

首次启动时若库里没有任何模型, 用 yaml 里的播种一次 (幂等, 见 seed_from_yaml)。
之后 yaml 不再参与模型列表 —— 否则升级包换了 yaml 会把客户现场的改动冲掉。

**顶层字段 (auto_fallback / max_fallback_prompt_tokens / 三个 hub 的地址)
仍然只从 yaml 读**, 不进库。它们是部署形态而不是业务配置, 改它们本来就该
伴随一次发布。

## 为什么用 psycopg 同步而不是 asyncpg

get_config() 是同步函数, 16 个调用点遍布同步/异步两种上下文。要走 asyncpg
就得把这 16 处全改成 async, 牵动面太大。quota.py 已有 psycopg 同步的先例
(每个 LLM 请求一次 INSERT 都扛得住), 配置读取是低频且有 TTL 缓存挡着,
同步完全够用。

## 没配 PG 怎么办

返 None, 上层退回纯 yaml。这条路只给单测和本机 dev 用 —— 生产的
docker-compose 一定配了 CATFISH_DB_URL。跟 quota 的 fail-loud 政策略有不同:
quota 不配就抛, 是因为配额数据丢了没法补; 而模型配置在 yaml 里本来就有一份
完整的, 退回去仍然能正常服务, 抛异常反而让本机 dev 跑不起来。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def _conninfo() -> str:
    return os.environ.get("CATFISH_DB_URL", "").strip()


def is_enabled() -> bool:
    """配了 PG 才启用库存储. 没配 → 上层退回纯 yaml."""
    return bool(_conninfo())


def _conn():
    """一次性同步连接, caller 负责关 (用 with)."""
    import psycopg  # 懒 import: 没装 PG 驱动也要能跑 yaml 模式

    return psycopg.connect(_conninfo())


def revision() -> int | None:
    """当前配置代次. 库不可用返 None.

    这是缓存是否要重新加载的**唯一依据**, 所以必须单调递增 ——
    见 migration 20260730_007 里为什么不用 max(updated_at)。
    """
    if not is_enabled():
        return None
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT revision FROM gateway_config_meta WHERE id = 1")
            row = cur.fetchone()
            return int(row[0]) if row else 0
    except Exception:
        # 库临时不可用不该让整个配置读取炸掉 —— 上层会沿用上一份缓存。
        # 但要留日志: 静默降级到 yaml 会让人以为改动没保存。
        logger.exception("读配置代次失败 (库不可用?), 上层将沿用缓存/yaml")
        return None


def read_models() -> list[dict[str, Any]] | None:
    """库里的模型列表 (已按展示顺序排好).

    返回:
        None      库没启用 / 不可用 → 上层用 yaml
        []        库通但没有任何模型 → 还没播种
        [ ... ]   正常
    """
    if not is_enabled():
        return None
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT payload FROM gateway_models ORDER BY sort_order, name"
            )
            return [r[0] for r in cur.fetchall()]
    except Exception:
        logger.exception("读模型配置失败 (库不可用?), 上层将沿用缓存/yaml")
        return None


def seed_from_yaml(models: list[dict[str, Any]]) -> int:
    """把 yaml 里的模型播种进库. 已有同名的**不覆盖**.

    幂等, 而且并发安全 —— 4 个 worker 启动时会同时跑这个, 靠
    ON CONFLICT DO NOTHING 让重复播种变成空操作。

    返回真正插进去的条数 (0 表示已经播过了)。
    """
    if not is_enabled() or not models:
        return 0
    try:
        with _conn() as conn, conn.cursor() as cur:
            n = 0
            for i, m in enumerate(models):
                cur.execute(
                    """
                    INSERT INTO gateway_models (name, payload, sort_order, updated_by)
                    VALUES (%s, %s::jsonb, %s, 'seed:models.yaml')
                    ON CONFLICT (name) DO NOTHING
                    """,
                    (m["name"], json.dumps(m, ensure_ascii=False), i),
                )
                n += cur.rowcount
            if n:
                _bump_revision(cur)
            conn.commit()
            return n
    except Exception:
        logger.exception("播种模型配置失败")
        return 0


def _bump_revision(cur) -> None:
    """代次 +1. 必须跟数据改动在**同一个事务**里 ——
    分开的话会出现"数据已改、代次没变"的窗口, 那期间所有 worker 都不会重新
    加载, 表现成"保存成功但不生效"。"""
    cur.execute(
        "UPDATE gateway_config_meta SET revision = revision + 1 WHERE id = 1"
    )


def upsert_model(name: str, payload: dict[str, Any], by: str) -> None:
    """新增或覆盖一个模型. payload 必须是已经过 ModelConfig 校验的 dict."""
    if not is_enabled():
        raise RuntimeError("未配置 CATFISH_DB_URL, 模型配置不可写")
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO gateway_models (name, payload, updated_by, updated_at)
            VALUES (%s, %s::jsonb, %s, now())
            ON CONFLICT (name) DO UPDATE
              SET payload = EXCLUDED.payload,
                  updated_by = EXCLUDED.updated_by,
                  updated_at = now()
            """,
            (name, json.dumps(payload, ensure_ascii=False), by),
        )
        _bump_revision(cur)
        conn.commit()


def delete_model(name: str, by: str) -> bool:
    """删一个模型. 返回是否真的删掉了 (False = 本来就不存在)."""
    if not is_enabled():
        raise RuntimeError("未配置 CATFISH_DB_URL, 模型配置不可写")
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM gateway_models WHERE name = %s", (name,))
        deleted = cur.rowcount > 0
        if deleted:
            _bump_revision(cur)
        conn.commit()
        return deleted


def set_order(names: list[str], by: str) -> None:
    """按给定顺序重排. 不在列表里的保持原样排在后面."""
    if not is_enabled():
        raise RuntimeError("未配置 CATFISH_DB_URL, 模型配置不可写")
    with _conn() as conn, conn.cursor() as cur:
        for i, n in enumerate(names):
            cur.execute(
                "UPDATE gateway_models SET sort_order = %s, updated_by = %s, "
                "updated_at = now() WHERE name = %s",
                (i, by, n),
            )
        _bump_revision(cur)
        conn.commit()
