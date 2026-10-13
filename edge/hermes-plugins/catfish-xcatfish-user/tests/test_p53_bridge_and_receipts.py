"""P53 tool_call 转发已提升的 catfish 工具 + P28 审批回执中文。不 import hermes。"""
from __future__ import annotations

import plugin_bridge_promoted as p53
import plugin_weixin_zh as p28

REG = {"mcp__catfish_tools__catfish_wiki_search", "mcp__catfish_tools__catfish_wiki_read"}
registered = REG.__contains__
NOT_DEFERRABLE = ("'{n}' is not a deferrable tool. If it appears in the model-facing tools "
                  "list already, call it directly instead of via tool_call.")


def _orig_resolve(args):
    """上游行为: 提升过的 catfish 工具 / 核心工具都不 deferrable → 报错。"""
    name = args.get("name")
    if name == "mcp__catfish_tools__deferred_one":
        return name, args.get("arguments") or {}, None
    return None, {}, NOT_DEFERRABLE.format(n=name)


resolve = p53.wrap_resolve(_orig_resolve, registered)


def test_the_1013_call_now_dispatches():
    # 10/13 13:52 那一次: 模型包了 tool_call 去调 catfish_wiki_search
    name, args, err = resolve({"name": "mcp__catfish_tools__catfish_wiki_search",
                               "arguments": '{"query": "福富 资质"}'})
    assert (name, args, err) == ("mcp__catfish_tools__catfish_wiki_search", {"query": "福富 资质"}, None)


def test_missing_prefix_is_repaired():
    assert resolve({"name": "catfish_wiki_read", "arguments": {"path": "x"}})[0] == \
        "mcp__catfish_tools__catfish_wiki_read"


def test_non_catfish_or_unregistered_names_keep_upstream_error():
    for n in ("terminal", "execute_code", "mcp__other__thing", "mcp__catfish_tools__not_registered",
              "catfish_unknown"):
        name, _, err = resolve({"name": n})
        assert name is None and "is not a deferrable tool" in err, n


def test_deferred_tools_untouched_and_bad_arguments_still_rejected():
    assert resolve({"name": "mcp__catfish_tools__deferred_one", "arguments": {"a": 1}}) == \
        ("mcp__catfish_tools__deferred_one", {"a": 1}, None)
    _, _, err = resolve({"name": "mcp__catfish_tools__catfish_wiki_search", "arguments": "{bad"})
    assert "not valid JSON" in err
    _, _, err = resolve({"name": "mcp__catfish_tools__catfish_wiki_search", "arguments": [1]})
    assert "must be an object" in err


def test_scope_only_adds_catfish_tools_already_in_this_session():
    defs = [{"function": {"name": "mcp__catfish_tools__catfish_wiki_search"}},
            {"function": {"name": "terminal"}},
            {"function": {"name": "mcp__catfish_tools__x_deferred"}}]
    scoped = p53.wrap_scoped(lambda d: frozenset({"mcp__catfish_tools__x_deferred"}))(defs)
    assert scoped == {"mcp__catfish_tools__x_deferred", "mcp__catfish_tools__catfish_wiki_search"}
    assert "terminal" not in scoped
    # 本会话没给的 catfish 工具不会凭空出现
    assert "mcp__catfish_tools__catfish_wiki_read" not in scoped


# ── P28 审批回执 ───────────────────────────────────────────────────────

def test_approval_receipts_are_chinese():
    t = p28._translate_hermes_zh
    assert t("✅ Command approved. The agent is resuming...") == "✅ 好的, 已同意, 小鲶继续干活…"
    assert t("✅ Command approved (pattern approved for this session). The agent is resuming...") == \
        "✅ 好的, 已同意 (同类的这次对话里都允许了), 小鲶继续干活…"
    assert t("✅ Commands approved (3 commands). The agent is resuming...") == \
        "✅ 好的, 已同意 (3 条), 小鲶继续干活…"
    assert t("❌ Command denied.") == "❌ 好的, 不执行了."
    assert t("No pending command to approve.") == "现在没有在等你同意的操作。"
    for s in ("✅ Command approved. The agent is resuming...", "❌ Commands denied (2 commands)."):
        out = t(s)
        assert "Command" not in out and "agent" not in out, out
