"""供应商配置的库存储 (8/1).

跟 model_store 同一个套路 (psycopg 同步 / 没配 PG 返 None / 写操作 bump
revision), 理由见 model_store 文件头。

## migrate_models_to_providers 为什么放在启动时而不是 alembic 里

拆分要读 JSONB、按 (api_base, api_key_env) 去重、生成可读 id、回写 —— 用
Python 写能逐条钉测试并注入故障验证, 写成 SQL 只能靠肉眼。而且这个仓已经
有两个"启动时幂等迁移"的先例 (restore_env_placeholders / enforce_single_default,
7/30), 套路一致。

alembic 那边只建表, 但 `downgrade()` 必须把 provider 字段内联回模型 ——
只 DROP TABLE 的话回滚等于把模型配置删掉一半 (见 20260801_008)。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .model_store import _bump_revision, _conn, is_enabled

logger = logging.getLogger(__name__)

#: 表不存在的警告只打一次 —— read_providers 每 3 秒被配置重载调一次, 每次都
#: 打的话日志会被淹掉, 而淹掉的日志等于没有日志。
_WARNED_MISSING = False


def read_providers() -> dict[str, dict[str, Any]] | None:
    """所有供应商, id → 行. 库不可用返 None (上层退回纯 yaml / 老形态).

    api_key_enc 是密文, **这里原样返回 bytes**, 解密在更上层做 ——
    这样"谁能解密"是一个可以单独审的点, 而不是散在读取路径里。
    """
    if not is_enabled():
        return None
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, display_name, api_base, api_key_env, api_key_enc, timeout "
                "FROM gateway_providers ORDER BY id"
            )
            return {
                r[0]: {
                    "id": r[0],
                    "display_name": r[1],
                    "api_base": r[2],
                    "api_key_env": r[3],
                    "api_key_enc": r[4],
                    "timeout": r[5],
                }
                for r in cur.fetchall()
            }
    except Exception:
        # 表还没建 (没跑 alembic 008) 也走这里。这**是**一个能工作的中间态 ——
        # 老形态的模型 upstream 里自带 api_base/api_key_env, 不需要这张表。
        #
        # 但不能只在 DEBUG 级别记一行: 表现是「供应商」页显示"0 家",
        # 跟"真的一家都没有"长得一模一样, 而管理员会去点「+ 新增供应商」,
        # 然后保存失败。8/1 鸿波第一次打开这一页就撞上了。
        #
        # 所以: 首次警告一次 (不刷屏 —— 这个函数每 3 秒被配置重载调一次),
        # 并且让 read_providers 的 None 和 {} 在**接口层**分开
        # (见 admin_providers_router 的 table_ready)。
        global _WARNED_MISSING
        if not _WARNED_MISSING:
            _WARNED_MISSING = True
            logger.warning(
                "读 gateway_providers 失败 —— 多半是 alembic 008 还没跑。"
                "现在按老形态运行 (模型 upstream 里自带端点和 key 变量名), "
                "但「供应商」页会是空的。\n"
                "修法: cd central/llm-gateway && alembic upgrade head, 然后重启网关。\n"
                "(Docker 部署不会走到这里 —— Dockerfile 的 CMD 就是 "
                "`alembic upgrade head && python -m catfish_gateway.app`。"
                "本机 dev 直跑 app 的话要自己跑一次。)",
                exc_info=True,
            )
        return None


def upsert_provider(pid: str, row: dict[str, Any], by: str) -> None:
    """新增或覆盖一个供应商.

    ⚠ `api_key_enc` 只在 row 里**显式出现**时才写。界面上 key 输入框留空 =
    不改, 这是密码字段的标准做法 —— 否则每次编辑别的字段都会把 key 清掉,
    而且清掉之后没有任何提示, 要等员工调用失败才发现。
    """
    if not is_enabled():
        raise RuntimeError("未配置 CATFISH_DB_URL, 供应商配置不可写")

    cols = ["display_name", "api_base", "api_key_env", "timeout"]
    vals: list[Any] = [
        row.get("display_name") or pid,
        row.get("api_base") or None,
        row.get("api_key_env") or None,
        int(row.get("timeout") or 60),
    ]
    if "api_key_enc" in row:
        cols.append("api_key_enc")
        vals.append(row["api_key_enc"])

    set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols)
    placeholders = ", ".join(["%s"] * len(cols))
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO gateway_providers (id, {', '.join(cols)}, updated_by, updated_at) "
            f"VALUES (%s, {placeholders}, %s, now()) "
            f"ON CONFLICT (id) DO UPDATE SET {set_clause}, "
            f"updated_by = EXCLUDED.updated_by, updated_at = now()",
            (pid, *vals, by),
        )
        _bump_revision(cur)
        conn.commit()


def delete_provider(pid: str, by: str) -> bool:
    """删一个供应商. 返回是否真的删掉了.

    ⚠ **调用方必须先确认没有模型在引用它。** 这里不查是因为"谁在引用"要读
    gateway_models 的 JSONB, 属于业务判断; 而且拦截时要点名是哪几个模型
    (同 fallback 链那条), 错误信息得在 API 层拼。
    """
    if not is_enabled():
        raise RuntimeError("未配置 CATFISH_DB_URL, 供应商配置不可写")
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM gateway_providers WHERE id = %s", (pid,))
        deleted = cur.rowcount > 0
        if deleted:
            _bump_revision(cur)
        conn.commit()
        return deleted


def models_using(pid: str) -> list[dict[str, str]]:
    """哪些模型在引用这个供应商. 返回 [{name, display_name}].

    **两个都要**, 用途不同:
      name          唯一标识。删除拦截的报错里用它 —— 显示名可以重复,
                    报错里给重复的名字等于没给。
      display_name  给人看的。界面上列出"哪几个模型在用"时用它 ——
                    列表里显示 catfish-private-vision 不如显示
                    「Qwen3-VL 30B」直观。

    8/1 鸿波: "模型 1、2 代表的要用人看懂的值" —— 原来这里只返 name,
    而界面只显示了个数量。数量本身不构成信息: 你想知道的是**哪几个**,
    而那恰恰决定了这家能不能删。
    """
    if not is_enabled():
        return []
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT name, COALESCE(payload ->> 'display_name', name) "
                "FROM gateway_models "
                "WHERE payload -> 'upstream' ->> 'provider' = %s "
                "ORDER BY sort_order, name",
                (pid,),
            )
            return [{"name": r[0], "display_name": r[1]} for r in cur.fetchall()]
    except Exception:
        logger.exception("查供应商引用失败")
        return []


def migrate_models_to_providers(by: str = "migrate:provider-split") -> list[str]:
    """把老形态的模型拆成供应商 + 引用. 返回新建的供应商 id.

    幂等: 已经是新形态 (upstream 里有 provider) 的模型跳过; 供应商按
    (api_base, api_key_env) 去重, 已存在的不重建。

    **整件事在一个事务里**。拆到一半失败的话, 会出现"供应商建好了但模型还
    指着老字段", 那个中间态本身能工作 (老形态原样通过), 但下次再跑会按
    已有的供应商重新去重 —— 所以即使失败也不会越搞越乱。用事务是为了让
    "模型改成引用" 和 "供应商存在" 这两件事不可能只发生一半。
    """
    from .config_providers import split_upstream

    if not is_enabled():
        return []

    created: list[str] = []
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT id, api_base, api_key_env, timeout FROM gateway_providers")
            existing = cur.fetchall()
            # 已有供应商也进去重表, 这样重复跑不会建出 dashscope-2
            by_key: dict[str, str] = {
                f"{(r[1] or '').strip()}|{(r[2] or '').strip()}": r[0] for r in existing
            }
            # ⚠ 供应商**实际存的** timeout, 按 pid 记。
            #
            # 下面判断"模型的 timeout 要不要单独保留"时必须跟这个比, 不能跟
            # 当前模型重新算出来的 prow 比 —— 两个模型共用一家而 timeout 不同时,
            # 后者会跟自己比, 结论永远是"相同, 可以省掉", 于是它的 timeout
            # **静默变成前者的值**。
            #
            # 实盘就有: gemini-pro timeout 90 / gemini-flash timeout 60,
            # 两个共用 gemini 这一家。第一版代码会把 flash 从 60 改成 90,
            # 而且配置上看不出少了什么 —— 黄金对照测试逮到的就是这个。
            provider_timeout: dict[str, int] = {r[0]: r[3] for r in existing}
            taken = set(by_key.values())

            cur.execute("SELECT name, payload FROM gateway_models ORDER BY sort_order, name")
            rows = cur.fetchall()

            for name, payload in rows:
                up = (payload or {}).get("upstream") or {}
                if up.get("provider"):
                    continue  # 已经是新形态

                dedupe_key, suggested, prow = split_upstream(up)
                pid = by_key.get(dedupe_key)
                if pid is None:
                    pid = _unique_id(suggested, taken)
                    taken.add(pid)
                    by_key[dedupe_key] = pid
                    cur.execute(
                        "INSERT INTO gateway_providers "
                        "(id, display_name, api_base, api_key_env, timeout, updated_by) "
                        "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
                        (pid, pid, prow["api_base"], prow["api_key_env"], prow["timeout"], by),
                    )
                    created.append(pid)
                    provider_timeout[pid] = prow["timeout"]

                new_up = {"model": up.get("model"), "provider": pid}
                # timeout 留在模型这一层 —— 它是模型属性 (视觉 180 / embedding 30),
                # 只有跟**这家供应商实际存的**默认值相同时才省掉 (见上面的说明)。
                if up.get("timeout") and up["timeout"] != provider_timeout.get(pid):
                    new_up["timeout"] = up["timeout"]
                # param_overrides 是模型级的, 跟着模型走
                if up.get("param_overrides"):
                    new_up["param_overrides"] = up["param_overrides"]
                if up.get("param_overrides_scope"):
                    new_up["param_overrides_scope"] = up["param_overrides_scope"]

                cur.execute(
                    "UPDATE gateway_models SET payload = %s::jsonb, "
                    "updated_by = %s, updated_at = now() WHERE name = %s",
                    (json.dumps({**payload, "upstream": new_up}, ensure_ascii=False), by, name),
                )

            if rows:
                _bump_revision(cur)
            conn.commit()
    except Exception:
        # 迁移失败不该拦住启动 —— 老形态的模型仍然能正常工作 (merge_provider
        # 对没有 provider 键的 row 原样放行)。但必须留日志。
        logger.exception("拆分供应商失败, 本次继续按老形态运行")
        return []
    return created


def _unique_id(base: str, taken: set[str]) -> str:
    """重名时补数字后缀.

    三个内网端点的 key 变量名都是 INTERNAL_LLM_KEY, 域名又是 IP 取不出东西,
    所以 suggest_provider_id 会给出同一个 'internal-llm' —— 这里补成
    internal-llm / internal-llm-2 / internal-llm-3。

    不好看, 但比随机串强, 而且客户可以在界面上改 display_name。
    真正的名字 (「内网 vLLM · 视觉」) 该由人来起, 猜不出来。
    """
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"
