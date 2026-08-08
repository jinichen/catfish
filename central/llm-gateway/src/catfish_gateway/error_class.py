"""把上游错误归成**封闭词表**里的一个码 —— 中央端不存自由文本。

# 为什么

`docs/CENTRAL-EDGE-DATA-BOUNDARY.md` 把 gateway_audit 允许的字段列死了:

    (ts, user_email, dept, model, latency_ms, status, cache_*_tokens)
    —— 不含 prompt/response 内容

**`error` 不在这张表里。** 而它存的是上游异常的原文, 代码里那行的注释自己
就写着会带什么:

    # Truncate to avoid accidentally leaking upstream prompt echoes in errors.
    record["error"] = scrub_credentials_in_text(error)[:200]

也就是说: 靠"截断 200 字 + 脱凭据"来兜住"可能回显 prompt", 而这个字段还会
显示在中央管理面板 (web/src/routes/admin/QuotaEventsPage.tsx 既展示又导 CSV)。

8/8 实测 3058 条带 error 的记录, **0 条含员工内容** —— 全是 provider 异常串。
但那是运气不是保证: 字段结构上不受控, 上游返什么就存什么。

hermes v0.20 的 monitoring plane 把这条写成设计原则:
「content-free by design: rendered log messages are not exported」。

# 做法

归类, 不转述。上游原文**只进网关自己的运行日志** (ops 排查用, 今天那条
DeepSeek 402 的完整 traceback 就是在那儿看到的), 审计记录里只留一个码。

封闭词表的意义: 它是**枚举**, 不是摘要。枚举里的每一个值都是我们自己写死的
常量, 上游再怎么回显员工内容也不可能变成其中之一。截断和脱敏做不到这一点 ——
它们处理的还是上游那串东西。

# 词表怎么定的

按"运维看到这个码之后下一步做什么"分, 不按 HTTP 状态或异常类名分 ——
后者对着 litellm 那一层的分类抖动 (今天 402 就被它映射成 BadRequestError)。
"""
from __future__ import annotations

#: 封闭词表。改这里要同时改 tests/test_error_class.py 里那条结构性测试。
#:
#: 每一项后面是"看到它该干什么":
ERROR_CLASSES = (
    "insufficient_balance",   # 充值 / 换模型
    "rate_limit",             # 等一会儿 / 换模型
    "quota_exhausted",        # 配额用光 (免费额度那类), 等周期或换模型
    "auth",                   # key 过期 / 权限不对
    "timeout",                # 上游慢, 看是不是 prompt 太大
    "connection",             # 网络 / VPN / 内网不可达
    "upstream_5xx",           # 上游自己挂了, 等或切
    "bad_request",            # 我们发的东西不对 (schema / 参数), 要看日志改代码
    "model_not_found",        # 模型名不对 / 没配
    "content_filter",         # 上游内容策略拦的
    "unknown",                # 兜底 —— 这个比例高就说明词表该补了
)

#: 判据按顺序试, **第一个命中的赢**。顺序不是随意的:
#: 具体的排前面, 宽泛的排后面 (例: "insufficient balance" 必须排在
#: "bad_request" 前面 —— DeepSeek 的余额不足就是被 litellm 映射成 400 的)。
_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("insufficient_balance", (
        "insufficient balance", "insufficient_balance",
        "insufficient quota", "insufficient_quota",
        "payment required", "arrearage", "余额不足", "欠费",
    )),
    ("quota_exhausted", (
        "free tier", "freetieronly", "allocationquota",
        "quota exceeded", "resource_exhausted", "resource exhausted",
    )),
    ("rate_limit", ("rate limit", "ratelimit", "too many requests", "429")),
    ("auth", (
        "unauthorized", "invalid api key", "invalid_api_key",
        "authentication", "permission denied", "forbidden", "401", "403",
    )),
    ("timeout", ("timeout", "timed out", "deadline exceeded")),
    ("connection", (
        "connection error", "connection refused", "cannot connect",
        "connect call failed", "broken pipe", "apiconnectionerror",
        "unable to get json response", "name or service not known",
    )),
    ("model_not_found", ("model not found", "does not exist", "unknown model")),
    ("content_filter", ("content filter", "content_policy", "safety")),
    ("upstream_5xx", (
        "internalservererror", "internal server error", "bad gateway",
        "service unavailable", "upstream error", "502", "503", "504", "500",
    )),
    ("bad_request", ("badrequest", "bad request", "invalid schema", "400")),
)


def classify_upstream_error(text: str | None) -> str:
    """把上游错误原文归成 ERROR_CLASSES 里的一个。

    **返回值永远是词表里的常量**, 不含任何来自 text 的片段 —— 这是这个函数
    存在的全部意义, 别为了"更好排查"往返回值里拼原文。

    永不抛: 归类失败返 "unknown"。审计少一个精确分类, 好过归类器把请求搞挂。
    """
    if not text or not isinstance(text, str):
        return "unknown"
    try:
        low = text.lower()
        for name, keys in _RULES:
            if any(k in low for k in keys):
                return name
        return "unknown"
    except Exception:  # noqa: BLE001 — 归类器不该抛, 抛了也只降级不影响主流程
        return "unknown"
