"""agent / model 运行时 patch —— 从 plugin.py 拆出 (8/15 第 2 趟)。

P1 (agent_init) · P3 (auxiliary_client) · P5/P6/P11 (_create_agent +
picker model override) · P10 (apply_client_headers) · P23 (inbound picker 联动)。

共同点: 都在决定"这次请求用哪个模型、带什么 header"。
三条 ContextVar 从 plugin_ctx 拿 —— 它是基座, 不反向依赖任何人。
"""
from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import urlparse
import os

# 跟 plugin.py 同名 —— logging.getLogger 同名返回同一对象
logger = logging.getLogger("catfish.xcatfish_user.plugin")


def _sib(name):
    """延迟取兄弟模块。

    # ⚠ 8/15 晚修的 bug —— 这个函数原来是坏的, 而且坏得很安静

    早上把 plugin.py 从 3226 行拆开时, 我写了这个 helper, body 是一行:

        from plugin import _import_sibling      # ← 裸 absolute import

    **那正是这个插件不能用的写法。** 目录名带 dash (`catfish-xcatfish-user`),
    hermes 用 `spec_from_file_location` + `submodule_search_locations` 加载,
    模块在 sys.modules 里叫 `catfish-xcatfish-user.plugin`, 没有叫 `plugin` 的。

    讽刺的是 plugin.py 里的 `_import_sibling` 有三段 fallback, 存在的理由就是
    这个 (5/28 那次"装好 9 天没工作"之后加的) —— 而我写的这个 helper, 名义上是
    "复用那三段", 实际用了三段要绕开的那一段。

    ## 为什么拖到晚上才发现

    hermes 进程从拆分之前就一直跑着, Python 把老模块缓存在内存里。8/15 18:03
    重启 gateway 之后才第一次加载新拆的模块, 日志里立刻冒出 4 条:

        P23 inbound: picker ... failed: No module named 'plugin'
        P1 post-init apply_headers failed: No module named 'plugin'
        P6/P11 _create_agent post-init failed: No module named 'plugin'

    三处都被 `except Exception` 包着, 只 warning 不抛 —— 员工看不出任何异常,
    只是 header 注入、picker 模型覆盖这些悄悄不干活了。

    单测也没抓住: 测试里 `sys.path.insert(0, PLUGIN_DIR)` 之后裸 import 是通的,
    生产的加载方式不通。**判据比真事窄**, 今天栽的第 N 次。

    # 现在的写法

    段 1 走相对 import —— 跟 plugin.py 的 `_import_sibling` 段 1 是**同一条路**,
    所以命中的是 sys.modules 里同一个 module 对象, 不会造出第二份
    (双 module 对象那个病今天在 catfish-memory 上专门防过)。

    段 2/3 保留原来的路径, 兜住 `__package__` 没设好的加载方式。
    """
    from importlib import import_module

    # 段 1: 相对 —— 生产上走的就是这条 (hermes 给了 submodule_search_locations)
    if __package__:
        try:
            return import_module(f".{name}", package=__package__)
        except (ImportError, SystemError, ValueError, TypeError):
            pass

    # 段 2: plugin 的三段 fallback (它自己能被裸 import 到时才通)
    try:
        from plugin import _import_sibling
        return _import_sibling(name)
    except ImportError:
        pass

    # 段 3: 按文件路径兜底 (跟 plugin._import_sibling 段 3 同款)
    import importlib.util
    import sys as _sys
    from pathlib import Path

    _py = Path(__file__).parent / f"{name}.py"
    if not _py.exists():
        raise ImportError(f"{name}.py 不存在: {_py}")
    _modname = f"_catfish_xcatfish_user_{name}"
    if _modname in _sys.modules:          # 防重复 exec 出第二个 module 对象
        return _sys.modules[_modname]
    _spec = importlib.util.spec_from_file_location(_modname, _py)
    if not _spec or not _spec.loader:
        raise ImportError(f"spec_from_file_location 失败: {_py}")
    _mod = importlib.util.module_from_spec(_spec)
    _sys.modules[_modname] = _mod
    _spec.loader.exec_module(_mod)
    return _mod


def _resolver():
    return _sib("resolver")


def _model_authority():
    return _sib("model_authority")


def _ctx():
    return _sib("plugin_ctx")














# ── P29 (6/5 鸿波) — gateway URL detection helper ─────────────────────────
#
# 商用部署改 IP 时 base_url 会变 (e.g. http://10.10.40.50:8999), 之前 P3/P10
# 硬编码 "localhost:8999" / "127.0.0.1:8999" 字符串匹配 → 中央部署检测不到 →
# X-Catfish-User 跨员工 header 不注入 → 跨员工数据 P0 风险.
#
# helper: 走 env CATFISH_GATEWAY_URL (跟 Companion / catfish-memory 同名) 解析
# host:port → 跟 base_url host:port 比. 兼容老硬编码 default (localhost:8999).
# host + port 都 match 才算同一 gateway (防别人也起 8999 端口被误判).

def _get_catfish_gateway_host_port() -> set[tuple[str, int]]:
    """返当前 catfish-gateway `(host, port)` 真集合 (兼容多默认).

    包括: env CATFISH_GATEWAY_URL 解析出 + 老硬编码默认 (localhost:8999 +
    127.0.0.1:8999). 多 default 防员工只设 host 不设 port 时漏配.
    """
    hosts_ports: set[tuple[str, int]] = {
        ("localhost", 8999),
        ("127.0.0.1", 8999),
    }
    env_url = os.environ.get("CATFISH_GATEWAY_URL", "").strip()
    if env_url:
        try:
            parsed = urlparse(env_url if "://" in env_url else f"http://{env_url}")
            if parsed.hostname and parsed.port:
                hosts_ports.add((parsed.hostname.lower(), parsed.port))
        except Exception:  # noqa: BLE001
            pass
    return hosts_ports




def _is_catfish_gateway_base_url(base_url: str) -> bool:
    """base_url 真是不是 catfish-gateway (P3 / P10 `X-Catfish-User` header 注入判断).

    走 _get_catfish_gateway_host_port() 真 set, 比 host + port. 失败 fallback
    走老 "localhost:8999 / 127.0.0.1:8999" 字符串匹配 (兼容性).
    """
    if not base_url:
        return False
    try:
        parsed = urlparse(base_url if "://" in base_url else f"http://{base_url}")
        if parsed.hostname and parsed.port:
            return (parsed.hostname.lower(), parsed.port) in _get_catfish_gateway_host_port()
    except Exception:  # noqa: BLE001
        pass
    # fallback: 字符串包含 (老硬编码兼容, 中央部署不 work 但至少 dev 仍 work)
    bu = base_url.lower()
    return "localhost:8999" in bu or "127.0.0.1:8999" in bu




# ── P1 ───────────────────────────────────────────────────────────────────

def _patch_p1_agent_init() -> None:
    """agent_init.init_agent 跑完 → apply_headers + 重建 client (拿到 X-Catfish-User)."""
    from agent import agent_init

    _orig = agent_init.init_agent

    def patched_init_agent(agent: Any, **kwargs: Any) -> Any:
        result = _orig(agent, **kwargs)
        try:
            if hasattr(agent, "_apply_client_headers_for_base_url"):
                agent._apply_client_headers_for_base_url(str(getattr(agent, "base_url", "") or ""))
            if hasattr(agent, "_replace_primary_openai_client"):
                agent._replace_primary_openai_client(reason="catfish_xcatfish_user_inject")
        except Exception as e:
            logger.warning("P1 post-init apply_headers failed: %s", e)
        return result

    agent_init.init_agent = patched_init_agent




# ── P3 ───────────────────────────────────────────────────────────────────

def _patch_p3_auxiliary_client() -> None:
    """
    P3a: _MAIN_RUNTIME_FIELDS 加 catfish_outgoing_user — _normalize_main_runtime
         和 _client_cache_key 都是 runtime lookup, module 属性赋新 tuple 即生效.

    P3b: _resolve_auto wrap — return 前 rebuild OpenAI/AsyncOpenAI client
         带 default_headers={"X-Catfish-User": cf_user}.
    """
    from agent import auxiliary_client as aux

    # P47: 私有模型的 auxiliary 失败后只报错，不允许 Hermes 自己降级到公网。
    _sib("private_auxiliary_guard").patch(aux)

    # P3a
    if "catfish_outgoing_user" not in aux._MAIN_RUNTIME_FIELDS:
        aux._MAIN_RUNTIME_FIELDS = aux._MAIN_RUNTIME_FIELDS + ("catfish_outgoing_user",)

    # P3b
    _orig_resolve = aux._resolve_auto

    def patched_resolve_auto(main_runtime=None, task=None):
        # P3.5.88 (6/23 鸿波 catch context_compressor TypeError): hermes upstream
        # _resolve_auto 升级签名加 task=None 参数 (用于 task-specific aux routing).
        # P3 老 signature 单 main_runtime 撞 TypeError → context compression fail →
        # Companion 报 Load failed. 补 task=None + forward 给 _orig.
        client, model = _orig_resolve(main_runtime=main_runtime, task=task)
        try:
            if client is None:
                return client, model
            runtime = aux._normalize_main_runtime(main_runtime)
            cf_user = (runtime.get("catfish_outgoing_user") or "").strip()
            if not cf_user:
                return client, model
            base_url = str(getattr(client, "base_url", "") or "")
            # P29 (6/5): 走 _is_catfish_gateway_base_url 真 env-aware 检测,
            # 不再硬编码 localhost:8999 字符串 (中央部署 IP 会变)真.
            if not _is_catfish_gateway_base_url(base_url):
                return client, model
            cls = type(client)
            if cls.__name__ not in ("OpenAI", "AsyncOpenAI"):
                return client, model
            api_key = getattr(client, "api_key", "") or runtime.get("api_key", "") or ""
            client = cls(
                api_key=api_key,
                base_url=str(getattr(client, "base_url", "") or ""),
                default_headers={"X-Catfish-User": cf_user},
            )
        except Exception as e:
            logger.debug("P3b _resolve_auto rebuild failed: %s", e)
        return client, model

    aux._resolve_auto = patched_resolve_auto




def _patch_p5_p6_p11_api_server_create_agent_and_picker() -> None:
    """
    P5: APIServerAdapter._extract_catfish_outgoing_user 新方法
    P6: APIServerAdapter._create_agent wrap: post-init set attribute + apply_headers + picker
        model 覆盖
    P11: aiohttp middleware 拦截 body.model (在 _patch_p8_p9 一起注册到 app, 因为
         middleware 注册时机一致)
    """
    from gateway.platforms.api_server import APIServerAdapter

    # P5: _extract_catfish_outgoing_user
    def _extract_catfish_outgoing_user(self, request) -> str:
        """从 request 提 X-Catfish-User header (HTTP path).

        Companion / hermes-cli HTTP client 调 hermes 时已经把 user 在 header 里,
        直接提. CLI / 手动起的 agent 没这 header 时返回 "", agent 走 5 步链兜底.
        """
        try:
            user = (request.headers.get("X-Catfish-User", "") or "").strip()
            if user:
                return user
        except Exception:
            pass
        return ""

    APIServerAdapter._extract_catfish_outgoing_user = _extract_catfish_outgoing_user

    # P6 + P11: _create_agent wrap
    #
    # hermes 0.15 真实签名 (api_server.py:1101):
    #   def _create_agent(self, ephemeral_system_prompt=None, session_id=None,
    #                     stream_delta_callback=None, ..., model_override=None,
    #                     catfish_outgoing_user=None) -> Any
    # **全 kwargs**, 没有 request/body. 用 *args, **kwargs 通用 wrap, 不假设签名.
    #
    # 关键: 我们这版 plugin 不能拿到 request, 因为 hermes 0.15 native handler
    # 在 _create_agent 之前就把 body.model + X-Catfish-User header 提出来当 kwarg
    # 传进来 (model_override / catfish_outgoing_user). 所以 P6/P11 实质上是
    # **依靠 hermes 已经接受这俩 kwarg** — 我们仓内 5/27-5/29 patch 把它们 wire
    # 上了. 这是 plugin 跟 hermes 仓 patch 的耦合点.
    #
    # 如果 hermes 0.15 没 wire (上游 pristine), model_override / catfish_outgoing_user
    # 永远不会被 caller 传, plugin 就 noop. 这是 picker 这条最难走 plugin 的原因
    # (前面分析过). 真要 100% 不依赖 hermes 仓 patch, 必须走 aiohttp middleware
    # 拦 body.model + X-Catfish-User, 再 wrap _create_agent 取出来. middleware
    # 已经在 P11 注册 (_picker_model_stash_middleware), 但 X-Catfish-User 没拦.
    # 这条 TODO 留给 revert 后真破 P6 时再补.
    _orig_create_agent = APIServerAdapter._create_agent

    def patched_create_agent(self, *args, **kwargs):
        agent = _orig_create_agent(self, *args, **kwargs)
        # P6: X-Catfish-User 注入. 三段查找:
        # (1) kwargs.catfish_outgoing_user (hermes 仓 5/27 patch 在时 caller 传)
        # (2) CV_CF_USER (middleware 从 X-Catfish-User header 提取, plugin 独立路径)
        # (3) 不动 (orig _create_agent 自己 set 过的情况)
        #
        # 拿到 user 后: 写 agent attribute → 重 apply headers → 重建 OpenAI client.
        try:
            cf_user = kwargs.get("catfish_outgoing_user") or _ctx().CV_CF_USER.get() or ""
            current = getattr(agent, "_catfish_outgoing_user", "") or ""
            if cf_user and cf_user != current:
                agent._catfish_outgoing_user = cf_user
                if hasattr(agent, "_apply_client_headers_for_base_url"):
                    agent._apply_client_headers_for_base_url(
                        str(getattr(agent, "base_url", "") or "")
                    )
                if hasattr(agent, "_replace_primary_openai_client"):
                    agent._replace_primary_openai_client(reason="catfish_user_from_cv")

            # P47/P4: 跨线程的标题任务需要知道当前请求是不是 Companion 内部服务。
            sid = getattr(agent, "session_id", "") or ""
            try:
                cf_source = (_ctx().CV_CF_SOURCE.get() or "").strip()
            except Exception:  # noqa: BLE001
                cf_source = ""
            if sid and cf_source:
                _sib("session_registry").register_source(sid, cf_source)

            # P11 + P46: picker 是**唯一真源**.
            #
            # 老 P11 只有两段: kwargs.model_override / CV_PICKER_MODEL (都来自
            # 请求体的 model)。请求体没带 model 时就"不动", 于是 hermes 自己那套
            # 优先级说了算 —— 而它里面有一条**会话级持久化 override**
            # (state.db sessions.model), 一旦写进去就永久生效, 员工换 picker 也
            # 不解除。
            #
            # 8/9 实撞: 工作台选的是 deepseek, 但会话 api-b6bcbf8a419068fa 的
            # sessions.model 钉着 catfish-public-qwen-flash (周配额已耗尽),
            # 于是同一次早安页刷新里一部分请求 deepseek 成功、一部分 qwen 429。
            # session_key 是 sha256(system_prompt + 首条 user message)[:16],
            # 员工看不到也清不掉。
            #
            # 鸿波 8/9 拍板: **在要求确定性的环境里这不可接受, 直接砍掉。**
            # 加第三段 —— 请求体没带 model 时读 picker_state.json 兜底, 让那个
            # 持久化的会话模型变成惰性的 (我们不拦它的写入, 只是不再听它的)。
            #
            # 判据本身在 model_authority.decide_model, 那是纯函数有单测;
            # 这里只负责"什么时候调"。
            #
            # 8/13: 原来这里是 `from . import model_authority` (函数体内相对导入)。
            # 它一直是通的 —— 日志里 P46 真定夺过、"P6/P11 post-init failed" 一次
            # 没有。但它落在一个 except 只打 warning 的 try 里, 万一哪天 hermes 换
            # 加载方式导致相对导入失效, 表现就是「模型只能 picker 模型」这条硬规矩
            # 静默失效, 只留一行 warning。改用模块层已经装好的那份, 少一个失败面。
            model_override = _model_authority().decide_model(
                request_model=kwargs.get("model_override") or _ctx().CV_PICKER_MODEL.get(),
            )
            if model_override and agent.model != model_override:
                logger.info(
                    "P46 picker 定夺: agent.model %s → %s "
                    "(会话持久化的模型不参与, 见 model_authority.py)",
                    agent.model, model_override,
                )
                agent.model = model_override
        except Exception as e:
            logger.warning("P6/P11 _create_agent post-init failed: %s", e)
        return agent

    APIServerAdapter._create_agent = patched_create_agent














# ── P10 ──────────────────────────────────────────────────────────────────

def _patch_p10_apply_client_headers_localhost() -> None:
    """AIAgent._apply_client_headers_for_base_url 加 localhost:8999 分支.

    wrap: 先 check 是不是 catfish-gateway, 是就跑我们 5 步链 set X-Catfish-User,
    return. 不是就调 orig (其它 provider OpenRouter/NIM/Codex etc.).
    """
    from run_agent import AIAgent

    _orig = AIAgent._apply_client_headers_for_base_url

    def patched(self, base_url: str) -> None:
        # P29 (6/5): env-aware gateway 检测, 不再硬编码 localhost:8999.
        if _is_catfish_gateway_base_url(base_url or ""):
            cf_user = _resolver().resolve_for_agent(self)
            headers: dict[str, str] = {}
            if cf_user:
                headers["X-Catfish-User"] = cf_user
            # P41 (8/8): 来源标记跟着走。中间件在 inbound 请求上 set 的 CV,
            # 经 _patch_asyncio_executor_for_contextvars 的 copy_context 一路
            # 带到 executor 线程, 这里读得到。
            #
            # **跟 X-Catfish-User 解耦**: 有 source 没 user 的场景 (CLI / cron
            # 调 hermes 时不带 user) 也要标得住, 所以不写在 if cf_user 里面。
            try:
                cf_source = (_ctx().CV_CF_SOURCE.get() or "").strip()
            except Exception:  # noqa: BLE001  CV 不该抛, 抛了也不能拖垮发请求
                cf_source = ""
            if cf_source:
                headers["X-Catfish-Source"] = cf_source

            if headers:
                self._client_kwargs["default_headers"] = headers
            else:
                # 两个都没有时 explicit clear (防别地方继承上一轮)
                self._client_kwargs.pop("default_headers", None)
            return
        return _orig(self, base_url)

    AIAgent._apply_client_headers_for_base_url = patched




# ── P21 (P3.5.74, 6/22 鸿波 catch "cron 不是用 picker 吗") ──────────────
#
# hermes cron/scheduler.py run_job 读 config.yaml model.default → cron job
# 用 catfish-public-deepseek-flash, 跟 chat picker 解耦. P3.5.28/42/42.1 把
# picker 联动到 chat / advisor / email scheduler / vision / catfish-memory
# summarize, 这里补 cron job — 最后一个 picker sprint gap.
#
# 实施: monkey-patch hermes cron.scheduler.run_job. wrap 老 run_job, 在调
# 用前检查 picker_state.json — 若 picker set 了 chat_model, 把 job 字段 +
# 环境 + config 字段都 override 让 hermes 原代码读到 picker model.
#
# 优先级 (跟 catfish-memory _get_summarize_model 一致):
#   picker_state.json > job.model (user 显式指定) > config.yaml.model.default > env
#
# 安全: try/except 包死, picker 读失败 fallback 老路径不影响 cron 跑.
# fail-silent fallback (跟 P16 / catfish-memory 风格一致).

def _read_catfish_picker_model() -> str:
    """读 ~/.catfish/picker_state.json 拿 chat_model。

    # 8/13: 从"本地抄一份"改成转调 model_authority.read_picker_model

    这个函数原来是 catfish-memory 那份 `_read_picker_state_model` 的手抄副本
    (注释写着"两个 plugin 独立装载, 不能 cross import, 抄个最简版本")。那个理由
    对 **catfish-memory** 成立, 但对同一个包里的 `model_authority` 不成立 ——
    P46 早就把同一段逻辑放在那里了, 而且它才是"picker 是唯一真源"这条规矩的归属
    模块。

    两份并存的风险很具体: 「模型只能 picker 模型」是硬规矩, 而 P21 / P23 / P39
    走这份、model_authority.decide_model 走那份。两边哪天飘了, 表现是"有的路径听
    picker、有的不听", 跟 8/9 那次会话级 model override 一模一样 —— 同一个员工、
    同一个界面, 不同请求用不同模型, 而且没有任何地方显示这件事。

    改之前把两份实现在 11 种输入上对拍过 (文件不存在 / 空文件 / 坏 json /
    不是 dict / 缺键 / 空串 / 全空格 / 非字符串 / null / 前后带空格 / 正常),
    输出逐个相同。测试在 tests/test_picker_reader_single_source.py。
    """
    return _model_authority().read_picker_model()










# ── P23 (P3.5.79, 6/23 鸿波): inbound message 路径 picker 联动 ─────────
#
# 真因 audit (P3.5.77 audit-完整 + 6/22 23:50 微信 ClawBot 真聊 fail):
#
#   ① 微信 inbound message 走 gateway.run.handle_inbound → AIAgent(model=...)
#   ② model 来自 turn_route["model"], 由 _resolve_session_agent_runtime 算
#   ③ _resolve_session_agent_runtime 第一行调 _resolve_gateway_model(config)
#   ④ _resolve_gateway_model 直接读 cfg["model"]["default"] 返
#      = config.yaml.model.default (当时 = catfish-public-deepseek-flash,
#        7/22 校: **现值 = catfish-auto** — 会走 gateway roles.yaml chat_default resolve.
#        老注释这行只作历史 bug 复现现场读)
#   ⑤ 微信 path 完全不读 ~/.catfish/picker_state.json, 跟桌面 chat 行为不一致
#      (桌面 chat 走 P5/P6/P11 _RUNTIME_MAIN_MODEL override, 真桥到 picker)
#   ⑥ 实证: 23:46:57 inbound msg='19号花了300块钱，加油' platform=weixin
#           model=catfish-public-deepseek-flash    ← deepseek 而不是 picker 选的 qwen
#
# 修法 (跟 P21 cron picker 同款 monkey-patch pattern): wrap _resolve_gateway_model,
# 在原结果之前优先读 picker_state.json. 一处 hook 覆盖**所有 platform** (微信 /
# Discord / Slack / Telegram / CLI inbound), 跟 P3.5.74 cron 同一架构.
#
# 优先级 (新): picker_state.json > config.yaml.model.default
#  (跟 cron P21 优先级一致, 让员工 picker 一切真"主权" — 选啥所有 platform 用啥)
#
# fail-safe: picker 读失败 / hermes gateway.run 模块没导 / patch attach 失败 →
# silent fallback 老路径 (config.yaml.model.default). _PATCH_TARGETS 加
# ("gateway.run", "_resolve_gateway_model", "func") fail-loud verify.

def _patch_p23_inbound_picker_integration() -> None:
    """patch gateway.run._resolve_gateway_model — inbound message model 跟 picker 联动.

    hermes _resolve_gateway_model 代码 (gateway/run.py:2070):
        def _resolve_gateway_model(config=None) -> str:
            cfg = config if config is not None else _load_gateway_config()
            model_cfg = cfg.get("model", {})
            if isinstance(model_cfg, str): return model_cfg
            elif isinstance(model_cfg, dict):
                return model_cfg.get("default") or model_cfg.get("model") or ""
            return ""

    patch 思路: wrap, 在 _orig 调用前先读 picker_state.json. 若 picker 有值, 跳过
    _orig 直接返 picker model. picker 空 → fallback 老路径 (config.yaml).

    fail-safe: picker 读失败 → 走老路径 (silent, 不抛). hermes gateway.run 模块没导
    → silent skip patch (旧 hermes 版本不支持).
    """
    try:
        from gateway import run as _gateway_run  # noqa: PLC0415
    except ImportError as e:
        logger.warning("P23: hermes gateway.run module 没导, skip patch (%s)", e)
        return

    _orig_resolve = getattr(_gateway_run, "_resolve_gateway_model", None)
    if _orig_resolve is None:
        logger.warning(
            "P23: gateway.run._resolve_gateway_model 不存在 (hermes 重构?), skip patch."
        )
        return

    def _patched_resolve_gateway_model(config=None):
        # picker override (优先级最高). picker 读失败 → fallback 老路径.
        try:
            picker_model = _read_catfish_picker_model()
            if picker_model:
                logger.info(
                    "P23 inbound picker integration: gateway model → %r "
                    "(picker_state.json override, was config.yaml fallback)",
                    picker_model,
                )
                return picker_model
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "P23 inbound: picker_state 读失败 (%s), fallback config.yaml",
                e,
            )

        # picker 空 / 读失败 → 走 hermes 老路径 (config.yaml.model.default)
        return _orig_resolve(config)

    _gateway_run._resolve_gateway_model = _patched_resolve_gateway_model
    logger.info(
        "P23 inbound picker integration patched — inbound message (微信/Discord/Slack/"
        "Telegram/CLI) model 跟 picker_state.json 联动 (优先级: picker > yaml.default) ✓"
    )
