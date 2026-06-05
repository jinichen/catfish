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

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

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

    @property
    def api_key(self) -> str:
        """Read the API key from env at request time (not baked into config)."""
        key = os.environ.get(self.api_key_env)
        if not key:
            raise RuntimeError(
                f"env variable {self.api_key_env} is not set -- cannot call model '{self.model}'"
            )
        return key

    @property
    def is_available(self) -> bool:
        """True if the required API key is configured in the environment."""
        return bool(os.environ.get(self.api_key_env))


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

    # P1: 上游失败时自动切到 chain 里下一个模型。空 chain (默认) 表示不 fallback。
    fallback: FallbackConfig | None = None


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


class Config(BaseModel):
    version: int = 1
    models: list[ModelConfig]
    mcp_registry: McpRegistryConfig = Field(default_factory=McpRegistryConfig)
    skills_hub: SkillsHubConfig = Field(default_factory=SkillsHubConfig)

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


def load_config(path: Path | None = None) -> Config:
    """Load models.yaml. Path 优先级 (高→低):
        1. caller path 参数
        2. CATFISH_CONFIG env (单文件 override)
        3. CATFISH_GATEWAY_CONFIG_PATH/models.yaml (P26: 统一目录 env)
        4. config/models.yaml (repo 内默认)
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

    if not path.is_absolute():
        # try both cwd and the installed package dir
        candidates = [Path.cwd() / path, Path(__file__).parent.parent.parent / path]
        for c in candidates:
            if c.exists():
                path = c
                break

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
