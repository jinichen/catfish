"""BL-FALLBACK-500 (5/14 凌晨) — yaml 级回归测.

跑法: cd central/llm-gateway && PYTHONPATH=src python -m pytest tests/test_fallback_500_policy.py -q

# 背景

5/14 凌晨鸿波撞 deepseek-v4-flash 500 没自动切, 排查发现 fallback chain 的
on_errors trigger 列表历史漏 500. 修法: 公网链 (qwen-flash / deepseek-flash /
gemini-pro / gemini-flash) 加 500 trigger; 内网链 (private-main / private-vision)
**不加** 500 — 防内网内容因 500 走公网, 违 SOUL_FFCS 国央企保密原则.

# 这个测试守护啥

下次有人改 models.yaml (e.g. "新加 fallback / 调整 trigger") 时, 如果不小心:
  - 公网链漏了 500 → fallback 链对 500 不工作, 用户撞 500 没自动切
  - 内网链加了 500 → 内网 prompt 因 500 → 公网, 客户 IT 看到日志炸

任何一边不对都让 CI 红, 强制改的人重新评估再 ship.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml


PUBLIC_MODELS_REQUIRING_500: set[str] = {
    "catfish-public-qwen-flash",
    "catfish-public-deepseek-flash",
    "catfish-public-gemini-pro",
    "catfish-public-gemini-flash",
}

# 8/8: 拿掉了 catfish-private-main —— 它已从本部署的 models.yaml 删除
# (内网 122B 下线)。留着的话 test_all_named_models_exist 会红在"模型不存在",
# 而那条断言的本意是"防改名导致保护空跑", 不是"禁止删模型"。
#
# 但直接删掉名字就少了一层保护, 所以下面补了一条**结构性**检查
# (test_所有内网模型的链都不含500): 不认名字, 只看 tier=private。
# 这比硬编码强 —— 新加的内网模型自动受保护, 删模型也不会让它空跑。
# 这里保留具名清单是因为它还兼着"这个名字必须存在"的守护作用。
PRIVATE_MODELS_REJECTING_500: set[str] = {
    "catfish-private-vision",
}


@pytest.fixture
def models_yaml() -> dict:
    """读真 config/models.yaml (不是 .example), 这是生产配置."""
    p = (
        Path(__file__).resolve().parent.parent
        / "config"
        / "models.yaml"
    )
    assert p.exists(), f"models.yaml 不存在: {p}"
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _model_by_name(cfg: dict, name: str) -> dict | None:
    for m in cfg.get("models", []) or []:
        if m.get("name") == name:
            return m
    return None


# ── 公网链必须含 500 ─────────────────────────────────────


@pytest.mark.parametrize("model_name", sorted(PUBLIC_MODELS_REQUIRING_500))
def test_public_chain_contains_500(models_yaml, model_name):
    """公网链 trigger 必须含 500 — 公网到公网切没保密问题, 用户体验优先."""
    m = _model_by_name(models_yaml, model_name)
    assert m is not None, f"模型 {model_name} 在 models.yaml 不存在"
    fb = m.get("fallback")
    assert fb is not None, f"{model_name} 没配 fallback"
    on_errors = fb.get("on_errors") or []
    assert 500 in on_errors, (
        f"{model_name} 是公网模型, on_errors 必须含 500. "
        f"BL-FALLBACK-500 (5/14): 鸿波撞 deepseek 500 没切, 修法是 4 条公网链全加 500. "
        f"当前 on_errors = {on_errors}"
    )


# ── 内网链必须不含 500 (防泄密) ──────────────────────────


@pytest.mark.parametrize("model_name", sorted(PRIVATE_MODELS_REJECTING_500))
def test_private_chain_rejects_500(models_yaml, model_name):
    """内网链 trigger 不能含 500 — 内网 500 走公网 = 内网 prompt 泄到公网,
    违反 SOUL_FFCS 国央企保密原则.
    """
    m = _model_by_name(models_yaml, model_name)
    assert m is not None, f"模型 {model_name} 在 models.yaml 不存在"
    fb = m.get("fallback")
    if fb is None:
        # 没 fallback 当然没 500, 没问题
        return
    on_errors = fb.get("on_errors") or []
    assert 500 not in on_errors, (
        f"{model_name} 是内网模型, on_errors 不能含 500. "
        f"内网 500 → 公网链 = 内网 prompt 泄到公网, 违 SOUL_FFCS 保密原则. "
        f"当前 on_errors = {on_errors}. "
        f"如果真要加, 必须先评估 prompt 内容是否能走公网."
    )


# ── 完整性 sanity check ──────────────────────────────────


def test_all_named_models_exist(models_yaml):
    """守护: PUBLIC_MODELS_REQUIRING_500 / PRIVATE_MODELS_REJECTING_500 里
    列的模型必须真在 models.yaml 里 — 防模型改名后这个测变成空跑.
    """
    yaml_names = {
        m.get("name") for m in (models_yaml.get("models") or []) if m.get("name")
    }
    missing_pub = PUBLIC_MODELS_REQUIRING_500 - yaml_names
    missing_priv = PRIVATE_MODELS_REJECTING_500 - yaml_names
    assert not missing_pub, (
        f"PUBLIC_MODELS_REQUIRING_500 里以下模型在 models.yaml 不存在 — "
        f"模型可能改名了, 这个测保护就失效了, 请改本文件: {missing_pub}"
    )
    assert not missing_priv, (
        f"PRIVATE_MODELS_REJECTING_500 里以下模型在 models.yaml 不存在 — "
        f"模型可能改名了, 这个测保护就失效了, 请改本文件: {missing_priv}"
    )


def test_所有内网模型的链都不含500(models_yaml):
    """结构性版本: 不认具体名字, 只看 tier == private (8/8 加).

    ## 为什么要在具名清单之外再来一条

    上面 PRIVATE_MODELS_REJECTING_500 是硬编码的名字。它有两个失效方式:

      · 新加一个内网模型, 没人记得往清单里加 → 这个模型不受保护
      · 删掉清单里的模型 (8/8 就删了 catfish-private-main) → 要么测试红在
        "模型不存在" (跟保密毫无关系的假警报), 要么有人图省事把名字删了,
        保护跟着一起没了

    保密约束的真实主语是「tier=private 的模型」, 不是某几个名字。按 tier 判,
    上面两种情况自动覆盖。

    ## 约束本身

    内网模型撞 500 时若走 public chain, 内网 prompt 就出端了 —— 违 SOUL_FFCS
    国央企保密原则。所以内网模型的 on_errors 一律不能含 500。
    (429/502/503/504/timeout 这些是"上游没接住", 不代表 prompt 已被处理,
     切公网重发的语义不同 —— 那部分是既有设计, 本测不动。)
    """
    offenders = []
    for m in models_yaml.get("models") or []:
        if m.get("tier") != "private":
            continue
        fb = m.get("fallback")
        if not fb:
            continue  # 没链当然不会外流
        if 500 in (fb.get("on_errors") or []):
            offenders.append(m.get("name"))
    assert not offenders, (
        f"内网模型的 on_errors 含 500: {offenders}。"
        "内网 500 → 公网链 = 内网 prompt 泄到公网, 违 SOUL_FFCS 保密原则。"
        "真要加必须先评估 prompt 内容能不能走公网。"
    )


def test_每条_fallback_链引用的模型都存在(models_yaml):
    """所有 fallback chain 里的名字必须真在 models.yaml 里.

    ## 为什么要有这条

    上面那条 test_all_named_models_exist 只守护本文件硬编码的 6 个名字。
    这条守护**所有** chain 引用, 不管将来加多少模型。

    改名踩坑已经发生过两次 (2026-04-28 / 2026-06-24), 第二次的 5 周里:
      · 5 条 chain 的首选备用模型解析不到, 被 resolve_chain 静默跳过
      · 前端审计页显示"未知模型", 计价按兜底价算, 成本高估 2.22 倍

    两次都不是有人故意改坏, 而是改名时没意识到别处在引用。靠注释提醒
    (models.yaml 里那段"不要再改 name") 已经证明不管用 —— 第二次改名的人
    根本没看到那段注释, 因为改动夹在一个不相干的提交里。

    所以要一条**结构性**的检查: 不针对具体名字, 只要 chain 指向不存在的
    模型就红。这样将来任何改名都会当场暴露, 不依赖任何人记得去 grep。

    ## 为什么不能只靠运行时日志

    resolve_chain 遇到不存在的模型是 logger.warning + continue —— 不中断,
    员工侧完全无感。生产日志里那行 warning 五周没人看到。
    """
    models = models_yaml.get("models") or []
    names = {m.get("name") for m in models if m.get("name")}

    broken: list[str] = []
    for m in models:
        chain = ((m.get("fallback") or {}).get("chain")) or []
        for ref in chain:
            if ref not in names:
                broken.append(f"{m.get('name')} 的 chain 引用了不存在的 {ref!r}")

    assert not broken, (
        "fallback chain 指向不存在的模型 —— 这些跳会被 resolve_chain 静默跳过, "
        "只留一行日志, 员工侧无感:\n  " + "\n  ".join(broken) +
        f"\n\n当前存在的模型: {sorted(names)}"
    )


def test_不该有模型的_chain_引用自己(models_yaml):
    """自引用会被 resolve_chain 跳过并 warn, 属于写错了.

    单独列一条是因为它跟"引用不存在的模型"表现一样 (静默少一跳), 但根因
    不同 —— 前者是改名漏改, 后者是复制粘贴时没改 chain。
    """
    bad = [
        m.get("name")
        for m in (models_yaml.get("models") or [])
        if m.get("name") in (((m.get("fallback") or {}).get("chain")) or [])
    ]
    assert not bad, f"这些模型的 fallback chain 引用了自己: {bad}"


# ── 8/8: 余额不足必须触发 fallback ────────────────────────────────────
#
# 实撞: DeepSeek 余额烧光, 早安页 + 邮件评级全线停摆, 而 fallback **没触发**:
#
#   Client error '402 Payment Required' for url 'https://api.deepseek.com/...'
#   litellm.BadRequestError: DeepseekException -
#       {"error":{"message":"Insufficient Balance","code":"invalid_request_error"}}
#   catfish.gateway.fallback: err=BadRequestError 不在 on_errors 里, 不 fallback
#
# 两道判据都没接住: LiteLLM 把 402 重映射成 BadRequestError (status=400, 402 根本
# 不出现), 而关键词表里 "rate limit" 组只有 "quota", 沾不上 "Insufficient Balance"。


def test_余额不足能触发_fallback():
    """拿 8/8 那条真实报文当用例 —— 一字不改。"""
    from catfish_gateway.fallback import should_fallback

    real = (
        'litellm.BadRequestError: DeepseekException - '
        '{"error":{"message":"Insufficient Balance","type":"unknown_error",'
        '"param":null,"code":"invalid_request_error"}}'
    )

    class _BadRequest(Exception):
        status_code = 400          # LiteLLM 实际给的就是 400, 不是 402

    exc = _BadRequest(real)
    on_errors = [429, 500, 502, 503, 504, "timeout", "rate limit", "insufficient balance"]
    assert should_fallback(exc, on_errors), "余额不足必须切模型 —— 换一家立刻能用"


def test_没配这个关键词的话不切_证明是这一条在起作用():
    """反证: 去掉关键词就切不了, 说明命中的确实是它, 不是别的条目蒙对的。"""
    from catfish_gateway.fallback import should_fallback

    class _BadRequest(Exception):
        status_code = 400

    exc = _BadRequest("DeepseekException - Insufficient Balance")
    assert not should_fallback(exc, [429, 500, 502, 503, 504, "timeout", "rate limit"])


@pytest.mark.parametrize("msg", [
    "Insufficient Balance",                    # DeepSeek
    "insufficient_quota",                      # OpenAI
    "402 Payment Required",                    # 原始 HTTP 语义
    "Arrearage: account in debt",              # 阿里云百炼
    "账户余额不足, 请充值",                      # 中文上游
    "当前账号已欠费",
])
def test_各家的说法都认得(msg):
    from catfish_gateway.fallback import should_fallback

    class _E(Exception):
        status_code = 400

    assert should_fallback(_E(msg), ["insufficient balance"]), msg


def test_不误伤正常报文():
    """'billing' 这种宽词故意没收 —— 误切比不切更难查。"""
    from catfish_gateway.fallback import should_fallback

    class _E(Exception):
        status_code = 400

    for msg in [
        "invalid request: messages must not be empty",
        "model not found: gpt-5.6-luna",
        "see your billing dashboard for details",   # 有 billing 但不是欠费
    ]:
        assert not should_fallback(_E(msg), ["insufficient balance"]), msg


def test_公网模型全都配了这个关键词_内网一个都不配(models_yaml):
    """结构性: 新增公网模型忘了配 → 红。内网模型配了也红 (它没有余额概念,
    配上去只会让人以为内网也会因为欠费切到公网)。"""
    for m in models_yaml["models"]:
        fb = m.get("fallback")
        if not fb:
            continue
        has = "insufficient balance" in [
            str(x).lower() for x in fb.get("on_errors", [])
        ]
        if m.get("tier") == "private":
            assert not has, f"内网模型 {m['name']} 不该配余额关键词"
        else:
            assert has, f"公网模型 {m['name']} 的 on_errors 缺 'insufficient balance'"
