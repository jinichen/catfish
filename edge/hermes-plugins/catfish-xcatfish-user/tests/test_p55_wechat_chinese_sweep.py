"""10/13 全量盘点后的一次性收尾: 危险命令原因全中文 / 审批超时告诉员工 / 微信外发提示全中文。

不 import hermes。危险命令清单直接读 hermes 源码 (本机装了才跑, CI 跳过)。
"""
from __future__ import annotations

import ast
import logging
import os
from pathlib import Path

import pytest

import plugin_approval_ux as ux
import plugin_weixin_zh as p28

HERMES = Path(os.environ.get("HERMES_ROOT") or os.path.expanduser("~/.hermes/hermes-agent"))


def _dangerous_descriptions():
    src = HERMES / "tools" / "approval.py"
    if not src.exists():
        pytest.skip("本机没装 hermes")
    tree = ast.parse(src.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "DANGEROUS_PATTERNS" for t in node.targets):
            out = []
            for elt in node.value.elts:
                if isinstance(elt, ast.Tuple) and len(elt.elts) >= 2:
                    d = elt.elts[1]
                    if isinstance(d, ast.Constant) and isinstance(d.value, str):
                        out.append(d.value)
            return out
    pytest.fail("hermes 源码里找不到 DANGEROUS_PATTERNS —— 结构变了, 看一眼 P51 的 DANGER_ZH")


def test_every_hermes_dangerous_pattern_has_chinese():
    descs = _dangerous_descriptions()
    assert len(descs) > 50
    missing = sorted(set(d for d in descs if d not in ux.DANGER_ZH))
    assert not missing, f"DANGER_ZH 缺这些 (hermes 新加的?): {missing}"


def test_reason_translation_joins_dedups_and_never_leaks_english(caplog):
    assert ux.translate_reason("recursive delete") == "成批删除文件"
    assert ux.translate_reason("recursive delete; recursive delete (long flag); SQL DROP") == \
        "成批删除文件、删除数据库表"
    assert ux.translate_reason("arbitrary program execution via env -S") == "通过别的程序运行任意命令"
    assert ux.translate_reason("Security scan — [HIGH] something") == "安全扫描发现了风险"
    with caplog.at_level(logging.WARNING):
        zh = ux.translate_reason("brand new pattern hermes added tomorrow")
    assert zh == "可能删改重要数据或影响系统"
    assert "DANGER_ZH" in caplog.text                    # 打 log, 下次补表
    assert ux.translate_reason("") == "可能有风险的命令"


# ── P55 审批超时 ───────────────────────────────────────────────────────

TIMEOUT = {"approved": False, "message": "BLOCKED: execute_code script timed out without user "
                                         "response. The user has NOT consented to running this code."}


def test_timeout_result_tells_the_model_to_tell_the_user():
    out = ux.add_timeout_note(TIMEOUT)
    assert out["message"].startswith(TIMEOUT["message"])
    assert "[必须告诉员工]" in out["message"] and "没有执行" in out["message"]
    assert "[必须告诉员工]" not in TIMEOUT["message"]               # 不改原 dict


def test_other_results_untouched():
    for r in ({"approved": True, "message": None},
              {"approved": False, "message": "BLOCKED: User denied this command."},
              None, "x"):
        assert ux.add_timeout_note(r) == r


def test_guard_wrapper_keeps_inner_markers_for_idempotent_reload():
    def inner(*a, **k):
        return TIMEOUT

    inner._catfish_p54 = True
    w = ux.wrap_guard_with_timeout_note(inner)
    assert w._catfish_p54 is True and w._catfish_p55 is True
    assert "[必须告诉员工]" in w("code", "local")["message"]


# ── P28 外发全中文 ────────────────────────────────────────────────────

SAMPLES = {
    "⏳ Subagent working — your message is queued for when it finishes (use /stop to cancel everything).":
        "⏳ 子任务还在跑 — 你的消息排上了, 这步做完就处理 (发 /stop 可以全部取消)。",
    "⚡ Stopped. You can continue this session.": "⚡ 停了, 可以接着聊。",
    "Queued for the next turn. (3 queued)": "排上了, 下一轮处理。 (排了 3 条)",
    "⏳ Draining 2 active agent(s) before restart...": "⏳ 先等 2 个进行中的任务做完再重启…",
    "Unknown command `/foo`. Type /commands to see what's available, or resend without the leading "
    "slash to send as a regular message.": "没有 /foo 这个命令。发 /commands 看看有哪些, 或者去掉开头的 / 当普通消息发。",
    "⚠️ No activity for 15 min. If the agent does not respond soon, it will be timed out in 15 min. "
    "You can continue waiting or use /reset.":
        "⚠️ 已经 15 分钟没动静了, 再过 15 分钟还没反应就会停下。可以继续等, 或发 /reset 重来。",
    "⏱️ Model provider is rate-limiting": None,   # 占位: 下面单独测
    "✨ Session reset! Starting fresh.\n✦ Tip: use /compact to shrink context": "✨ 已开新对话。",
    "⚠️ Cron job 'daily' was interrupted — the gateway is restarting and killed the run before it "
    "finished. No result was produced for this run.": "⚠️ 定时任务「daily」被中断了 (小鲶在重启), 这次没有结果。",
}


@pytest.mark.parametrize("en", [k for k, v in SAMPLES.items() if v])
def test_outbound_samples_are_chinese(en):
    assert p28._translate_hermes_zh(en) == SAMPLES[en]


def test_new_confirm_block_is_fully_chinese():
    en = ("⚠️ **Confirm /new**\n\nThis starts a fresh session and discards the current conversation "
          "history.\n\nChoose:\n• **Approve Once** — proceed this time only\n• **Always Approve** — "
          "proceed and silence this prompt permanently\n• **Cancel** — keep current conversation\n\n"
          "_Text fallback: reply `/approve`, `/always`, or `/cancel`._")
    zh = p28._translate_hermes_zh(en)
    assert "确认开新对话" in zh and "取消" in zh
    for w in ("Confirm", "Choose", "Approve Once", "Always Approve", "Text fallback", "discards"):
        assert w not in zh, w


def test_inactivity_timeout_multiline_is_collapsed():
    en = ("⏱️ Agent inactive for 30 min — no tool calls or API responses.\n"
          "The agent appears stuck on tool `execute_code` (900s since last activity, iteration 3/90).\n"
          "To increase the limit, set agent.gateway_timeout in config.yaml (value in seconds, 0 = no limit) "
          "and restart the gateway.\nTry again, or use /reset to start fresh.")
    assert p28._translate_hermes_zh(en) == "⏱️ 小鲶 30 分钟没有任何进展, 这次先停下了。再试一次, 或发 /reset 开新对话。"


def test_safety_net_logs_untranslated_english_but_not_chinese(caplog):
    with caplog.at_level(logging.WARNING):
        p28._translate_hermes_zh("Something brand new that hermes added in a later release today.")
    assert "P28 miss" in caplog.text
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        p28._translate_hermes_zh("福富的资质列表已经出好了, 文件在 Downloads 里, 一共 79 条 entries listed here ok.")
    assert "P28 miss" not in caplog.text


# ── hermes 切中文 (display.language: zh) 后它自带的说法 → 我们的说法 ─────────────

ZH_SAMPLES = {
    "✅ 命令已批准。代理正在恢复…": "✅ 好的, 已同意, 小鲶继续干活…",
    "✅ 命令已批准（2 条命令）。代理正在恢复…": "✅ 好的, 已同意(2 条), 小鲶继续干活…",
    "✅ 命令已批准（本次会话内允许该模式）。代理正在恢复…": "✅ 好的, 已同意 (同类的这次对话里都允许了), 小鲶继续干活…",
    "❌ 命令已拒绝。": "❌ 好的, 不执行了。",
    "❌ 命令已拒绝（批准已过期）。": "❌ 这条审批已经过期了。",
    "❌ 命令已拒绝。 已将原因转达给代理: \"太危险\"": "❌ 好的, 不执行了, 已把原因转告小鲶: \"太危险\"",
    "没有待批准的命令。": "现在没有在等你同意的操作。",
    "⚡ 已停止。你可以继续此会话。": "⚡ 停了, 可以接着聊。",
    "✨ 会话已重置！重新开始。\n✦ 提示：Use /compact to shrink context": "✨ 已开新对话。",
    "⏳ 正在等待 3 个活跃代理结束后重启...": "⏳ 先等 3 个进行中的任务做完再重启…",
}


@pytest.mark.parametrize("zh", list(ZH_SAMPLES))
def test_hermes_zh_catalog_is_rewritten_to_our_wording(zh):
    assert p28._translate_hermes_zh(zh) == ZH_SAMPLES[zh]


def test_every_zh_receipt_from_hermes_catalog_loses_the_word_daili():
    # 读本机 hermes 的 zh.yaml, 审批 / 拒绝 / 停止 / 重置这几段里员工能看到的都不再出现「代理」
    import yaml
    src = HERMES / "locales" / "zh.yaml"
    if not src.exists():
        pytest.skip("本机没装 hermes")
    gw = yaml.safe_load(src.read_text(encoding="utf-8"))["gateway"]
    texts = [v for sec in ("approve", "deny", "stop", "reset") for v in gw[sec].values()]
    texts += [gw["approval_expired"], gw["draining"]]
    for t in texts:
        out = p28._translate_hermes_zh(t.replace("{count}", "2").replace("{reason}", "x")
                                        .replace("{title}", "t").replace("{tip}", "tip")
                                        .replace("{error}", "e"))
        assert "代理" not in out, (t, out)


def test_untranslated_status_and_context_lines():
    t = p28._translate_hermes_zh
    assert t("**Model:** `catfish-public-qwen-flash`") == "**模型:** `catfish-public-qwen-flash`"
    assert t("**Context:** 87,000 / 1,000,000 (8%)") == "**对话长度:** 87,000 / 1,000,000 (8%)"
    assert t("🧠 **Context Window**\nModel: `x`\nIn use: 1 / 2 (50%)") == "🧠 **对话长度**\n模型: `x`\n已用: 1 / 2 (50%)"
