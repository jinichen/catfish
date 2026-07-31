"""模型配置管理接口 (7/30, 8/1 按 CLAUDE.md 军规 §1 从 app.py 拆出).

app.py 是 3838 行 —— 红线的 4.8 倍。这一轮往里加了 141 行 (模型 CRUD、
api_key_env 校验、播种回迁), 而军规第 2 条要求"改任何 .py 文件后都跑一遍
check_file_sizes.sh", 这两天一次都没跑。

先把**这一轮加进去的**整块搬出来止血。app.py 本身仍然远超红线, 需要一轮
专门的拆分 (那是另一件事, 混进功能改动里 review 不清)。

## 这几个接口在做什么

在这之前模型只能改 models.yaml 再重启 gateway, 而那个文件在容器里是只读
挂载, 所以"在界面上改模型"这条路根本不存在。现在模型存库 (见 model_store),
这几个接口是它的读写口。

只给 sysadmin —— 改模型影响全员, 且 upstream 里带 api_key_env / api_base
这类部署细节, 不该让普通员工看到。注意跟匿名的 /v1/catalog 区分: 那个只返
展示用的字段, 这里返完整配置。

每个写操作末尾都 invalidate_config(), 让**本 worker** 立刻生效; 其余 3 个
worker 靠 TTL 收敛 (默认 3s, 见 config.py 里为什么不做跨进程失效)。

## 军规 §3 (re-export)

app.py 顶部 `from .admin_models_router import *` 不可行 (FastAPI 路由要挂在
同一个 app 上)。这里改用 `register(app)` 显式注册, 而 app.py 侧保留
`from .admin_models_router import register_model_admin_routes` —— 老的
`from .app import _check_api_key_env` 这类测试 monkeypatch 路径同样在
app.py 顶部 re-export (军规 §3 第 3 条: 测试 monkeypatch 路径要跟着搬)。
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from fastapi import Depends, FastAPI, HTTPException

from . import model_store, provider_store
from .auth import User, get_current_user
from .config import (
    Config,
    ModelConfig,
    get_config,
    invalidate_config,
    load_raw_models,
    model_config_errors,
)

logger = logging.getLogger(__name__)


def register_model_admin_routes(app: FastAPI) -> None:
    """把这一组接口挂到 app 上. app.py 建完 app 之后调一次."""

    # ── 模型配置管理 (7/30) ──────────────────────────────────────────────
    #
    # 在这之前模型只能改 models.yaml 再重启 gateway, 而那个文件在容器里是只读
    # 挂载, 所以"在界面上改模型"这条路根本不存在。现在模型存库 (见 model_store),
    # 这几个接口是它的读写口。
    #
    # 只给 sysadmin —— 改模型影响全员, 且 upstream 里带 api_key_env / api_base
    # 这类部署细节, 不该让普通员工看到。注意跟匿名的 /v1/catalog 区分: 那个只返
    # 展示用的字段, 这里返完整配置。
    #
    # 每个写操作末尾都 invalidate_config(), 让**本 worker** 立刻生效; 其余 3 个
    # worker 靠 TTL 收敛 (默认 3s, 见 config.py 里为什么不做跨进程失效)。


    def _require_model_admin(user: User) -> None:
        if user.role != "sysadmin":
            raise HTTPException(
                status_code=403,
                detail=f"role={user.role} 不能编辑模型配置 (sysadmin only)",
            )


    def _effective_auto_fallback(cfg: Config) -> bool:
        """fallback 到底开没开. 跟 fallback.py:426 的判定保持一致.

        两个来源, env 优先:
            CATFISH_AUTO_FALLBACK=1/true/yes
            models.yaml 顶层 auto_fallback: true

        **默认是关的** (BL-FALLBACK-TOGGLE 2026-05-16)。这件事必须告诉界面 ——
        否则管理员会认真配一条 fallback 链, 而它根本不会执行。配置了不生效
        且没有任何提示, 是今晚反复出现的那类问题。
        """
        return (
            os.environ.get("CATFISH_AUTO_FALLBACK", "").lower() in ("1", "true", "yes")
            or bool(getattr(cfg, "auto_fallback", False))
        )


    # 合法的环境变量名。POSIX 就是这个字符集, 而真 API key 基本都含 `-` 或小写
    # (sk-xxx / AIza... / 长 base64), 所以这一条同时挡住了"把真 key 粘进来"。
    _ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


    def _check_api_key_env(m: ModelConfig) -> None:
        """api_key_env 填的必须是**变量名**, 不是 key 本身.

        这一格是整个表单里最容易填错的 —— 名字里带 "API Key" 三个字, 而旁边
        「API 地址」那格填的又确实是值本身。粘进一个真 key 的后果是它**明文写进
        数据库**, 也就进 pg_dump、进备份、进任何一次 `SELECT payload` ——
        而这个字段之所以存在, 就是为了让 key 永远不进配置。

        前端也校验了, 但那道能被绕过 (直接 PUT)。真正兜住的是这一道。
        """
        v = (m.upstream.api_key_env or "").strip()
        if _ENV_NAME_RE.match(v) and len(v) <= 64:
            return
        raise HTTPException(
            400,
            detail=(
                "「API Key 环境变量名」这一格填的是**变量名**, 不是 key 本身。\n\n"
                f"你填的是: {v[:12]}{'…' if len(v) > 12 else ''}\n"
                "合法的变量名只能用字母、数字、下划线, 且不能以数字开头, "
                "例如 DASHSCOPE_API_KEY。\n\n"
                "key 本身请让 IT 放到服务器的 .env 里 —— 它不进数据库, 也就不会"
                "出现在备份和数据库导出里。"
            ),
        )


    def _check_provider_ref(m: ModelConfig) -> None:
        """引用的供应商必须存在. 8/1.

        不拦的话模型能存进去, 但它的 api_base 和 key 都拿不到 —— 仍在列表里,
        每次调用都失败, 而配置上只能看到一个 provider 名字。

        跟 fallback 链那条一样: 拦下来时要**给出可选项**, 否则管理员得自己
        去猜供应商叫什么。
        """
        pid = m.upstream.provider
        if not pid:
            return  # 老形态 (upstream 里直接写 api_base/api_key_env)
        known = provider_store.read_providers() or {}
        if pid in known:
            return
        raise HTTPException(
            400,
            detail=(
                f"供应商 {pid!r} 不存在。\n\n"
                + (
                    "现有的供应商:\n" + "\n".join(f"    · {k}" for k in sorted(known))
                    if known
                    else "现在一个供应商都还没有 —— 先去「接入 → 供应商」新建一个。"
                )
                + "\n\n为什么要拦: 存进去的话这个模型的端点和 key 都拿不到, "
                "它仍然出现在列表里但每次调用都失败。"
            ),
        )


    def _check_fallback_500_policy(m: ModelConfig) -> None:
        """内网模型的 on_errors 不许含 500 —— 保密红线, 不是风格问题.

        内网 500 触发 fallback → 内网 prompt 落到公网模型 = 内网内容出公司,
        违反 SOUL_FFCS 国央企保密原则。

        ## 为什么必须在接口上拦, 不能只靠测试

        tests/test_fallback_500_policy.py 读的是 config/models.yaml —— 而模型
        改成可在界面上增删改之后, **库里的模型完全不在那个测试的视野内**。
        通过界面加一个内网模型、on_errors 填 500, 没有任何测试会红。

        公网链缺 500 是另一回事 (影响体验不影响保密), 那个只警告不拦 ——
        见 GET 返回里的 warnings。
        """
        if m.tier != "private" or not m.fallback:
            return
        if 500 in (m.fallback.on_errors or []):
            raise HTTPException(
                400,
                detail=(
                    f"内网模型 {m.name} 的失败切换条件不能包含 500。\n"
                    "内网返 500 就切公网, 等于内网 prompt 出公司 —— 违反保密要求。\n"
                    "公网模型之间切换没有这个问题, 可以含 500。"
                ),
            )


    def _model_store_error(e: Exception) -> HTTPException:
        """把库操作的异常翻译成运维**看得懂、能动手**的错误 (7/30).

        为什么要专门做这件事: 写接口原本让异常直接往外抛, FastAPI 兜成
        "HTTP 500: Internal Server Error"。管理界面上就显示这一行 —— 对着屏幕的
        人完全不知道发生了什么, 更不知道下一步该做什么。真正的原因埋在 gateway
        日志里, 而点保存的人未必有服务器日志权限。

        读接口 (read_models / revision) 已经是吞异常 + 记日志 + 降级, 那条路不会
        走到这里; 写接口不能吞 —— 吞了就是"显示保存成功但什么都没写", 比报错糟。
        所以是"抛, 但把话说清楚"。

        最常见的一种单独识别: 迁移没跑, 表不存在。这在本机 dev 和刚升级的环境
        上都很容易发生, 而错误原文 (relation "gateway_models" does not exist)
        对不熟悉这块的人不构成行动指引。
        """
        msg = str(e)
        if "gateway_models" in msg or "gateway_config_meta" in msg:
            if "does not exist" in msg or "UndefinedTable" in type(e).__name__:
                return HTTPException(
                    500,
                    detail=(
                        "模型配置表不存在 —— 数据库迁移还没跑过。\n"
                        "在 gateway 所在环境执行:\n"
                        "    alembic upgrade head\n"
                        "然后重启 gateway 让它播种出厂模型。\n\n"
                        f"原始错误: {msg}"
                    ),
                )
        return HTTPException(500, detail=f"写模型配置失败: {msg}")


    def _require_model_store() -> None:
        """没配库时明确拒绝, 而不是假装成功.

        这条很重要: 库没启用时写操作无处可去, 如果静默返回 ok, 界面上会显示
        "保存成功"但什么都没发生 —— 那比报错难查得多。
        """
        if not model_store.is_enabled():
            raise HTTPException(
                status_code=503,
                detail=(
                    "模型配置库未启用 (未配置 CATFISH_DB_URL), 无法修改。"
                    "此时模型来自 models.yaml, 只能改文件后重启。"
                ),
            )


    @app.get("/api/admin/models")
    async def api_admin_models_list(
        user: User = Depends(get_current_user),
    ) -> dict[str, Any]:
        """完整模型配置 (含 upstream). sysadmin only.

        ⚠ 返回的是**库里的原样**, 不是 cfg.models。

        cfg.models 是插值后的运行时配置 (${VAR} 已经换成真实地址)。而界面拿到
        什么就会在保存时原样传回来 —— 返回插值后的值, 等于管理员随便编辑一次
        就把占位符烤成了字面量, 把 7/30 那个 bug 当场撤销。

        连带的: 只有返回原样, 界面上"这里是环境变量占位符, 改成写死的地址就
        不跟着 .env 走了"那条提示才可能出现。返回插值后的值时它永远不匹配。
        """
        _require_model_admin(user)
        cfg = get_config()
        rows = model_store.read_models()
        if rows is None:
            # 库没启用 / 不可用 → 用 yaml 原文 (同样保留占位符)。
            # 这条路径是只读的 (editable=False), 只用于展示。
            try:
                rows = load_raw_models()
            except Exception:
                logger.exception("读 models.yaml 原文失败, 退回运行时配置 (占位符会显示成真实值)")
                rows = [m.model_dump(mode="json") for m in cfg.models]
        return {
            "ok": True,
            "editable": model_store.is_enabled(),
            "revision": model_store.revision(),
            # 失败切换全局开关。**默认是关的** —— 界面必须显示这个, 否则管理员
            # 会认真配一条 fallback 链而它根本不执行。
            "auto_fallback": _effective_auto_fallback(cfg),
            # 哪些模型的 env 占位符没解析成功。不显示的话, "这个模型为什么不工作"
            # 在界面上没有任何线索 —— 只有服务器日志里有一行。
            "config_errors": model_config_errors(),
            # 每个模型引用的那个 key 变量, 在服务器上到底设没设。
            #
            # 这是管理员问得最多的那个问题 ——「我把变量名填进去了, 生效了吗」。
            # 答案不在数据库里, 而在服务器的 .env 里, 界面本来完全看不到。
            # 之前只能等员工调用失败才发现。
            # 只返 True/False, **不返 key 的任何内容**。
            "api_key_configured": {
                m.name: m.upstream.is_available for m in cfg.models
            },
            "models": rows,
        }


    @app.put("/api/admin/models/{name}")
    async def api_admin_models_put(
        name: str,
        body: dict[str, Any],
        user: User = Depends(get_current_user),
    ) -> dict[str, Any]:
        """新增或修改一个模型. sysadmin only."""
        _require_model_admin(user)
        _require_model_store()

        # 路径里的 name 是权威的 —— body 里写了别的名字就是搞错了, 直接拒绝,
        # 不要"以路径为准"地悄悄改掉。悄悄改会造成"我明明改了 A 却动了 B"。
        body_name = body.get("name")
        if body_name is not None and body_name != name:
            raise HTTPException(
                400, detail=f"路径里的模型名 {name!r} 跟 body 里的 {body_name!r} 不一致"
            )
        body = {**body, "name": name}

        # 用跟启动时同一个 pydantic 模型校验 —— 库里存的必须是能被 gateway 正常
        # 加载的东西。这里放过去的话, 下次重载配置时整个 gateway 都起不来。
        try:
            validated = ModelConfig.model_validate(body)
        except Exception as e:
            raise HTTPException(400, detail=f"模型配置不合法: {e}") from e

        # 三道拦截, 都是"存进去也能存, 但存了之后会静默出问题"那一类:
        #   api_key_env  粘了真 key → 明文写进数据库
        #   provider     引用不存在的供应商 → 模型在列表里但每次调用都失败
        #   fallback 500 内网模型因 500 切公网 → 内网内容出公司 (保密红线)
        #
        # 保密那条必须在这里拦 —— test_fallback_500_policy 只看 models.yaml,
        # 库里的模型不在它视野内。
        _check_api_key_env(validated)
        _check_provider_ref(validated)
        _check_fallback_500_policy(validated)

        cfg = get_config()
        existing = {m.name for m in cfg.models}

        # default 是全局唯一的 —— 两个 default 时 Config.default_model 返回的是
        # 列表里第一个, 也就是"取决于排序", 不可预测。设新 default 时清掉旧的。
        #
        # ⚠ 这里必须遍历**库里的**模型, 不能用 cfg.models。
        #   cfg 是"当前生效配置", 库还空着的时候它等于 yaml 那份 —— 拿它来清
        #   default, 会把整个 yaml 模型列表当成"旧 default"逐个写进库, 于是一次
        #   普通的"设为默认"变成了"把出厂配置全量导入"。
        #   7/30 被 test_删掉默认模型会自动指定新默认 逮到: 库空时 PUT 一个
        #   default=True 的模型, 库里凭空多出 7 个 yaml 模型。
        try:
            if validated.default:
                for row in model_store.read_models() or []:
                    if row.get("name") != name and row.get("default"):
                        model_store.upsert_model(
                            row["name"], {**row, "default": False}, by=user.sub
                        )

            model_store.upsert_model(name, validated.model_dump(mode="json"), by=user.sub)
        except HTTPException:
            raise
        except Exception as e:
            raise _model_store_error(e) from e
        invalidate_config()
        return {
            "ok": True,
            "name": name,
            "created": name not in existing,
            "revision": model_store.revision(),
        }


    @app.delete("/api/admin/models/{name}")
    async def api_admin_models_delete(
        name: str,
        user: User = Depends(get_current_user),
    ) -> dict[str, Any]:
        """删一个模型. sysadmin only.

        有两道拦截, 都是为了不让一次误操作把服务打瘫:
          · 不许删到一个不剩 —— 没有模型的 gateway 无法服务任何聊天请求
          · 不许删掉还被别的模型 fallback.chain 引用的 —— 那条链会在运行时
            指向一个不存在的模型, 而 fallback 只在上游出错时才走, 平时看不出来
        """
        _require_model_admin(user)
        _require_model_store()

        cfg = get_config()
        if not any(m.name == name for m in cfg.models):
            return {"ok": True, "name": name, "deleted": False}  # 幂等

        if len(cfg.models) <= 1:
            raise HTTPException(
                400, detail="这是最后一个模型, 删了 gateway 无法服务任何请求"
            )

        referenced_by = [
            m.name
            for m in cfg.models
            if m.name != name and m.fallback and name in (m.fallback.chain or [])
        ]
        if referenced_by:
            raise HTTPException(
                400,
                detail=(
                    f"模型 {name} 还被这些模型的失败切换链引用:\n"
                    + "\n".join(f"    · {r}" for r in referenced_by)
                    + "\n\n"
                    "请先逐个编辑它们、在「失败切换」里移除这一跳, 然后再删。\n\n"
                    "为什么要拦: 链里指向不存在的模型时, gateway 只会记一行日志然后"
                    "跳过那一跳 —— 而失败切换只在上游出错时才走, 平时完全看不出来, "
                    "等真出故障那天才发现兜底少了一环。"
                ),
            )

        # ⚠ 下面一律用**库里的原样行**, 不用 cfg.models。
        #   cfg.models 是插值后的 (${VAR} 已经换成真实地址), 拿它 model_dump 再
        #   写回库, 等于把占位符烤成字面量 —— 正是 7/30 修掉的那个 bug。
        rows = model_store.read_models() or []
        was_default = any(r.get("name") == name and r.get("default") for r in rows)

        # 删完之后还有没有可用的对话模型?
        #
        # ⚠ 判据是"删完还剩不剩", **不是"删的是不是默认模型"**。
        #   只在 was_default 时把关的话, 同一个洞从旁边就能走进去:
        #     [chatA(非默认), embedB(默认)]  删 chatA → 剩一个 embedding 挂着"默认"
        #     [chatC, embedD]  两个都没标默认 → 删 chatC 后 default_model() 退化成
        #                      models[0] = embedD
        #   两种终局一模一样: 全公司默认对话模型是个向量模型, 员工一开口就报错,
        #   而界面上完全看不出哪里不对。
        #
        # mode 缺省是 "chat" (config.ModelConfig), 所以老数据不会被误排除。
        chat_rest = [
            r for r in rows if r.get("name") != name and (r.get("mode") or "chat") == "chat"
        ]
        is_chat = any(
            r.get("name") == name and (r.get("mode") or "chat") == "chat" for r in rows
        )
        if is_chat and not chat_rest:
            raise HTTPException(
                400,
                detail=(
                    f"删掉 {name} 之后就没有可用的对话模型了。\n\n"
                    "剩下的要么是向量模型 (mode=embedding), 要么一个都不剩 —— "
                    "两种情况下员工一开口都会直接报错, 而「模型」页上看不出哪里不对。\n\n"
                    "请先新增一个对话模型, 再回来删这个。"
                ),
            )

        try:
            deleted = model_store.delete_model(name, by=user.sub)
        except Exception as e:
            raise _model_store_error(e) from e

        # 删掉的是默认模型, 或者删完之后没有任何模型挂着"默认" → 立刻指定一个。
        # 否则 default_model() 退化成"列表第一个", 也就是取决于排序, 员工下次
        # 开聊用到哪个模型不可预测。
        promoted = None
        needs_heir = deleted and (was_default or not any(r.get("default") for r in chat_rest))
        if needs_heir and chat_rest:
            heir = chat_rest[0]
            try:
                model_store.upsert_model(
                    heir["name"], {**heir, "default": True}, by=f"{user.sub} (自动接任默认)"
                )
                promoted = heir["name"]
            except Exception as e:
                # 模型已经删了但新默认没指定成功 —— 这个状态必须说出来,
                # 否则 default_model() 会退化成"列表第一个"而没人知道。
                raise _model_store_error(e) from e

        invalidate_config()
        return {
            "ok": True,
            "name": name,
            "deleted": deleted,
            "promoted_default": promoted,
            "revision": model_store.revision(),
        }


    @app.put("/api/admin/model-order")
    async def api_admin_models_order(
        body: dict[str, Any],
        user: User = Depends(get_current_user),
    ) -> dict[str, Any]:
        """重排模型展示顺序. body: {"names": [...]}. sysadmin only.

        ⚠ 路径故意**不放在 /api/admin/models/ 下面**。
          放成 /api/admin/models/_order 的话会被上面的 /api/admin/models/{name}
          抢先匹配 (FastAPI 按注册顺序匹配路由), 变成"修改一个名叫 _order 的模型"。
          靠"把它注册在 {name} 之前"也能绕开, 但那样这条路由的正确性就依赖于
          代码里的先后位置 —— 哪天有人整理顺序就会静默失效。用一个不可能撞的
          路径, 跟位置无关。
        """
        _require_model_admin(user)
        _require_model_store()
        names = body.get("names")
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            raise HTTPException(400, detail="body 需要 {\"names\": [模型名, ...]}")
        try:
            model_store.set_order(names, by=user.sub)
        except Exception as e:
            raise _model_store_error(e) from e
        invalidate_config()
        return {"ok": True, "revision": model_store.revision()}

