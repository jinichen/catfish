"""P62 (10/22): 重试用尽的聊天提示中文化。

钉三件事:
  1. 配置类原因 (网关说了「管理员 / 供应商」) 不叫员工「过一会儿再试」
  2. 额度类给恢复时间
  3. 开头那句跟 Companion upstreamErrorGuard.ts / 网关 app.py 的识别词对得上 ——
     对不上的话拟稿 / 早安卡片会把这段失败提示当成模型写的正文
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

_DIR = Path(__file__).resolve().parent.parent
_REPO = _DIR.parents[2]


def _mod():
    spec = importlib.util.spec_from_file_location("plugin_failure_copy_zh", _DIR / "plugin_failure_copy_zh.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


GATEWAY_REASON = ("模型服务拒绝了中央配置的 API key (模型 catfish-private-vision) —— 不是员工这边的问题。"
                  "管理员到门户「控制台 → 接入 → 供应商」, 核对这个模型所用供应商的 key")


def test_config_problem_says_admin_not_retry():
    text = _mod().failure_copy_zh(3, GATEWAY_REASON, None)
    assert "需要管理员处理" in text and "过一会儿" not in text
    assert text.endswith(GATEWAY_REASON)


def test_quota_gives_reset_time():
    text = _mod().failure_copy_zh(3, "调用频率超限", "~3h")
    assert "3h 后恢复" in text


def test_transient_suggests_retry():
    assert "过一会儿再发一次" in _mod().failure_copy_zh(3, "上游过载 (503)", None)


def test_guards_recognize_the_lead():
    text = _mod().failure_copy_zh(3, "上游过载 (503)", None)
    guard_ts = (_REPO / "edge/companion-app/src/lib/upstreamErrorGuard.ts").read_text(encoding="utf-8")
    m = re.search(r"const UPSTREAM_ERROR_PATTERN =\s*/(.+)/i;", guard_ts)
    assert m and re.search(m.group(1), text, re.I), "Companion upstreamErrorGuard 认不出 P62 的中文提示"
    app_py = (_REPO / "central/llm-gateway/src/catfish_gateway/app.py").read_text(encoding="utf-8")
    block = app_py[app_py.index("_UPSTREAM_ERROR_AS_CONTENT_PATTERNS = ("):]
    block = block[: block.index(")\n")]
    pats = re.findall(r'"([^"]+)"', block)
    assert any(p in text for p in pats), "网关 _UPSTREAM_ERROR_AS_CONTENT_PATTERNS 认不出 P62 的中文提示"
