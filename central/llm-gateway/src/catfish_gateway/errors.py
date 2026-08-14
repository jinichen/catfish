"""错误信息翻译 — 把 LiteLLM / OpenAI / 网络层异常 trace 转成员工能看的话.

设计独立模块, 不依赖 litellm/fastapi, 让测试可以独立 import 跑.
"""
from __future__ import annotations

import datetime as _dt
import re

# 配额/余额烧光的关键词。
#
# ⚠ fallback.py 的 _ERROR_KEYWORDS["insufficient balance"] 里有一份更全的。
# 两处**故意**分开, 因为问的不是同一个问题:
#   fallback.py:  要不要自动切到别的模型 (是产品/合规决策, 8/9 鸿波定的 opt-in)
#   这里:         给员工什么建议 (纯文案, 跟切不切无关)
# 防漂移靠 tests/test_friendly_error.py::test_配额词表是_fallback_那组的子集 ——
# 这边加词而那边没有, 测试就红。
_QUOTA_EXHAUSTED = (
    "insufficient balance",
    "insufficient_balance",
    "insufficient quota",
    "insufficient_quota",
    "payment required",
    "arrearage",
    "余额不足",
    "欠费",
)

# 上游报的恢复时间: "The quota will reset at 08-14 23:54:00 UTC."
# 只认明写 UTC 的 —— 没写时区的时间戳换算过去只会更误导。
_RESET_UTC_RE = re.compile(
    r"reset(?:s)?\s+at\s+(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*UTC",
    re.IGNORECASE,
)


def localize_reset_hint(raw: str, now: _dt.datetime | None = None) -> str | None:
    """把上游 UTC 的配额恢复时间换算成本机时区, 换不出来返 None。

    # 为什么要这个

    2026-08-15 现场: 网关日志打的是本地时间 `2026-08-15 06:32`, 上游报的是
    `reset at 08-14 23:54 UTC`。两个并排放着, 日期还差一天, 人的第一反应是
    "早该恢复了, 是不是我们哪儿坏了" —— 实际还差 1 小时 22 分。
    两个时区并排显示而不标注, 等于让人自己做时区换算, 而人在排查故障时不会做。

    上游只给月-日, 不给年。用当前年份补, 跨年时按"离现在最近"取舍。
    """
    m = _RESET_UTC_RE.search(raw)
    if not m:
        return None
    now = now or _dt.datetime.now(_dt.timezone.utc)
    month, day, hour, minute = (int(m.group(i)) for i in (1, 2, 3, 4))
    second = int(m.group(5) or 0)
    for year in (now.year, now.year - 1, now.year + 1):
        try:
            when = _dt.datetime(year, month, day, hour, minute, second, tzinfo=_dt.timezone.utc)
        except ValueError:
            continue  # 2-29 之类, 换个年份再试
        if abs((when - now).days) <= 180:
            break
    else:
        return None
    local = when.astimezone()
    delta_h = (when - now).total_seconds() / 3600
    if delta_h > 0:
        return f"配额 {local:%m-%d %H:%M} 恢复 (本地时间, 还有 {delta_h:.1f} 小时)"
    return f"配额 {local:%m-%d %H:%M} 应已恢复 (本地时间) —— 若仍报错就是别的原因"


def friendly_upstream_error(raw: str) -> str:
    """把 LiteLLM / OpenAI / Google 的 trace 转人话, 截断在 200 字符以内.

    覆盖 catfish 真实撞过的全部错误类别 (2026-04-27/28 调试时积累):

      4xx 上游拒绝:
        400 / BadRequest    → 请求格式错 (常见: 工具 schema 错 / image_url 不被这个模型支持 / 模型名错)
        401 / Unauthorized  → API Key 无效或过期
        403 / Forbidden     → 没权限调这个模型 / 区域受限
        404 / not found     → 模型在 dashscope 上不存在 (qwen3.6-max-preview 类)
        429 / rate limit    → 限流
      5xx 上游异常:
        500 / Internal      → 上游服务自己挂了
        502 / Bad Gateway   → 上游网关找不到服务 (内网 LLM endpoint UUID 失效)
        503 / overloaded    → 上游过载
        504 / Gateway Timeout → 上游响应慢
      网络层:
        Connection error / refused / cannot connect → 内网不通 (VPN/Clash 问题)
        Broken pipe / ClientOSError → 连接中途断
        APIConnectionError → openai sdk 抛, 一般是底层网络
      Provider 特有:
        RESOURCE_EXHAUSTED / quota exceeded → Gemini 免费配额耗尽
        Hermes/Gemini code_execution → 代码执行模式被拒 (我们的 gemini_guard 拦的)
    """
    low = raw.lower()

    # ===== Provider 特有错误 (优先匹配, 信号最强) =====
    if "resource_exhausted" in low or ("quota" in low and "exceed" in low):
        return "Gemini 免费配额今日耗尽 — 切到 Qwen 或明天再试"
    if "code_execution" in low and ("disabled" in low or "not allowed" in low or "拒" in low):
        return "Gemini 拒绝了带工具的请求 — gemini_guard 问题, 反馈给鸿波"

    # ===== 4xx 客户端错误 =====
    # ⚠ 配额烧光必须排在 429 之前。
    #
    # 上游把"周配额用尽"也报成 429, 于是它会被下面那条接住, 员工看到的建议是
    # "等几秒再试" —— 而真实情况是等 1.4 小时或者换模型。2026-08-15 现场就是
    # 这么误导过一次。两件事的处理方式完全相反, 不能共用一句文案:
    #   限流   = 瞬时, 等几秒就好, 同一个模型能继续用
    #   配额尽 = 账务状态, 等几秒没用, 必须换模型或等恢复
    if any(k in low for k in _QUOTA_EXHAUSTED):
        hint = localize_reset_hint(raw)
        base = "上游配额/余额已用尽 (不是限流, 等几秒没用) — 换个模型, 或等配额恢复"
        return f"{base}\n{hint}" if hint else base
    if " 429" in f" {low} " or "rate limit" in low or "ratelimit" in low or "too many requests" in low:
        return "调用频率超限 (429) — 等几秒再试 / 换个模型"
    if " 401" in f" {low} " or "unauthorized" in low or "invalid api key" in low or "incorrect api key" in low:
        return "API Key 无效 (401) — 检查 .env 的 key 是否过期 / 写错"
    # BL-PROVIDER-AUTH-CN (7/18 鸿波 catch WeChat 显英文): litellm 新版返
    # "Provider authentication failed. Check the configured credentials; raw provider
    # details are in the gateway logs." 兜底路径直接返英文, 员工看不懂. 加中文匹配.
    if "provider authentication failed" in low or "check the configured credentials" in low:
        return "上游 LLM Provider 鉴权挂了 — 检查 .env 里 DEEPSEEK_API_KEY / DASHSCOPE_API_KEY / GEMINI_API_KEY 是否配 / 过期. 查 gateway log 详情."
    # BL-PROVIDER-RETRIES-CN (7/19 鸿波 catch WeChat 显英文 · LiteLLM 兜底串):
    # "The model provider failed after retries. I kept raw provider details out of chat;
    # check gateway logs for diagnostics." 触发场景 · gateway 用 LiteLLM 调上游 · 连
    # 续 retry 全挂 · gateway 回这兜底串. 员工看不懂 · 加中文匹配.
    if ("model provider failed after retries" in low
        or "kept raw provider details out of chat" in low
        or "check gateway logs for diagnostics" in low):
        return "上游 LLM 连续重试仍挂 — 大概率 API Key 失效 / 上游过载 / 网络挂. 查 gateway log: tail -50 ~/catfish-gateway-$(date +%Y%m%d).log"
    if " 403" in f" {low} " or "forbidden" in low or "permission denied" in low:
        return "没权限调这个模型 (403) — 公司账号未开通 / 区域受限"
    if " 404" in f" {low} " or ("model" in low and "not found" in low) or "找不到服务" in raw:
        return "上游说没这个模型 (404) — 检查 models.yaml 的 upstream.model 名字对不对"
    if " 400" in f" {low} " or "bad request" in low or "badrequest" in low:
        # 400 最容易撞但原因多样, 提示员工常见可能性
        # Go protobuf 解析错 — 大概率多模态发给非 vision 模型 (上游 Go 服务用 protobuf 解析)
        # 踩过坑 2026-04-28 鸿波: catfish-private-main 收到截图 → proto syntax error.
        # 现在 multimodal_guard 已经在前面拦了, 但 fallback 链可能还撞.
        if "proto" in low and "syntax" in low and "invalid value" in low:
            return "请求格式上游不认 (400) — 大概率截图发给了非 vision 模型. multimodal_guard 应该自动切, 切完还撞就是 bug"
        if "image" in low or "image_url" in low or "vision" in low:
            return "请求带图但模型不支持视觉 (400) — Companion 应该自动切视觉模型, 没切就是 bug"
        if "tool" in low or "function" in low:
            return "工具调用格式错 (400) — tool schema 可能缺字段, 反馈给鸿波"
        return "请求格式错 (400) — 模型名 / 参数 / 工具 schema 有一个不对"

    # ===== 5xx 上游服务器错误 =====
    if " 502" in f" {low} " or "bad gateway" in low:
        return "上游网关异常 (502) — 内网 LLM endpoint 可能 UUID 失效, 联系平台管理员或切换模型"
    if " 504" in f" {low} " or "gateway timeout" in low:
        return "上游网关超时 (504) — 上游慢, 稍后再试 / 换模型"
    if " 503" in f" {low} " or "overloaded" in low or "service unavailable" in low:
        return "上游过载 (503) — 稍后再试或换模型"
    if " 500" in f" {low} " or "internal server error" in low or "internalservererror" in low:
        return "上游内部错误 (500) — 上游服务自己挂了, 换模型或稍后试"

    # ===== 网络层错误 =====
    if "connection refused" in low or "connect call failed" in low:
        return "网络层拒绝连接 — 内网服务没在跑 / VPN 没连上 / Clash 把内网代理了"
    if "cannot connect" in low or "connectionerror" in low or "apiconnectionerror" in low:
        return "网络层连接失败 — 检查 VPN / Clash / 内网状态"
    if "broken pipe" in low or "clientoserror" in low:
        return "连接中途断了 — 网络抖动, 重试一次"
    if "timeout" in low or "timed out" in low:
        return "上游响应超时 — 网络慢或上游慢, 稍后再试"

    # ===== 兜底 =====
    return raw[:200]
