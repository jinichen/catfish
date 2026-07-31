"""Config loader -- parse models.yaml and provide access helpers.

Env-var 插值:
    YAML 里允许写 ${VAR} 或 ${VAR:-default}, 加载时从 os.environ 替换。
    设计目的: 让 models.yaml 里不出现内网 IP / UUID / API base 等敏感信息,
    全部通过 env 注入。开源出去时 yaml 是干净的占位符模板。

    支持语法 (只在 string value 里识别):
        ${VAR}            必需; 没设置就报错
        ${VAR:-default}   可选, 没设置时用 default

    嵌入式 (一行可有多个占位符) + 嵌套 dict/list 全递归处理。
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, PrivateAttr

from . import model_store, provider_store, secrets_box

logger = logging.getLogger(__name__)

# ${VAR} 或 ${VAR:-default}; default 段允许空, 但不允许出现 } 字面
# (复杂的 default 自己加引号即可避开)
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _interpolate_env(value: Any) -> Any:
    """递归把 ${VAR} / ${VAR:-default} 替换为 os.environ 里的值。

    递归边界: str / dict / list 才递归; 其它类型 (int/bool/None) 原样返回。
    """
    if isinstance(value, dict):
        return {k: _interpolate_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate_env(v) for v in value]
    if not isinstance(value, str):
        return value

    def _sub(match: re.Match[str]) -> str:
        var = match.group(1)
        default = match.group(2)
        env_val = os.environ.get(var)
        if env_val is not None:
            return env_val
        if default is not None:
            return default
        raise RuntimeError(
            f"env variable ${{{var}}} is referenced in config but not set "
            f"(use ${{{var}:-default}} 给 fallback 或在 .env 里设)"
        )

    return _ENV_PATTERN.sub(_sub, value)


class UpstreamConfig(BaseModel):
    """Upstream LLM endpoint.

    Supports any LiteLLM-compatible provider. The `model` field must include
    the provider prefix, e.g. 'openai/qwen_v3_5_122b_a10b' or
    'gemini/gemini-2.5-pro'.
    """

    model: str

    # ── 供应商引用 (8/1) ────────────────────────────────────────────
    #
    # 新形态: upstream 只写 {model, provider, timeout}, api_base 和
    # api_key_env 从供应商行合并进来 (见 config_providers.merge_provider)。
    #
    # ⚠ 这个字段**必须真的存在于 UpstreamConfig 上**, 不能只当成"合并前的
    # 中间键"。模型 PUT 走的是 ModelConfig.model_validate(body), 而 pydantic
    # 默认忽略额外字段 —— 没有这个字段的话, 界面提交
    # {model, provider, timeout} 之后:
    #   · provider 被**静默丢掉**, 模型跟供应商的关联没了
    #   · api_key_env 落回默认值 "INTERNAL_LLM_KEY"
    # 对内网模型可能碰巧还能用 (掩盖问题), 对 Gemini / DeepSeek 就是错的,
    # 而配置上看不出少了什么。8/1 写供应商界面前实测到的。
    provider: str | None = None

    api_base: str | None = None  # only set for OpenAI-compatible self-hosted endpoints
    api_key_env: str = "INTERNAL_LLM_KEY"

    # Parameters the gateway will FORCE on every request, regardless of what
    # the client sent. Use this for model-specific requirements like
    # "Gemini 3 must run at temperature=1.0" or "Claude must not set n>1".
    # Client-sent values for the same keys are silently replaced.
    param_overrides: dict[str, Any] = Field(default_factory=dict)

    # Per-upstream timeout in seconds. Some preview models (Gemini 3) can
    # be slow to cold-start; private fast models rarely need more than 60s.
    timeout: int = 60

    # ── 存库的 key (8/1, DESIGN-PROVIDER-SPLIT §5) ──────────────────
    #
    # 用 **PrivateAttr 而不是普通字段**, 这是个刻意的安全选择:
    # 私有属性不进 model_dump() / model_dump_json(), 所以解密后的 key
    # **不可能**从任何一次序列化里漏出去 —— 那是结构性保证, 不靠人记得
    # 每次都 exclude。
    #
    # (admin_models_router 有一条降级路径会 dump cfg.models, 而 /v1/models
    #  和 /v1/catalog 也各自序列化模型信息。靠纪律去逐个 exclude 迟早漏一处。)
    #
    # 由 _assemble_config 在合并供应商之后赋值, model_validate 塞不进来。
    _secret: str | None = PrivateAttr(default=None)

    @property
    def api_key(self) -> str:
        """取 API key. 两条来源: 存库的密文 (解密后) 优先, 否则读环境变量.

        两条并存是迁移期的基础 —— 一半供应商走 env、一半走库, 都要能调用
        (DESIGN §6.2)。
        """
        key = self._secret or os.environ.get(self.api_key_env)
        if not key:
            raise RuntimeError(
                f"没有可用的 API key -- 无法调用模型 '{self.model}'。"
                f"这个供应商既没有配存库的 key, "
                f"环境变量 {self.api_key_env or '(未指定)'} 也没设。"
            )
        return key

    @property
    def is_available(self) -> bool:
        """key 配好了没. 9 处代码用它决定这个模型能不能被选到。"""
        return bool(self._secret or os.environ.get(self.api_key_env))


class FallbackConfig(BaseModel):
    """模型 fallback 链配置 —— 上游错误时自动切到下一个模型。

    设计:
        on_errors: 触发 fallback 的错误码/类型集合。429 (配额) / 503 (上游不可用) /
                   504 / "timeout" 是默认。其它错误 (400 客户端错 / 401 鉴权错) 不
                   走 fallback —— 切模型不会修。
        chain:    按顺序尝试的 model name 列表。第一个挂了试第二个, 以此类推。
        max_hops: 最多跳几次。防 chain 互相循环 (a→b, b→a) 卡死。

    用法:
        catfish-public-gemini-pro 配 chain=[catfish-public-qwen-flash]
        Gemini 撞 429 → gateway 透明地用 Qwen 重试同一个 messages, 员工无感。

    展望:
        未来可加 chain 选择策略 (cheap_first / fast_first / quality_first),
        现在按 yaml 顺序就够。
    """

    on_errors: list = Field(
        default_factory=lambda: [429, 503, 504, "timeout"],
    )
    chain: list[str] = Field(default_factory=list)
    max_hops: int = 2


class RateLimitsConfig(BaseModel):
    """6/7 BL-GROQ-TPM-PREFLIGHT: 上游 provider 限速字段, 让 gateway preflight.

    鸿波 6/7 22:43 撞 Groq Free Plan TPM=8K, 单次 95K 请求 → 上游 413 +
    rate_limit_exceeded. Catfish 之前没 preflight, 直接抛 stack trace 给员工.

    现在 chat completion 前 check estimate_prompt_tokens > tpm × 0.9, 超就
    返友好 413 + 替代 model 建议. 跟 LiteLLM 的 num_retries=0 + auto_fallback
    取舍 (永久错不重试) 一致.

    字段语义跟 Groq docs (console.groq.com/docs/rate-limits) 一致:
    - tpm: tokens per minute (主要)
    - rpm: requests per minute
    - rpd: requests per day
    - tpd: tokens per day
    - tier: 上游 service tier 标识 (groq-free / groq-dev / openai-paid / etc.),
           给员工友好提示用 ("升级到 Groq Dev Tier 解锁 60K TPM")
    """

    tpm: int | None = None
    rpm: int | None = None
    rpd: int | None = None
    tpd: int | None = None
    tier: str | None = None


class ModelConfig(BaseModel):
    """A single model the gateway can route to."""

    name: str
    tier: str = "private"  # private | public
    display_name: str
    mode: str = "chat"  # chat | embedding
    default: bool = False

    upstream: UpstreamConfig

    context_window: int | None = None
    # BL-MAX-OUTPUT-TOKENS (5/15 22:31): 单次 output 上限, 跟 context_window 区分.
    # 实盘: DeepSeek context 1M 但 max_tokens 393216 / Gemini Pro context 2M 但
    # max_tokens 65536. 没配 → 沿用老行为 (= context_window).
    # 这字段就是各家 model 的真实属性, 不是 hack. 查上游 API 文档抄过来即可.
    max_output_tokens: int | None = None
    supports_tool_use: bool = False
    supports_streaming: bool = True
    supports_vision: bool = False
    recommended_for: list[str] = Field(default_factory=list)
    cost_tier: str = "free"  # free | paid

    # ── 展示与计价 (7/30) ────────────────────────────────────────────
    #
    # 这三个字段原本硬编码在前端 central/web/src/lib/modelDisplay.ts 的两张
    # 表里 (_MAP 和 _PRICE_RMB_PER_1K_TOKEN), 那个文件的注释还写着
    # "添加新 model: 在 _MAP 加一行"。
    #
    # 模型改成可在界面上增删改之后, 那种做法直接矛盾: 客户加一个模型是运行时
    # 操作, 而补上它的显示名和单价却要改前端代码 + 重新构建 + 重新部署。
    # 不补的话审计页显示"未知模型 ⚪", 计费按兜底价 0.001 算 —— 这正是 7/30
    # 查 catfish-public-qwen-flash 改名事故时暴露的表现, 区别只在于那次是
    # 偶然触发, 而可编辑模型会让它**每次新增模型都必然发生**。
    #
    # 所以搬到模型配置里, 跟着模型走。前端仍保留一张兜底表, 但用途变成
    # "历史审计数据引用了已被删除的模型" —— 那种情况配置里查不到, 是真的
    # 需要兜底, 不是偷懒。

    #: 每 1000 token 的人民币单价. 审计页成本核算用。
    #: 不填 → 前端按兜底价算, 并且**应该**在界面上提示这个模型的成本不准。
    price_per_1k_tokens: float | None = None

    #: 图表里区分模型用的品牌色 (CSS color, 如 "#10b981")。不填 → 前端给个默认灰。
    color: str | None = None

    #: 列表行首的小圆点 emoji, 一眼分辨 provider。不填 → 前端用 ⚪。
    dot_emoji: str | None = None

    # P1: 上游失败时自动切到 chain 里下一个模型。空 chain (默认) 表示不 fallback。
    fallback: FallbackConfig | None = None

    # 6/7 BL-GROQ-TPM-PREFLIGHT: 上游 rate limits (preflight 用, 防 413 暴露 stack)
    rate_limits: RateLimitsConfig | None = None


class McpRegistryConfig(BaseModel):
    """BL-D3 (5/9): 反向代理 mcp-registry 服务的配置.

    gateway 收到 /v1/mcp/* 请求 → 透传到 upstream_url + /v1/mcp/*,
    并把员工的 dept (从 JWT 抽出) 加到 X-Catfish-User-Dept header,
    让 mcp-registry 做部门权限过滤.

    上游 mcp-registry 服务默认跑在 :8996 (跟 skills-hub 8997 错开), dev/prod
    通过 yaml 配:

        mcp_registry:
          upstream_url: http://127.0.0.1:8996
          enabled: true
          timeout: 10
    """

    upstream_url: str = "http://127.0.0.1:8996"
    enabled: bool = True
    timeout: int = 10  # 秒, registry 操作都很快, 10s 够


class SkillsHubConfig(BaseModel):
    """BL-D2 (5/10): 反向代理 catfish-skills-hub 服务. 跟 mcp_registry 同模式.

    gateway 收 /v1/hub/* → 上游 :8997. 注入 X-Catfish-User-Sub/-Dept/-Role,
    上游 hub (BL-D2 改造后) 信任 header 不再自己验 dev_token.

        skills_hub:
          upstream_url: http://127.0.0.1:8997
          enabled: true
          timeout: 30   # publish multipart 可能稍慢, 给 30s
    """

    upstream_url: str = "http://127.0.0.1:8997"
    enabled: bool = True
    timeout: int = 30  # 秒, publish multipart 文件传输可能超 10s


class WikiHubConfig(BaseModel):
    """P3.3.18 (6/10): 反向代理 catfish-wiki-hub 服务 (部门 wiki publish).

    跟 skills_hub 同模式. gateway 收 /v1/wiki/* → 上游 :8994. 注入
    X-Catfish-User-Sub/-Dept/-Role. 上游 wiki-hub 信任 header.

    端口约定 (P3.4.1 6/13 砍 8995): 8642 hermes / 8996 mcp-registry /
    8997 skills-hub / 8998 identity-server / 8999 gateway / 8994 wiki-hub.
    (~~8995 secret-broker~~ — P3.4.1 砍, OAuth token 改员工本机存)

        wiki_hub:
          upstream_url: http://127.0.0.1:8994
          enabled: true     # 客户不需要部门 wiki 时可关
          timeout: 20

    Manifesto: 公理 2 例外 (员工主动 push), 公理 3/4 (中央无 push / 无反向拉).
    """

    upstream_url: str = "http://127.0.0.1:8994"
    enabled: bool = True
    timeout: int = 20  # 秒, wiki body 一般小, 20s 够


class Config(BaseModel):
    version: int = 1
    models: list[ModelConfig]
    mcp_registry: McpRegistryConfig = Field(default_factory=McpRegistryConfig)
    skills_hub: SkillsHubConfig = Field(default_factory=SkillsHubConfig)
    # P3.3.18 (6/10): wiki-hub 配置默认 enabled — 客户不要部门 wiki 时 yaml 关
    wiki_hub: WikiHubConfig = Field(default_factory=WikiHubConfig)

    # BL-FALLBACK-TOGGLE (2026-05-16 鸿波):
    # 默认 false — 上游挂直接返客户端, **不自动跳别的 model**.
    # 鸿波模式: 私有部署员工选私有 → 用私有; 私有挂 → 报错让员工换 model;
    # 不要自动切公网 (合规风险 + 行为不可预测).
    #
    # 想恢复老 fallback 行为 (例 dev / 测试 / 客户特殊需求): 改 yaml 顶层加
    #   auto_fallback: true
    # 或 env CATFISH_AUTO_FALLBACK=1.
    #
    # 注: 老 fallback.py / yaml fallback.chain 字段都保留, 不删 — 作 escape hatch.
    # 只是默认不触发. 真要用 chain 时切 toggle 即可.
    auto_fallback: bool = False

    # BL-FALLBACK-PROMPT-CAP (5/14 鸿波 token audit 后加): 公网 fallback 拦大 prompt.
    # 鸿波 5/14 audit 发现公网 deepseek fallback 51 次 / 2.9M tokens, avg 57K 比内网
    # 40K 还重 — 内网慢一点就切公网, 公网更慢更贵. 加阈值: prompt 估算超这个值的请求,
    # fallback 链跳过所有 tier=public 的 candidate (只在内网链 retry).
    # 估算用 messages 字符数 / 2 (中英混保守估), 可能高估 50% 但安全侧靠公网钱.
    # 设 0 = 关功能 (回到老行为, 任意 prompt 都允许 fallback 公网).
    # 设 30000 = 默认 (鸿波 audit 30K 阈值合理, 大多数日常 chat 在内, 长任务 / 大附件
    # 走超阈值不切公网).
    #
    # 注意 BL-FALLBACK-TOGGLE 后, 这个 cap 只在 auto_fallback=true 时才生效
    # (auto_fallback=false 直接不 fallback, 不需要二次 cap).
    max_fallback_prompt_tokens: int = 30000

    def get_model(self, name: str) -> ModelConfig | None:
        for m in self.models:
            if m.name == name:
                return m
        return None

    def default_model(self) -> ModelConfig | None:
        for m in self.models:
            if m.default:
                return m
        return self.models[0] if self.models else None


def resolve_config_path(path: Path | None = None) -> Path:
    """算出 models.yaml 的实际路径. 优先级 (高→低):
        1. caller path 参数
        2. CATFISH_CONFIG env (单文件 override)
        3. CATFISH_GATEWAY_CONFIG_PATH/models.yaml (P26: 统一目录 env)
        4. config/models.yaml (repo 内默认)

    从 load_config 里抽出来 —— get_config() 要用它 stat 文件判有没有变,
    不能各算各的, 否则"检查的文件"和"加载的文件"可能不是同一个。
    """
    if path is None:
        env_single = os.environ.get("CATFISH_CONFIG", "").strip()
        env_dir = os.environ.get("CATFISH_GATEWAY_CONFIG_PATH", "").strip()
        if env_single:
            path = Path(env_single)
        elif env_dir:
            path = Path(env_dir) / "models.yaml"
        else:
            path = Path("config/models.yaml")

    # 允许调用方传字符串 —— 类型标注写的是 Path, 但实际调用点 (含测试) 很容易
    # 顺手传个 str, 而 str 没有 .is_absolute() 会直接 AttributeError, 报错信息
    # 跟"配置有问题"毫无关系, 查起来绕。统一转一次, 一行的事。
    path = Path(path)

    if not path.is_absolute():
        # try both cwd and the installed package dir
        candidates = [Path.cwd() / path, Path(__file__).parent.parent.parent / path]
        for c in candidates:
            if c.exists():
                path = c
                break
    return Path(path)


def load_config(path: Path | None = None) -> Config:
    """无条件读盘 + 解析 models.yaml.

    ⚠ 业务代码不要直接调这个 —— 用 get_config()。
      这个函数每次都重读重解析, 且不参与缓存/失效, 直接调会绕开一致性保证。
      保留它是因为 (a) get_config 内部要用 (b) 测试要按显式 path 加载。
    """
    path = resolve_config_path(path)

    if not Path(path).exists():
        raise FileNotFoundError(
            f"models config not found: {path} "
            "(set CATFISH_CONFIG env, CATFISH_GATEWAY_CONFIG_PATH (目录), "
            "or place it in config/models.yaml)"
        )

    with open(path) as f:
        data = yaml.safe_load(f)

    # 在 pydantic validate 之前先做 env 插值 —— 这样 ModelConfig 拿到的就是
    # 已经替换好的真实值 (api_base / 任何 ${VAR} 占位符都被替换), validation
    # 也能正确工作 (例如必填字段没设默认时能立即报清楚错)
    data = _interpolate_env(data)
    return Config.model_validate(data)


# 库里存的模型, **只有这几个路径**做 env 插值.
#
# 不是"整行递归插值"。原因是 display_name 这类字段会经 /v1/catalog 发给
# **未认证**调用方 —— 把 display_name 填成 ${INTERNAL_LLM_KEY} 就能让网关
# 把密钥打印出来。而 models.yaml 里全部 3 处占位符都在 upstream.api_base
# (7/30 实查), 没有别的字段需要这个能力。
#
# ── 8/1 军规 §1 拆分 ────────────────────────────────────────────────
# env 占位符那一族 (约 134 行) 搬去 config_env.py。config.py 712 行已在警戒区,
# 而 provider 拆分还要往里加合并逻辑, 必然推过 800。
#
# 军规 §3 re-export: 老 caller `from .config import restore_placeholders` /
# `interpolate_model_row` / `model_config_errors` 全部不受影响。
from .config_providers import merge_provider  # noqa: E402,F401
from .config_env import (  # noqa: E402,F401
    _ENV_PATHS,
    _get_path,
    _set_path,
    interpolate_model_row,
    model_config_errors,
    restore_placeholders,
    set_model_config_errors,
)


def load_raw_models(path: Path | None = None) -> list[dict[str, Any]]:
    """读 models.yaml 里的模型列表, **不做 env 插值** —— 保留 ${VAR} 原样.

    ## 为什么需要这个

    models.yaml 里内网地址是故意写成 `api_base: ${INTERNAL_LLM_BASE_QWEN_VISION}`
    的 —— 真实地址在 .env 里, 不进配置文件、不进 git。

    但 7/30 把模型搬进库之后, 播种用的是 `config.models`, 那已经是插值**之后**
    的对象。`model_dump()` 一下, 整串 `http://10.10.40.102:32730/openapi/<uuid>/v1`
    就作为字面量进了 gateway_models.payload。两个后果:

      1. 那个刻意的隔离没了 —— 地址进了库, 也就进了 pg_dump、进了备份,
         还会直接显示在「模型」页上被截图带走。
      2. **改 .env 从此不再有任何作用。** 库里是烤死的旧地址, 而播种是
         ON CONFLICT DO NOTHING, 重启也不会更新。网关继续打老地址,
         不报错不提示 —— 又一个静默失败。

    所以播种要用这份原始的。占位符留在库里, 由 _assemble_config 在读出来时
    再插值 (跟 yaml 那条路径同一个 _interpolate_env)。
    """
    path = resolve_config_path(path)
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    models = data.get("models") or []
    return [m for m in models if isinstance(m, dict)]



# ── 配置访问的唯一入口 (7/30) ────────────────────────────────────────
#
# ## 在补什么
#
# 在这之前, 同一个进程里有**两套真相**:
#
#   app.state.config     启动时 load_config() 的快照, 之后永不更新 (10 处在用,
#                        含 /v1/models、/v1/catalog、三个 hub proxy)
#   load_config()        每次调用重读重解析 yaml (6 处在用, 含 proactive、
#                        facts_pipeline、conversation_compressor)
#
# 后果: 改完配置, 一部分代码立刻看到新值, 另一部分要重启才看到。表现是
# "改了、页面上也变了、但实际调用还用旧的" —— 这类不一致极难查, 因为每次
# 复现走到哪条路径是随机的。
#
# 顺带纠正一处错误注释: facts_pipeline.py 里写着 "走 load_config 单例缓存,
# 不重读 yaml"。**load_config 从来没有任何缓存**, 每次都重读重解析。
#
# ## 为什么是 TTL 而不是进程内失效
#
# gateway 跑 4 个 worker (compose: GATEWAY_WORKERS:-4), 是**独立进程**。
# 写接口只会落在其中一个 worker 上, 那种"写完 invalidate 本进程缓存"的做法
# 只有 1/4 的请求能看到新配置, 而且哪次看到是随机的 —— 比不刷新更糟。
#
# 跨进程要么上 IPC (Redis pub/sub、PG LISTEN/NOTIFY), 要么各自定期回源。
# 这里选后者: 每个 worker 独立地"最多陈旧 TTL 秒", 无需任何跨进程设施,
# 也不会出现部分 worker 永久落后。代价是改配置后最长等 TTL 秒全员生效。
#
# TTL 内不碰磁盘; 过了 TTL 只做一次 stat (纳秒级), 内容没变就续期不重新解析。
# 所以稳态开销 ≈ 每 TTL 秒一次 stat, 可以忽略。
#
# ## 换 DB 存储时怎么改
#
# 只需换 _config_stamp(): 文件时代返 (mtime_ns, size), DB 时代返版本号/
# max(updated_at)。get_config / invalidate_config 和全部调用方都不用动。
_CACHE: Config | None = None
_CACHE_STAMP: Any = None
_CACHE_CHECKED_AT: float = 0.0
_CACHE_LOCK = threading.Lock()

_DEFAULT_TTL_SECONDS = 3.0


def config_ttl_seconds() -> float:
    """回源间隔. env CATFISH_CONFIG_TTL 覆盖; 设 0 = 每次都查 (测试用)."""
    raw = os.environ.get("CATFISH_CONFIG_TTL", "").strip()
    if not raw:
        return _DEFAULT_TTL_SECONDS
    try:
        v = float(raw)
    except ValueError:
        return _DEFAULT_TTL_SECONDS
    return v if v >= 0 else _DEFAULT_TTL_SECONDS


def _file_stamp() -> Any:
    """models.yaml 的代次 = (mtime_ns, size).

    只用 mtime 不够: 同一秒内的两次写在某些文件系统上 mtime 可能相同,
    加 size 能多挡一类。真要严格得算内容 hash, 但那要读全文, 失去了 stat
    的成本优势; 配置文件不是高频改动的东西, 这个强度够用。
    """
    try:
        st = resolve_config_path().stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _config_stamp() -> Any:
    """配置源的"代次" —— 变了就说明要重新加载.

    ⚠ 必须**同时覆盖两个源**。启用库存储后:
        模型列表  → 库 (gateway_config_meta.revision)
        顶层字段  → 仍然只从 models.yaml 读 (auto_fallback /
                    max_fallback_prompt_tokens / 三个 hub 的地址)
    只盯库的话, 改了 yaml 顶层字段永远不会被重新加载; 只盯文件的话,
    界面上改模型永远不生效。两个都要进代次。
    """
    file_part = _file_stamp()
    if model_store.is_enabled():
        return ("db", model_store.revision(), file_part)
    return ("file", file_part)


# 是否曾经成功从库读到过模型。决定库挂掉时是"沿用缓存"还是"降级 yaml" ——
# 见 _assemble_config 里的分支说明。一旦为 True 就不再变回 False: 库恢复后
# 自然会继续从库读, 而库真的坏了的期间我们要坚持用缓存而不是悄悄换配置。
_DB_EVER_SERVED = False


def _assemble_config() -> Config:
    """组装最终配置: yaml 出顶层字段, 库出模型列表."""
    cfg = load_config()  # 顶层字段 + yaml 里的模型 (未播种时就用这份)

    if not model_store.is_enabled():
        return cfg  # 没配 PG (单测 / 本机 dev) → 纯 yaml

    rows = model_store.read_models()
    if rows is None:
        # 库不可用。这里要分两种情况, 混为一谈会出事:
        #
        #   曾经从库读到过 → **抛**, 让 get_config 走"沿用上一份好配置"。
        #     不能退回 yaml: 那会让客户在界面上配的模型突然消失、换回出厂
        #     默认, 而且没有任何报错。宁可短暂用旧缓存。
        #
        #   从没读到过 (冷启动时库还没起来 / 本机 dev 没跑 PG) → 降级到 yaml。
        #     此时没有"上一份"可沿用, 抛的话 gateway 直接起不来 —— 而 yaml 里
        #     本来就有一份完整可用的模型配置, 没有理由让整个服务挂掉。
        if _DB_EVER_SERVED:
            raise RuntimeError("模型库不可用, 且已有库配置在服务中 —— 拒绝退回 yaml")
        logger.warning(
            "模型库不可用, 本次用 models.yaml 里的模型 (冷启动降级)。"
            "库恢复后会自动切回, 无需重启。"
        )
        return cfg

    if not rows:
        return cfg  # 库通但还没播种 (首次启动) → 先用 yaml, lifespan 会播种

    globals()["_DB_EVER_SERVED"] = True

    # 8/1: 供应商合并。库里模型的 upstream 可能是
    #   老形态 {"model", "api_base", "api_key_env", "timeout"}
    #   新形态 {"model", "provider", "timeout"}
    # 两种并存 (迁移期), merge_provider 把后者还原成前者。
    #
    # **合并必须在插值之前** —— api_base 上的 ${VAR} 现在挂在供应商行上,
    # 先合并才轮得到 interpolate_model_row 去解析它。反过来的话占位符
    # 原样进 UpstreamConfig, 调用时拿 "${INTERNAL_LLM_BASE_X}" 当 URL 用。
    #
    # 供应商表读不到 (还没跑 008 / 库临时不可用) → providers 为空 dict,
    # 老形态模型照常工作, 新形态模型会被标成"引用了不存在的供应商"并在
    # 界面上显示原因。不抛。
    providers = provider_store.read_providers() or {}

    # 逐个模型来, 且不让单个模型的失败掀翻整份配置 (见 interpolate_model_row)。
    errs: dict[str, str] = {}
    models = []
    for r in rows:
        name = str(r.get("name", "?"))
        row, perr = merge_provider(r, providers)
        if perr:
            errs[name] = perr
            logger.error("模型 %s 的供应商引用有问题: %s", name, perr)
        # 库里存的是 ${VAR} 原样 (见 load_raw_models), 到这里才插值 —— 这样
        # IT 改 .env 重启就生效, 而不是被库里烤死的旧值盖住。
        row, err = interpolate_model_row(row)
        if err:
            errs.setdefault(name, err)
            logger.error("模型 %s 的 env 占位符没解析成功: %s", name, err)

        m = ModelConfig.model_validate(row)

        # 8/1: 存库的 key 在这里解密并挂到私有属性上。
        #
        # 用 PrivateAttr 而不是普通字段是刻意的 —— 它不进 model_dump(),
        # 所以解密后的 key 不可能从任何一次序列化里漏出去 (见 UpstreamConfig)。
        #
        # 解不开时**不抛**: 只有这一家的模型不可用 (is_available 返 False,
        # 于是它不出现在员工的模型选择里、也不会被 fallback 链选中),
        # 而不是整个网关起不来。原因照样进 errs, 界面上看得见。
        pid = (r.get("upstream") or {}).get("provider")
        prow = providers.get(pid) if pid else None
        if prow and prow.get("api_key_enc"):
            secret = secrets_box.decrypt(prow["api_key_enc"])
            if secret:
                m.upstream._secret = secret
            else:
                errs.setdefault(
                    name,
                    f"供应商 {pid!r} 的 API key 解不开 —— "
                    + (
                        f"{secrets_box.MASTER_KEY_ENV} 换过但没跑轮换脚本, 或者密文被改坏了。"
                        if secrets_box.is_configured()
                        else f"服务器没有配 {secrets_box.MASTER_KEY_ENV}。"
                        "请让 IT 在 .env 里加上它然后重启网关。"
                    ),
                )
        models.append(m)
    set_model_config_errors(errs)
    cfg.models = models
    return cfg


def get_config() -> Config:
    """**所有读配置的地方都走这里。** 不要直接调 load_config()。

    最多陈旧 config_ttl_seconds() 秒。线程安全。
    """
    global _CACHE, _CACHE_STAMP, _CACHE_CHECKED_AT

    now = time.monotonic()
    cache = _CACHE
    if cache is not None and (now - _CACHE_CHECKED_AT) < config_ttl_seconds():
        return cache

    with _CACHE_LOCK:
        # 双检: 可能在等锁期间别的线程已经刷过了
        now = time.monotonic()
        if _CACHE is not None and (now - _CACHE_CHECKED_AT) < config_ttl_seconds():
            return _CACHE

        stamp = _config_stamp()
        if _CACHE is not None and stamp == _CACHE_STAMP:
            # 源没变 —— 只续期, 不重新解析
            _CACHE_CHECKED_AT = now
            return _CACHE

        try:
            fresh = _assemble_config()
        except Exception:
            # 重载失败 (yaml 被写坏 / 正在被写) 时**继续用上一份好的配置**,
            # 而不是让整个 gateway 502。配置写坏是运维事故, 不该演变成全站故障。
            # 但不能静默: 抛给上层日志, 且不更新 stamp —— 下个周期还会再试。
            if _CACHE is not None:
                _CACHE_CHECKED_AT = now
                logger.exception("重载 models 配置失败, 继续沿用上一份 (源: %s)", resolve_config_path())
                return _CACHE
            raise  # 首次加载就失败 —— 没有可沿用的, 必须让它挂

        _CACHE = fresh
        _CACHE_STAMP = stamp
        _CACHE_CHECKED_AT = now
        return fresh


def invalidate_config() -> None:
    """强制下次 get_config() 回源.

    写接口改完配置后调一次 —— 让**本 worker** 立刻看到新值。其余 worker
    靠 TTL 自己收敛 (见上面为什么不做跨进程失效)。
    """
    global _CACHE_CHECKED_AT, _CACHE_STAMP
    with _CACHE_LOCK:
        _CACHE_CHECKED_AT = 0.0
        _CACHE_STAMP = None
