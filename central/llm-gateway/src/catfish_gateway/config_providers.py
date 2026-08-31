"""把「供应商」合并进模型的 upstream (8/1).

## 这个文件存在的全部意义: 让 30 个调用点一个都不用改

全仓读 upstream 的地方有 30 处, 全部走这五个成员:

    m.upstream.model         6 处      m.upstream.api_key       3 处
    m.upstream.api_base      4 处      m.upstream.timeout       2 处
    m.upstream.is_available  9 处      m.upstream.api_key_env   2 处 (报错文案)

分布在 network.py / app.py / facts_pipeline.py / fallback.py / catalog.py /
internal_models.py / user_model_resolver.py。

所以拆分的做法是: **UpstreamConfig 的对外接口一个字不改**, 只改它的值从哪来。
库里模型的 upstream 从

    {"model": "openai/x", "api_base": "http://…", "api_key_env": "K", "timeout": 180}

变成

    {"model": "openai/x", "provider": "internal-qwen-vision", "timeout": 180}

而这里在组装配置时把 provider 行合并回前一种形态, 再交给
`ModelConfig.model_validate`。下游拿到的东西跟改造前逐字段相同。

**判据**: 这一步做完如果还需要改那 30 个调用点, 说明合并的位置选错了,
方案要重想 (见 docs/DESIGN-PROVIDER-SPLIT-20260730.md §10)。

## timeout 为什么留在模型这一层

它是模型属性不是供应商属性 —— 同一个内网 vLLM 上, 视觉模型 180s、
embedding 30s。供应商那份只是默认值, 模型没写才用它。

## 迁移期两种形态并存

没有 `provider` 键的模型 (也就是现在库里那 7 个) 原样通过。这是
DESIGN §6.2 "第一步不碰 key" 的基础: 建表 + 生成 provider 之后, 老形态和
新形态可以同时存在, 一个一个迁, 中间随时可停。
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

#: 模型 upstream 里引用供应商的键名
PROVIDER_KEY = "provider"

#: `${VAR}` / `${VAR:-default}` 里的变量名。
#:
#: 不复用 config._ENV_PATTERN 是因为 config 会 import 这个模块 (合并逻辑),
#: 反过来 import 就成环了。这里只需要抓变量名、不做替换, 比那个简单一半。
_PLACEHOLDER_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)")


def merge_provider(
    row: dict[str, Any],
    providers: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], str | None]:
    """把 provider 行合并进模型 row 的 upstream. 返回 (新 row, 出错说明或 None).

    出错时返回**原样的 row**, 让上层照常把这个模型放进列表 —— 跟
    interpolate_model_row 同一个原则: 单个模型的问题不该掀翻整份配置,
    但必须能在界面上看到原因 (见 7/30 那条教训)。

    这种模型的 upstream 会缺 api_base / api_key_env, `is_available` 因此返
    False, 于是它不会出现在员工的模型选择里, 也不会被 fallback 链选中 ——
    坏得安静但不会坏到别人。
    """
    up = row.get("upstream")
    if not isinstance(up, dict):
        return row, None  # 结构不对, 交给 pydantic 去报

    pid = up.get(PROVIDER_KEY)
    if not pid:
        return row, None  # 老形态 (upstream 里直接写 api_base/api_key_env), 原样

    p = providers.get(pid)
    if p is None:
        return row, (
            f"引用了不存在的供应商 {pid!r} —— 可能是供应商被删了, "
            f"或者这条模型是从别的环境导入的。到「供应商」页新建一个同名的, "
            f"或者把这个模型改到别的供应商上。"
        )

    # provider 键**保留**, 不删 —— UpstreamConfig 上有这个字段 (8/1),
    # 删掉的话模型存回库时关联就丢了。留着也让运行时配置知道自己来自哪家,
    # 报错信息里能点名。
    merged = dict(up)
    merged["api_base"] = p.get("api_base")
    # api_key_env 可能为 None (第二步之后 key 存库的供应商)。
    # UpstreamConfig.api_key_env 是 `str` 有默认值, 传 None 会被 pydantic 拒,
    # 所以给空串 —— is_available 读 os.environ.get("") 得 None, 正好表示
    # "这个供应商没配 env key", 语义是对的。
    merged["api_key_env"] = p.get("api_key_env") or ""
    # timeout: 模型自己写了就用自己的 (视觉 180 / embedding 30 是模型属性),
    # 没写才落到供应商的默认值。
    if not merged.get("timeout"):
        merged["timeout"] = p.get("timeout") or 60

    return {**row, "upstream": merged}, None


def split_upstream(up: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """把一条老形态 upstream 拆成 (供应商标识键, 建议的 provider id, provider 行).

    去重键是 `(api_base, api_key_env)` —— 这两个一样就是同一家。
    实查现有 7 个模型, 按这个键去重得到 **6** 个供应商 —— 唯一重复的是
    gemini 那一对 (gemini-pro 和 gemini-flash 都没有 api_base、都用
    GEMINI_API_KEY)。三个内网端点虽然共用 INTERNAL_LLM_KEY, 但 api_base
    不同, 是三家。

    (设计初稿写的是 5 个, 那是算错了。测试 test_七个模型拆成六个供应商
     用真实数据把这个数钉住。)

    返回的 id 只是**建议值**, 真正落库时由 caller 处理重名 (见
    provider_store.migrate_models_to_providers)。
    """
    base = (up.get("api_base") or "").strip()
    key_env = (up.get("api_key_env") or "").strip()
    dedupe_key = f"{base}|{key_env}"
    return dedupe_key, suggest_provider_id(base, key_env), {
        "api_base": base or None,
        "api_key_env": key_env or None,
        "timeout": up.get("timeout") or 60,
    }


def suggest_provider_id(api_base: str, api_key_env: str) -> str:
    """给去重出来的供应商起一个人能读懂的 id.

    优先级:

      1. **api_base 是 ${VAR} 占位符 → 用变量名。** 迁移时读的是库里的原样值
         (库存占位符, 见 config_env), 所以内网那几个必然长这样。而变量名恰恰
         是信息最全的来源: INTERNAL_LLM_BASE_QWEN_MAIN / _QWEN_VISION /
         _BGE_M3 三个不同, 直接得到三个可区分的 id。
         反过来若按 IP 域名取, 三个都取不出东西, 只能落成
         internal-llm / internal-llm-2 / internal-llm-3 —— 那种编号 id 在
         下拉框里等于让人猜。
      2. 公网域名 (dashscope.aliyuncs.com → dashscope)
      3. 没有 api_base 的 (Gemini) 用 key 变量名 (GEMINI_API_KEY → gemini)
      4. 内网 IP 域名取不出东西 → 同 3, 重名由 caller 补后缀

    id 会出现在模型配置的 upstream.provider 里, 也会出现在界面的下拉框上,
    所以不能是随机串 —— 客户对着 `prov_a3f9` 没法判断该选哪个。
    """
    # 1. 占位符: 从变量名取。BASE/URL/ENDPOINT 是零信息的噪音词, 去掉。
    m = _PLACEHOLDER_VAR.search(api_base or "")
    if m:
        words = [w for w in m.group(1).lower().split("_") if w not in ("base", "url", "endpoint")]
        if words:
            return _slug("-".join(words))

    host = ""
    if api_base:
        rest = api_base.split("://", 1)[-1]
        host = rest.split("/", 1)[0].split(":", 1)[0]

    # 公网域名: 取主域 (dashscope.aliyuncs.com → dashscope,
    # api.deepseek.com → deepseek —— 跳过 api/www 这类无信息前缀)
    if host and not _looks_like_ip(host):
        parts = [p for p in host.split(".") if p not in ("api", "www", "com", "cn", "net", "org")]
        if parts:
            return _slug(parts[0])

    # 内网 IP 或没有 api_base → 用 key 变量名
    if api_key_env:
        s = api_key_env.lower()
        for suffix in ("_api_key", "_key", "_token"):
            if s.endswith(suffix):
                s = s[: -len(suffix)]
                break
        if s:
            return _slug(s)

    return "provider"


def _looks_like_ip(host: str) -> bool:
    parts = host.split(".")
    return len(parts) == 4 and all(p.isdigit() for p in parts)


def _slug(s: str) -> str:
    out = "".join(c if (c.isalnum() or c == "-") else "-" for c in s.lower())
    return out.strip("-") or "provider"
