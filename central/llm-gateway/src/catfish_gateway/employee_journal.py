"""STUB — DEPRECATED 5/26 (同 A2A 整批砍).

唯一 caller 是 `a2a_journal_hook` (写 federation 来的请求到 bob 本机 journal).
A2A 整套 5/26 砍后此模块成死代码. 同批 stub.

employee_journal 本身的 read / append 功能由 catfish-memory hermes plugin 接管
(hermes 0.14 release notes #20805 sessions, hermes 自己有 journal 概念).
"""
from __future__ import annotations

_DEPRECATED_NOTICE = (
    "employee_journal 5/26 砍 — A2A 整批同砍 (唯一 caller 是 a2a_journal_hook). "
    "catfish-memory hermes plugin 接管 journal 读写."
)


def __getattr__(name: str):  # noqa: D401
    raise RuntimeError(f"[DEPRECATED 5/26] {_DEPRECATED_NOTICE} (attr: {name})")
