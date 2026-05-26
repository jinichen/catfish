"""STUB — DEPRECATED 5/26 (死代码 + 中央扫错 path, 不是隐私违规).

# 砍的真原因 (5/26 audit)

老 skills_loader (782 行) 在 gateway chat 处理时调 `discover_skills()` 扫
SKILL.md 列表, 注入 system prompt 让 LLM 知道有哪些 skill 可调.

设计意图 OK (LLM 必须看到 skill catalog 才能调 skill, 这是产品功能, 不是 leak).
但**实现错位**:
- gateway 跑在中央服务器进程, 扫"home / .hermes / skills" = 中央自己 home,  # noqa: BOUNDARY
  **永远空** (中央服务器没人 git clone catfish skills)
- 真有效的 skill catalog inject 是 catfish-memory hermes plugin
  `_render_skills_catalog` 在做 (plugin 跑员工 mac, 读员工 catfish skills dir, 真扫到)
- gateway 这套从一开始就 inject 空块, 无实际效果 → 死代码

**这不是隐私违规** — SKILL.md 内容进 system prompt 上 LLM 本身是产品本质 (LLM
必须看到 skill 描述才能选择调). 5/26 第一版 stub 注释写"P0 隐私违规" 是错的,
鸿波纠正后修订. 真正的判定: **gateway 中央代码扫员工本机数据然后送 LLM**, 在
当前部署 (gateway 寄生员工 mac) 看起来 OK 但 SaaS 化即破; 而且 gateway 实际
扫的不是员工 home 而是中央自己 home (永远空), 所以是双重错位.

# 砍范围 (同批)

- skills_loader.py / skills_inject.py / skills_vector.py → fail-loud stub
- skill_guard.py → has_skill_intent + inject_skill_guard 改 no-op
- app.py 删 2 行 import + 1 处 discover_skills 调用
- ALLOWLIST 移除 skills_loader.py

# LLM 砍后怎么知道 skill 可调

plugin `_render_skills_catalog` 仍在跑 (员工 mac), 真正给 LLM 注入 skill catalog.
gateway 这套砍掉无影响 (本来就死代码).
"""
from __future__ import annotations

_DEPRECATED_NOTICE = (
    "skills_loader 5/26 砍 — 死代码 (gateway 中央扫自己 home 永远空), "
    "plugin _render_skills_catalog 在员工 mac 跑真接管 catalog 注入."
)


def __getattr__(name: str):  # noqa: D401
    raise RuntimeError(f"[DEPRECATED 5/26 P0 PRIVACY] {_DEPRECATED_NOTICE} (attr: {name})")
