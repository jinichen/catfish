"""自带 dispatch(name, args) 的子模块: 工具名前缀 → 模块名 (catfish_tools.dispatch_native 最后按这张表路由)。

10/17 从 catfish_tools.py 挪出来: 每加一个子模块那边就多一行, 它早就贴着 800 行红线。新子模块只改这里。
"""
from __future__ import annotations

PREFIX_MODULES: tuple[tuple[str, str], ...] = (
    ("catfish_wechat_", "wechat_archive"),
    ("catfish_customer", "customers"),
    ("catfish_form", "forms_tool"),
    ("catfish_workflow", "workflow_tool"),
    ("catfish_browser_download", "catfish_tools_browser_download"),
)
