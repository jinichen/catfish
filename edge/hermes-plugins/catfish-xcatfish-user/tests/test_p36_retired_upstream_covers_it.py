"""P36 退役之后, 我们依赖的那个上游行为得钉住。

# 背景

P36 干的事: launchd 起 hermes 时 process cwd = "/", 于是 setenv
`TERMINAL_CWD=$HOME` 兜底 —— execute_code / terminal / file_tools 三条路径都读
这个 env, 一次覆盖。8/19 量化时发现**上游 0.20 已经做了同一件事**, 于是删掉。

# 为什么删掉之后还要写测试

删一个 patch 的风险不是"删错了", 是"**上游哪天把那个行为拿掉, 而我们不知道**"。
故障形状跟当年一模一样: execute_code 里 `open(".catfish/uploads/x.csv")` 从 `/`
找, FileNotFoundError —— 而且没有任何东西会报错说"P36 当年就是治这个的"。

所以这条测试读**真的 hermes 树**, 验两件事:

  1. `gateway/run.py` 仍然在 `TERMINAL_CWD` 空/占位时调
     `resolve_placeholder_terminal_cwd(..., home_fallback=...)`
  2. `gateway/cwd_placeholder.py` 在 **local backend** 下必然返值 (不会是 None)

第 2 条是关键: 如果它可能返 None, 上游那段会 `os.environ.pop("TERMINAL_CWD")`,
consumer 退回 `os.getcwd()` = "/", P36 的病就回来了。

# 判据故意宽一格

只验"这个行为还在", 不验实现细节 (参数顺序 / 变量名)。上游重构是常态, 这条
测试不该因为改了个局部变量名就红 —— 它只在**行为消失**时红。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

HERMES_ROOT = Path(
    os.environ.get("HERMES_ROOT", Path.home() / ".hermes" / "hermes-agent")
)

pytestmark = pytest.mark.skipif(
    not (HERMES_ROOT / "gateway" / "run.py").exists(),
    reason=f"没有 hermes 树可对照 ({HERMES_ROOT}) —— CI/沙箱里跳过",
)


def _read(rel: str) -> str:
    return (HERMES_ROOT / rel).read_text(encoding="utf-8", errors="replace")


def test_gateway_仍然在空值时兜底到_home():
    """上游 gateway/run.py 仍然把 TERMINAL_CWD 兜底到 home。"""
    src = _read("gateway/run.py")

    assert "resolve_placeholder_terminal_cwd" in src, (
        "上游不再调 resolve_placeholder_terminal_cwd —— TERMINAL_CWD 的兜底链可能没了。\n"
        "P36 (setenv TERMINAL_CWD=$HOME) 8/19 因为它才退役的, 现在要重新评估:\n"
        "  execute_code 相对路径会从 launchd 的 cwd='/' 起, FileNotFoundError。\n"
        "见 plugin_wechat_qr.py 尾部的 P36 墓碑。"
    )
    assert re.search(r"home_fallback\s*=", src), (
        "上游还调 resolve_placeholder_terminal_cwd, 但不再传 home_fallback —— "
        "那 $HOME 兜底就没了, P36 的病会回来。"
    )


def test_local_backend_下必然返值_不会是_None():
    """cwd_placeholder 在 local backend 下不能返 None。

    返 None → 上游 `os.environ.pop("TERMINAL_CWD")` → consumer 退回 os.getcwd()
    → launchd 下就是 "/" → 正是 P36 当年治的那个病。
    """
    src = _read("gateway/cwd_placeholder.py")

    # 行为判据: local 分支里出现 home_fallback 且是 return
    m = re.search(
        r'if\s+backend\s*==\s*[\'"]local[\'"]\s*:(.{0,400}?)(?=\n\s{0,4}(?:if|return|def)\s)',
        src, re.S,
    )
    assert m, "cwd_placeholder.py 里找不到 local backend 分支 —— 结构变了, 重新评估 P36"
    local_branch = m.group(1)
    assert "home_fallback" in local_branch, (
        f"local 分支不再用 home_fallback:\n{local_branch}\n"
        "→ TERMINAL_CWD 可能被 pop 掉, P36 的病会回来。"
    )
    assert "return" in local_branch, "local 分支不 return —— 结构变了, 重新评估"


def test_文档字符串仍然承诺_local_不返_None():
    """上游自己的 docstring 是这条依赖的第二个来源, 也钉一下。"""
    src = _read("gateway/cwd_placeholder.py")
    head = src[: src.index("if configured_cwd")] if "if configured_cwd" in src else src[:2000]
    assert re.search(r"local.*placeholder.*home_fallback", head, re.S | re.I), (
        "上游 docstring 不再写「local + placeholder → MESSAGING_CWD 或 home_fallback」——\n"
        "契约变了, 去读实现确认 P36 要不要复活。"
    )


def test_p36_确实已经从插件里删干净了():
    """墓碑留着, 代码不留 —— 免得下次有人以为它还在跑。"""
    plugin_dir = Path(__file__).resolve().parent.parent
    live = []
    for py in plugin_dir.glob("*.py"):
        txt = py.read_text(encoding="utf-8", errors="replace")
        # 只找**可执行的**引用, 注释里的墓碑不算
        for i, line in enumerate(txt.splitlines(), 1):
            st = line.strip()
            if st.startswith("#") or not st:
                continue
            if "_patch_p36" in st:
                live.append(f"{py.name}:{i}: {st[:70]}")
    assert not live, "P36 还有活引用:\n" + "\n".join(live)
