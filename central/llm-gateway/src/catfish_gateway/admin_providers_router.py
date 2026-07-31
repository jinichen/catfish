"""供应商配置管理接口 (8/1, DESIGN-PROVIDER-SPLIT §7).

单独成文件而不是塞进 admin_models_router: 那个已经 452 行, 再加这一组会进
警戒区 (CLAUDE.md 军规 §4 "生成代码前看一下目标文件多大, > 500 行就考虑拆")。

## 三条硬规则

**1. key 永不回传。** 返回的是 `key_source` + `has_key`, 密文和明文都不出
这个进程。界面上 key 输入框留空 = 不改, 填了 = 覆盖 —— 这是密码字段的标准
做法, 也避免了"界面往返一次把 key 明文送了一圈"。

**2. 主密钥没配时拒绝保存 key。** 不能让人以为存进去了 —— 那会变成
"填了 key、界面显示成功、但员工调用一直失败", 而且没人知道去哪查。

**3. 删除前必须确认没有模型在引用, 并且点名是哪几个。** 跟 fallback 链那条
拦截同一个道理: 只说"删不掉"的话, 管理员得自己一个个翻模型去找。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import model_store, provider_store, secrets_box
from .auth import User, get_current_user
from .config import invalidate_config

logger = logging.getLogger(__name__)

#: provider id 会进 URL、进模型配置的 upstream.provider、进界面下拉框。
#: 限成 slug 是为了这三处都不用转义。
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


class ProviderBody(BaseModel):
    """界面提交的供应商配置.

    `api_key` 是**可选**的: 不传 = 不改 (编辑别的字段时不该动 key),
    传空串 = 清掉存库的 key (改回走环境变量)。这两种要分开, 所以用
    `str | None` 而不是默认空串 —— 默认空串的话"没传"和"传了空"分不出来,
    结果是每次编辑显示名都把 key 清掉。
    """

    display_name: str = ""
    api_base: str | None = None
    api_key_env: str | None = None
    api_key: str | None = None
    timeout: int = Field(default=60, ge=1, le=3600)


def register_provider_admin_routes(app: FastAPI) -> None:
    """把这一组接口挂到 app 上. app.py 建完 app 之后调一次."""

    def _require(user: User) -> None:
        if user.role != "sysadmin":
            raise HTTPException(
                403, detail=f"role={user.role} 不能编辑供应商配置 (sysadmin only)"
            )

    def _require_store() -> None:
        # 没配库时明确拒绝而不是假装成功 —— 静默返回 ok 的话界面会显示
        # "保存成功"但什么都没发生, 那比报错难查得多。
        if not model_store.is_enabled():
            raise HTTPException(
                503,
                detail=(
                    "配置库未启用 (未配置 CATFISH_DB_URL), 无法修改供应商。"
                    "此时模型配置来自 models.yaml, 只能改文件后重启。"
                ),
            )

    def _key_source(row: dict[str, Any]) -> str:
        """这家供应商的 key 从哪来. **不返回 key 本身。**"""
        if row.get("api_key_enc"):
            return "stored"
        if row.get("api_key_env"):
            return "env"
        return "none"

    @app.get("/api/admin/providers")
    async def api_admin_providers_list(
        user: User = Depends(get_current_user),
    ) -> dict[str, Any]:
        """供应商列表. sysadmin only.

        ⚠ 返回里**没有 key 的任何字节** —— 只有 key_source 和 key_ok。
        """
        _require(user)

        # ⚠ None 和 {} 必须分开:
        #   None = 表不存在 (alembic 008 没跑) / 库临时不可用
        #   {}   = 表在, 但确实一家供应商都没有
        # 合并成 `or {}` 的话界面上两种都显示"0 家", 而管理员会去点
        # 「+ 新增供应商」然后保存失败 —— 8/1 鸿波第一次打开这一页就撞上了。
        raw = provider_store.read_providers()
        table_ready = raw is not None
        rows = raw or {}

        out = []
        for pid, r in sorted(rows.items()):
            src = _key_source(r)
            # key 到底能不能用。存库的要能解开, 走 env 的那个变量要真的设了。
            # 这是管理员最想知道的一件事, 而它既不在库里也不在配置里 ——
            # 之前只能等员工调用失败才发现 (7/30 同款问题)。
            if src == "stored":
                ok = secrets_box.decrypt(r["api_key_enc"]) is not None
            elif src == "env":
                import os  # noqa: PLC0415

                ok = bool(os.environ.get(r["api_key_env"] or ""))
            else:
                ok = False
            out.append(
                {
                    "id": pid,
                    "display_name": r.get("display_name") or pid,
                    "api_base": r.get("api_base"),
                    "api_key_env": r.get("api_key_env"),
                    "key_source": src,
                    "key_ok": ok,
                    "timeout": r.get("timeout") or 60,
                    "models": provider_store.models_using(pid),
                }
            )

        return {
            "ok": True,
            "editable": model_store.is_enabled(),
            # 主密钥配了没 —— 界面据此决定要不要允许填 key。没配就该在保存前
            # 说清楚, 而不是让人填完点保存才撞 400。
            "secret_key_configured": secrets_box.is_configured(),
            "master_key_env": secrets_box.MASTER_KEY_ENV,
            # False = 供应商表还没建 (多半是 alembic 008 没跑)。界面据此
            # 显示"迁移没跑"而不是"还没有供应商" —— 两者的下一步动作完全不同。
            "table_ready": table_ready,
            "providers": out,
        }

    @app.put("/api/admin/providers/{pid}")
    async def api_admin_providers_put(
        pid: str,
        body: ProviderBody,
        user: User = Depends(get_current_user),
    ) -> dict[str, Any]:
        """新增或修改一个供应商. sysadmin only."""
        _require(user)
        _require_store()

        if not _ID_RE.match(pid):
            raise HTTPException(
                400,
                detail=(
                    f"供应商标识 {pid!r} 不合法。只能用小写字母、数字、连字符, "
                    "且以字母或数字开头 —— 它会出现在 URL 和模型配置里。\n"
                    "例如: dashscope / internal-vllm-vision"
                ),
            )

        existing = (provider_store.read_providers() or {}).get(pid)
        row: dict[str, Any] = {
            "display_name": body.display_name or pid,
            "api_base": (body.api_base or "").strip() or None,
            "api_key_env": (body.api_key_env or "").strip() or None,
            "timeout": body.timeout,
        }

        # ── key: 不传 = 不改, 传了 = 覆盖, 传空串 = 清掉 ──────────────
        if body.api_key is not None:
            plain = body.api_key.strip()
            if plain:
                if not secrets_box.is_configured():
                    # 拒绝而不是静默不存 —— 静默的话界面显示"保存成功",
                    # 而员工调用一直失败, 没人知道去哪查。
                    raise HTTPException(
                        400,
                        detail=(
                            f"服务器还没有配 {secrets_box.MASTER_KEY_ENV}, "
                            "没法加密保存 API key。\n\n"
                            "请让 IT 在服务器的 .env 里加一行, 然后重启网关:\n"
                            f'  {secrets_box.MASTER_KEY_ENV}=$(python3 -c "'
                            "from cryptography.fernet import Fernet; "
                            'print(Fernet.generate_key().decode())")\n\n'
                            "⚠ 这个值丢了的话, 所有存库的 API key 都解不开 —— "
                            "生成后请立刻存进密码管理器。\n\n"
                            "在那之前, 这个供应商的 key 可以先填「环境变量名」那一格。"
                        ),
                    )
                try:
                    row["api_key_enc"] = secrets_box.encrypt(plain)
                except Exception as e:
                    raise HTTPException(400, detail=str(e)) from e
            else:
                row["api_key_enc"] = None  # 显式清掉, 改回走环境变量

        # 两条来源都没有 = 这家供应商不可用。不拦 (可能是先建后配),
        # 但要在返回里说清楚, 免得管理员以为配好了。
        final_env = row["api_key_env"]
        final_enc = row.get("api_key_enc", existing.get("api_key_enc") if existing else None)
        warning = None
        if not final_env and not final_enc:
            warning = (
                "这个供应商还没有配 API key —— 用它的模型现在不可用。"
                "填「API key」那一格 (存库), 或者填「环境变量名」那一格。"
            )

        try:
            provider_store.upsert_provider(pid, row, by=user.sub)
        except Exception as e:
            raise HTTPException(500, detail=f"保存供应商失败: {e}") from e
        invalidate_config()
        return {
            "ok": True,
            "id": pid,
            "created": existing is None,
            "warning": warning,
        }

    @app.delete("/api/admin/providers/{pid}")
    async def api_admin_providers_delete(
        pid: str,
        user: User = Depends(get_current_user),
    ) -> dict[str, Any]:
        """删一个供应商. sysadmin only.

        还有模型在引用时拒绝, 并**点名是哪几个** —— 只说"删不掉"的话,
        管理员得自己一个个翻模型去找 (同 fallback 链那条拦截)。
        """
        _require(user)
        _require_store()

        used_by = provider_store.models_using(pid)
        if used_by:
            raise HTTPException(
                400,
                detail=(
                    f"供应商 {pid} 还被这些模型引用:\n"
                    # 报错里给 name (唯一标识) 而不是显示名 —— 显示名可以重复,
                    # 给重复的名字等于没给。
                    + "\n".join(
                        f"    · {m['display_name']}  ({m['name']})" for m in used_by
                    )
                    + "\n\n请先把它们改到别的供应商上, 或者删掉这些模型。\n\n"
                    "为什么要拦: 删掉之后这些模型的 upstream 会指向一个不存在的"
                    "供应商, 于是 api_base 和 key 都拿不到 —— 它们仍在列表里, "
                    "但每次调用都失败。"
                ),
            )

        try:
            deleted = provider_store.delete_provider(pid, by=user.sub)
        except Exception as e:
            raise HTTPException(500, detail=f"删除供应商失败: {e}") from e
        invalidate_config()
        return {"ok": True, "id": pid, "deleted": deleted}
