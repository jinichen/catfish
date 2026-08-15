"""网关启动接线 —— 从 app.py 的 lifespan 拆出 (8/15)。

lifespan 是 asynccontextmanager: `yield` 之前是启动、之后是关闭。这里搬走
**启动那一半** (216 行)。关闭那半留在 app.py —— 只有 47 行, 且要 await
启动起的那两个后台任务。

# 接口就是那两个任务句柄

拆之前扫过启动段: 没有 `global` 声明、没有任何 `x.y = ...` 属性写、
关闭段要用的启动段局部**只有两个** (refresh_task / archive_summary_task)。
所以 run_startup 把这两个 task 返回去, 关闭段照旧 cancel/await。

# 两个 helper 留在了 app.py (试过搬, 撤回了)

`_attach_file_handler_to_uvicorn` / `_seed_and_migrate_models` 乍看只有启动段
在用 (grep 次数少)。但前者跟 `_setup_file_logging` 共享模块级可变全局
`_FILE_HANDLER` (那边 `global _FILE_HANDLER` 写、这边读), 而且**就是被
_setup_file_logging 调用的** —— 我第一版只数了出现次数没区分调用方, 搬走之后
check_undefined_names 立刻报 15 处 NameError。
共享可变全局的两个函数不能分家, 分了就是各读各的那一份。
所以改成从 app 模块延迟取。
"""
from __future__ import annotations

import asyncio
import logging
import os

import httpx

from . import model_store, provider_store
from .config import get_config, invalidate_config, load_raw_models

# 跟 app.py 同名 —— logging.getLogger 同名返回同一对象
logger = logging.getLogger("catfish.gateway")


def _app_mod():
    """延迟取 app 模块 —— 顶层 import 会成环 (app.py import 本模块)。"""
    from . import app as _m
    return _m


# 8/15: 从 app.py 搬过来 —— 它**唯一的调用方就是下面的 run_startup**,
# 留在 app.py 只会让这里绕一次 _app_mod() 回去取。
# app.py 那边 re-export 保住 tests/test_admin_models_api.py 的
# `A._seed_and_migrate_models()` 调用路径。
def _seed_and_migrate_models() -> None:
    """启动时把 models.yaml 播种进库, 并回迁被烤死的 env 占位符.

    抽成函数是为了能测 —— 原来这段直接写在 lifespan 里, 而测试用的是
    `TestClient(app)` (不带 with), **lifespan 从来没被执行过**。于是
    "播种必须用 load_raw_models 而不是 config.models" 这条最要紧的性质
    一行防护都没有: 改回去 40 条测试照样全绿。
    """
    if not model_store.is_enabled():
        return
    try:
        # ⚠ 用 load_raw_models() 而不是 config.models —— 后者是 env 插值
        # **之后**的对象, 播进去等于把 .env 里的内网地址烤成库里的字面量。
        # 详见 config.load_raw_models 的说明 (那是 7/30 引入、当天发现的 bug)。
        raw_models = load_raw_models()
        n = model_store.seed_from_yaml(raw_models)
        if n:
            logger.info("模型配置首次播种: %d 个 (来源 models.yaml)", n)
            invalidate_config()  # 让本 worker 立刻读到库里那份

        # 一次性回迁: 把已经烤死的值换回 ${VAR}。
        # 只对 yaml 里也有的模型、且库里的值正好等于插值结果时才动 ——
        # 客户后来手填过别的地址就报出来让人确认 (见 restore_placeholders)。
        fixed, suspicious = model_store.restore_env_placeholders(raw_models)
        if fixed:
            logger.warning(
                "已把 %d 个模型里被烤死的 env 值换回 ${VAR} 占位符: %s。"
                "在此之前改 .env 对这些字段是不生效的。",
                len(fixed), ", ".join(fixed),
            )
            invalidate_config()
        # 库里撞出多个 default 时清到只剩一个。default 必须全局唯一 ——
        # default_model() 返回列表里第一个 default=True 的, 两个的话就取决于
        # 排序, 员工下次开聊用哪个模型不可预测。
        cleared = model_store.enforce_single_default()
        if cleared:
            logger.warning(
                "库里有多个默认模型, 已清掉多余的 %d 个: %s。"
                "在此之前员工用到哪个默认模型取决于排序。",
                len(cleared), ", ".join(cleared),
            )
            invalidate_config()

        # 8/1: 把老形态的模型拆成「供应商 + 引用」。幂等 —— 已经是新形态的
        # 跳过, 供应商按 (api_base, api_key_env) 去重。
        #
        # 放在这里而不是 alembic 里: 拆分要读 JSONB、去重、生成可读 id、回写,
        # 用 Python 写能逐条钉测试并注入故障验证 (见 provider_store 文件头)。
        # 跟上面两个"启动时幂等迁移"同一个套路。
        new_providers = provider_store.migrate_models_to_providers()
        if new_providers:
            logger.info(
                "已把模型拆成供应商 + 引用, 新建 %d 个供应商: %s。"
                "以后加同一家的模型不用再重敲端点和 key 变量名。",
                len(new_providers), ", ".join(new_providers),
            )
            invalidate_config()

        for mname, note in suspicious.items():
            # 换不回来的必须说出来, 不能静默跳过 —— 这个 bug 的发现路径
            # ("改了 .env 不生效") 恰恰会让"对不上"成为常态。
            logger.warning("模型 %s 的 env 占位符需要人工确认: %s", mname, note)
    except Exception:
        # 播种失败不阻塞启动 —— 此时仍能用 yaml 里的模型正常服务。
        # 但必须留日志: 否则会表现成"界面上改了模型但列表是空的"。
        logger.exception("模型配置播种失败, 本次将继续使用 models.yaml 里的模型")


async def run_startup(app, env_file_loaded):
    """跑完全部启动接线, 返回 (refresh_task, archive_summary_task)。

    env_file_loaded 就是 app.py 模块级的 _ENV_FILE_LOADED —— 它只被读不被写
    (模块级 `_ENV_FILE_LOADED = _load_dotenv()` 一次定死), 当参数传进来即可。
    """
    # ⚠ 8/13: 必须在这里**再挂一次**文件 handler 到 uvicorn 的 logger。
    #
    # 模块导入期已经挂过一次, 但 uvicorn 随后调
    # `logging.config.dictConfig(LOGGING_CONFIG)`, 而那份配置给 uvicorn.access
    # 指定了 `handlers: ["access"]` —— dictConfig **替换整个 handler 列表**,
    # 先挂上的会被抹掉。lifespan 跑的时候 dictConfig 已经完成, 这次才留得住。
    #
    # 8/13 第一版只在模块导入期挂, 重启后 gateway.log 里 HTTP/1.1 仍是 0 条 ——
    # 改了没效果, 真因就是被 dictConfig 抹了 (军规 §4.2: "改了没效果"先确认
    # 跑的是哪份代码, 这次跑的确实是新代码, 是时序问题)。
    _app_mod()._attach_file_handler_to_uvicorn()

    # 7/30: 启动时预热 + fail fast —— 配置坏了要在这里挂, 不要等第一个请求
    # 进来才 500。
    #
    # ⚠ 这里**不再往 app.state.config 塞快照**。那份快照是启动那一刻的样子、
    #   之后永不更新, 而进程里另有 6 处每次重读 yaml —— 同一个进程两套真相,
    #   改完配置"一部分代码看到新值、另一部分没看到", 且走到哪条路径是随机的。
    #   现在所有读配置的地方统一走 get_config() (带 TTL, 见 config.py)。
    config = get_config()

    # 7/30: 首次启动把 yaml 里的模型播种进库。
    #
    # 之后模型列表以库为准, yaml 不再参与 —— 否则升级包换了 yaml 会把客户
    # 现场改过的模型冲掉。yaml 从此只是"出厂默认"。
    #
    # 4 个 worker 会同时跑到这里, 靠 ON CONFLICT DO NOTHING 让重复播种变成
    # 空操作 (见 model_store.seed_from_yaml)。
    _seed_and_migrate_models()

    if env_file_loaded:
        logger.info("loaded .env from: %s", env_file_loaded)
    else:
        logger.info(".env not found -- using process env vars only")

    # P3.5.29 (6/17 鸿波) — model role 抽象层 load. config/roles.yaml 业务
    # 意图 → 物理 model name 映射, 客户改这一文件全代码跟着走.
    #
    # P3.5.29 Phase 6 (6/17 鸿波) — hard fail 改 raise: Phase 1-5 全 ship 完,
    # 全代码 (gateway / Companion / tool-bridge) 都依赖 /v1/roles. roles.yaml load
    # 失败raise 真 production 立刻挂, 好过 silent gateway 跑但 caller
    # 拿不到 role mapping (Companion email scheduler / tool-bridge fallback
    # hardcoded 也跑, 但客户改 roles.yaml 改错 真无人察觉**, production
    # 红线).
    try:
        from . import roles as roles_module
        roles_module.load_roles()
    except Exception as e:
        logger.error(
            "roles.yaml 加载失败 — production hard fail "
            "(P3.5.29 Phase 6). 客户改 yaml 真错 / 文件不存在 / 循环 ref, "
            "检查 central/llm-gateway/config/roles.yaml. 原因: %s", e,
        )
        raise

    # roles 指的模型到底存不存在 (8/14)
    #
    # load_roles() 只校验 yaml 自身自洽 (_validate_schema 的注释原话:
    # 「不能 detect 物理 name 错 (catalog 没 load), 只 catch role typo」)。
    # 也就是说 `embedding: 一个早就删掉的模型` 能顺利加载, 直到第一次请求才
    # 404 —— 而调用方 (Companion 的向量 / tool-bridge / hermes 插件) 普遍是
    # 拿不到就静默降级, 没有一处会报错。
    #
    # ## 为什么这里是 error 日志而不是 raise
    #
    # 上面 load_roles 失败是 hard fail, 但那是"yaml 坏了", 跟这里不同类。
    # 模型列表可能来自库 (model_store), 而 config.py:578 写明: 冷启动时库不可用
    # 会**降级到 models.yaml**。那种时刻只在库里的模型全都"不存在" —— 这时候
    # raise 等于把一次库抖动变成网关起不来。
    #
    # 真正该拦的位置是"制造 stale 的那一刻": 控制台删模型时 (admin_models_router
    # 的 roles_referencing 检查)。这里只负责让已经 stale 的状态**看得见**,
    # 另外 /api/admin/models 的 role_errors 会把它显示在界面上。
    try:
        _known = {m.name for m in get_config().models}
        _stale = roles_module.stale_model_refs(_known)
        if _stale:
            logger.error(
                "⚠️ roles.yaml 里这些角色指向不存在的模型: %s。"
                "用到它们的请求会拿到 404 model not found, 而调用方普遍静默降级 "
                "(Companion 的向量会退回本机 ONNX, Windows 客户端则完全没有向量)。"
                "当前模型列表: %s",
                ", ".join(f"{r} → {m}" for r, m in sorted(_stale.items())),
                ", ".join(sorted(_known)),
            )
    except Exception:  # noqa: BLE001
        # 这只是一条诊断, 不能因为它把启动搞挂
        logger.exception("roles ↔ 模型列表一致性检查本身出错 (不影响启动)")

    env_mode = os.environ.get("CATFISH_ENV", "dev")
    dev_token = os.environ.get("CATFISH_DEV_TOKEN", "dev-token-local")
    # Only show a fingerprint of the token, not the value itself
    token_preview = dev_token[:4] + "..." + dev_token[-4:] if len(dev_token) > 8 else "<short>"
    logger.info("mode=%s  dev_token_preview=%s", env_mode, token_preview)

    # BL-CENTRAL-EDGE-BOUNDARY + BL-QUOTA-SQLITE-DEPRECATE (5/17 鸿波):
    # 生产必配 CATFISH_DB_URL (PG). 没配 → 启动报警, 防 audit 数据静默丢 / 写
    # 员工本机. 单测走 CATFISH_QUOTA_DB env override (test fixture 设).
    from .quota import _audit_backend_configured  # noqa: PLC0415
    if not _audit_backend_configured():
        if env_mode == "prod":
            logger.error(
                "❌ CATFISH_DB_URL 未配, 但 CATFISH_ENV=prod — audit 数据将静默丢失. "
                "中央端必须配 PG (BL-CENTRAL-EDGE-BOUNDARY 规则: 不允许写员工本机 "
                "sqlite 兜底). 立刻配 CATFISH_DB_URL 重启."
            )
        else:
            logger.warning(
                "⚠️ CATFISH_DB_URL 未配 (CATFISH_ENV=%s) — audit 写会被丢. "
                "dev 没问题, 但要测 quota / audit 功能时该配本地 PG 或单测设 "
                "CATFISH_QUOTA_DB env.", env_mode,
            )

    # BL-FIX37 (5/10): gateway 内部 loopback (proactive_starter / session_summarizer)
    # 用 internal-only token 调自己 /v1/chat/completions, 修 BL-FIX29 关掉员工
    # dev_token 后内部调用 401 的副作用. 没显式配 → 自动生成 32B random.
    from .auth.dev_token import ensure_internal_dev_token  # noqa: PLC0415
    ensure_internal_dev_token()

    # BL-GATEWAY-MEMORY-REGISTRY-DELETE (5/20): memory_registry 整套删除.
    # 5/19 BL-MEMORY-OWNERSHIP-FIX Phase 2-3 已 disable provider 注册.
    # 5/20 Day 2 catfish-memory plugin (hermes 侧) 接管 5 provider 的 prefetch 路径.
    # 5/20 audit verdict: memory/ 整目录 (~1387 LOC) 是 no-op 死代码, 跟 caller 一起删.

    logger.info("catfish-gateway starting with %d model(s):", len(config.models))
    for m in config.models:
        logger.info(
            "  - %s [%s/%s] -> %s",
            m.name,
            m.tier,
            m.mode,
            m.upstream.api_base,
        )

    # 上游可达性自检：员工启动后立刻知道现在能调哪些 LLM，
    # 不必等到第一次对话时被 60s 超时教育。
    # 顺便把结果缓存到 app.state，/v1/catalog 直接读，避免每次列模型都探活。
    from .network import report_upstream_reachability  # noqa: PLC0415
    app.state.upstream_status = report_upstream_reachability(config.models)

    # 后台定期重探(60s 间隔), 让 catalog 反映 VPN 起停 / Clash 起停 / 服务器恢复等。
    # silent=True 不打 banner 免得日志被刷屏, 只用 logger.info 记一行变化。
    async def _refresh_loop() -> None:
        prev_reachable = {
            n: bool(s.get("reachable"))
            for n, s in app.state.upstream_status.items()
        }
        while True:
            try:
                await asyncio.sleep(60)
                new_status = report_upstream_reachability(config.models, silent=True)
                app.state.upstream_status = new_status

                # 只有"可达性变化"时打日志,避免每分钟刷一行噪音
                changes = []
                for name, s in new_status.items():
                    now_reach = bool(s.get("reachable"))
                    if prev_reachable.get(name) != now_reach:
                        arrow = "✓→" if now_reach else "✗→"
                        changes.append(f"{name}: {arrow} {s.get('reason')}")
                        prev_reachable[name] = now_reach
                if changes:
                    logger.info(
                        "upstream reachability changed: %s",
                        " | ".join(changes),
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("upstream refresh loop error (continuing)")

    refresh_task = asyncio.create_task(_refresh_loop())

    # BL-D3 (5/9) Phase 1 收尾: mcp-registry 反向代理用的 httpx client.
    # 长 lifecycle 单例, 复用连接池. lifespan close 时 aclose. 跑在 gateway
    # 同 process 不另起服务 — Companion 走 gateway 单 origin /v1/mcp/*.
    app.state.mcp_registry_client = httpx.AsyncClient(
        timeout=config.mcp_registry.timeout,
    )

    # BL-D2 (5/10): skills-hub 反代 httpx client, 同模式
    app.state.skills_hub_client = httpx.AsyncClient(
        timeout=config.skills_hub.timeout,
    )

    # P3.3.18 (6/10): wiki-hub 反代 httpx client, 同模式
    app.state.wiki_hub_client = httpx.AsyncClient(
        timeout=config.wiki_hub.timeout,
    )

    # A2A self_register 5/26 砍 — Plan D Federation 整套停 (0 真客户用, 详见
    # docs/HERMES-013-ALIGN.md A2A 段). 不再调 self_register.

    # BL-HERMES013-4 (5/12 鸿波拍板): 启动时 reap 上次崩前没流完的 in-flight stream.
    # 写一条 'interrupted_resumed' audit 留痕迹, 然后 unlink 文件 (防累积).
    # 失败不阻塞启动 (audit 写不成也只是少一条记录, 文件总会被清).
    try:
        from . import inflight_streams  # noqa: PLC0415
        reaped = inflight_streams.reap_interrupted()
        if reaped > 0:
            logger.info(
                "BL-HERMES013-4: 启动 reap 清掉 %d 个 interrupted in-flight stream",
                reaped,
            )
    except Exception as e:
        logger.warning("BL-HERMES013-4 reap_interrupted 失败 (静默): %s", e)

    # 6/7 BL-CATFISH-MANIFESTO clean-up: tool message archive summary_worker
    # 整套停. 历史:
    #   - 5/11 BL-Q3-ARCHIVE v1: gateway 端写 PG tool_archives 表 + haiku 摘要
    #   - 5/22 BL-CENTRAL-EDGE-TOOL-ARCHIVE Phase 6a: gateway PG archive 强制
    #     停, edge 端 (tool-bridge tool_archive_local.py) 接管 archive 写读
    #   - 5/26 BL-BOUNDARY: db.py 砍 PG 路径死代码, content 全员工本机
    #   - 6/7 (现在): summary_worker 没新 archive 进 PG 可摘要了, 跑空, 整套停
    # summary_worker 死代码留 git history, 后续 BL-MANIFESTO-CLEAN-DEAD 整套 rm.
    archive_summary_task = None
    return refresh_task, archive_summary_task
