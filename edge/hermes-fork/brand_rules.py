"""品牌替换的**规则表** —— 改什么、改成什么、怎么验。

8/15 从 apply_brand_patch.py 搬出来 (959 行, 过了 CLAUDE.md §1 的 800 红线)。

# 这个文件里没有逻辑, 只有数据

347 行全是四张表 + 两段模板文本。搬出去不是为了"解耦", 是因为**数据和逻辑
混在一个文件里时, 加一条规则和改一次算法看起来一样重**。

hermes 每次升级都要来这里加规则 (0.13 加了 5-10 条, 0.14 又一批)。加规则的
人不该被迫翻过 600 行 patch 逻辑才找到表在哪。

# 四张表各管什么

    RULES                   单行字符串字面量替换 (路径, 原文, 新文, 说明)
    REGEX_RULES             正则替换 —— 上游文案会小改, 精确匹配会 MISS
    VERIFY_MARKERS          --verify 检查哪些文件里必须还留着鲶鱼字样
    NEW_BUILD_WELCOME_BANNER / CATFISH_TIPS_BLOCK
                            整函数替换用的新函数体 (banner 那种多行的)

# ⚠ 只改用户看得见的字符串

不碰代码语法、不碰注释、不碰测试。这条是 apply 的正确性前提 —— 改到代码
逻辑上, hermes 就不是"换了个名字"而是"被我们改坏了", 而且下次上游升级
merge 冲突会一路炸。

# HERMES_ROOT / BACKUP_SUFFIX / PATCHES_DIR 也在这

它们是这一族脚本的公共坐标。放这儿是为了让 brand_hooks.py 能拿到它们而
**不用反向 import apply_brand_patch** —— 那个文件是当脚本跑的 (git hook 里
`python3 .../apply_brand_patch.py --apply`), 反向 import 会把它按 __main__
之外的名字再执行一遍, 得到第二个 module 对象。
"""
from __future__ import annotations

import os
from pathlib import Path

# HERMES_DIR env 优先 (B.3 测试 + admin 场景), 默认 ~/.hermes/hermes-agent
HERMES_ROOT = Path(os.environ.get("HERMES_DIR") or (Path.home() / ".hermes" / "hermes-agent"))
BACKUP_SUFFIX = ".before-catfish"

# .patch 文件目录 (5/19 BL-HERMES-PATCH-AUTOMATION)
# 跟 RULES 字符串替换并存 — RULES 改单行字符串字面量, PATCHES 改代码块
# (加常量 / 加 helper / 加分支等 git diff 能表达的多行 hunk).
# 命名规范: NNNN-描述.patch, 按字典序逐个 apply.
#
# ⚠ 8/15: 这里的 `Path(__file__)` 指的是**本文件**。本文件跟 apply_brand_patch.py
#   是同目录兄弟, 所以 parent 一样, 搬过来不改变指向。哪天有人把这个文件挪进
#   子目录, patches/ 就找不到了 —— 有守卫钉住 (test_brand_patch_split.py)。
PATCHES_DIR = Path(__file__).resolve().parent / "patches"

# 规则：(相对 HERMES_ROOT 的路径, 原字符串, 新字符串, 说明)
# 只改**用户在 UI 里看得见的字符串字面量**，不碰代码语法、不碰测试
RULES: list[tuple[str, str, str, str]] = [
    # ---------- TS: ui-tui/src/theme.ts ----------
    (
        "ui-tui/src/theme.ts",
        "name: 'Hermes Agent',",
        "name: '鲶鱼',",
        "theme.ts: brand name",
    ),
    (
        "ui-tui/src/theme.ts",
        "icon: '⚕',",
        "icon: '🐟',",
        "theme.ts: brand icon",
    ),
    (
        "ui-tui/src/theme.ts",
        "goodbye: 'Goodbye! ⚕',",
        "goodbye: '再见 🐟',",
        "theme.ts: exit message",
    ),
    # ---------- TS: ui-tui/src/bootBanner.ts (5/5 删除) ----------
    # hermes 0.11+ 起 bootBanner.ts 文件被删, 字符串迁到 branding.tsx 下面.
    # 5/5 阶段 A.3 dry-run 在 0.12 上验证: branding.tsx 规则已覆盖该字符串.
    # 删了原 2 条 (避免 SKIP 警告), 内容靠 branding.tsx 那 2 条规则同步.
    # ---------- TS: ui-tui/src/components/branding.tsx ----------
    (
        "ui-tui/src/components/branding.tsx",
        "Nous Research · Messenger of the Digital Gods",
        "鲶鱼平台 · 员工的数字副手",
        "branding: tagline in main banner",
    ),
    # 5/29 hermes 0.15.2 升级新增真 TAG_MID / TAG_TINY 变量 (中等 + 短 banner 真 fallback).
    # 含上下文真`= '...'` 防误伤别处 cli arg desc 真 Nous Research.
    (
        "ui-tui/src/components/branding.tsx",
        "TAG_MID = 'Messenger of the Digital Gods'",
        "TAG_MID = '员工的数字副手'",
        "branding.tsx: TAG_MID (0.15.2 新加变量)",
    ),
    (
        "ui-tui/src/components/branding.tsx",
        "TAG_TINY = 'Nous Research'",
        "TAG_TINY = '鲶鱼'",
        "branding.tsx: TAG_TINY (0.15.2 新加变量)",
    ),
    (
        "ui-tui/src/components/branding.tsx",
        " · Nous Research",
        " · 鲶鱼平台",
        "branding: model row suffix",
    ),
    # 0.12 真机检查 (5/5 B.2 brand check 抓到漏点): 当终端列数 < LOGO_WIDTH 时
    # branding.tsx 的 Banner 组件不渲染 ASCII 大字, fallback 到一行 Text:
    #   {t.brand.icon} NOUS HERMES
    # 之前 bootBanner.ts 的"NOUS HERMES → CATFISH" 字符串以为已被 branding.tsx
    # 现有 2 条规则覆盖 (tagline + model row), 实际**没**覆盖这条 fallback. 必须独加.
    (
        "ui-tui/src/components/branding.tsx",
        "{t.brand.icon} NOUS HERMES",
        "{t.brand.icon} 鲶鱼",
        "branding: ASCII fallback when terminal too narrow (LOGO_WIDTH 不够)",
    ),
    # ---------- TS: ui-tui/src/components/appLayout.tsx ----------
    # ⚕ 在这里作为状态栏前缀，改成 🐟
    (
        "ui-tui/src/components/appLayout.tsx",
        "⚕ {ui.status}",
        "🐟 {ui.status}",
        "appLayout: status bar icon",
    ),
    # ---------- Python: hermes_cli/banner.py ----------
    (
        "hermes_cli/banner.py",
        "[dim {dim}]Nous Research[/]",
        "[dim {dim}]鲶鱼平台[/]",
        "banner.py: model row suffix",
    ),
    # ---------- Python: cli.py ----------
    (
        "cli.py",
        "- Nous Research",
        "- 鲶鱼平台",
        "cli.py: title row suffix",
    ),
    # ---------- Python: hermes_cli/skin_engine.py ----------
    # 5 处相同 "Welcome to Hermes Agent!..." 全部替换
    (
        "hermes_cli/skin_engine.py",
        "Welcome to Hermes Agent! Type your message or /help for commands.",
        "欢迎回来。输入消息或 /help 看命令。",
        "skin_engine: welcome message (5 处都会命中)",
    ),
    # ---------- Python: cli.py welcome fallback ----------
    (
        "cli.py",
        '"Welcome to Hermes Agent! Type your message or /help for commands."',
        '"欢迎回来。输入消息或 /help 看命令。"',
        "cli.py: welcome fallback",
    ),
    (
        "cli.py",
        'get_branding("welcome", "Welcome to Hermes Agent! Type your message or /help for commands.")',
        'get_branding("welcome", "欢迎回来。输入消息或 /help 看命令。")',
        "cli.py: welcome in get_branding",
    ),
    # ---------- banner.py 主标题（line 243）----------
    # 这条是启动 banner 顶部的 "Hermes Agent v0.10.0 (2026.4.16) · upstream xxxx"
    # 真源头就在这里，改了整个 banner 标题立即变鲶鱼
    (
        "hermes_cli/banner.py",
        'base = f"Hermes Agent v{VERSION} ({RELEASE_DATE})"',
        'base = f"鲶鱼 v{VERSION} ({RELEASE_DATE})"',
        "banner.py: 启动 banner 标题",
    ),
    # ---------- banner.py agent_name fallback (5/5 删除) ----------
    # hermes 0.11+ 起这条字面量没了 (skin fallback 路径换实现).
    # 5/5 阶段 A.3 dry-run: MISS 不致命 - agent_name 主路径还在 skin_engine.py 4 处全命中.
    # 删了原规则避免 MISS 警告.

    # ---------- 0.12 新增: cli.py default skin banner ----------
    # hermes 0.12 cli.py:1727-1728 把 default skin 的 banner line 写死了
    # ⚠️ 顺序: 长字面量先 replace (line1), 再 replace 短的 (tiny_line).
    # 否则 tiny_line 的 "⚕ NOUS HERMES" 会先把 line1 里的 "⚕ NOUS HERMES" 部分改了.
    (
        "cli.py",
        '"⚕ NOUS HERMES - AI Agent Framework"',
        '"🐟 鲶鱼 - 员工的数字副手"',
        "cli.py: default skin banner line1 (0.12 新加)",
    ),
    (
        "cli.py",
        '"⚕ NOUS HERMES"',
        '"🐟 鲶鱼"',
        "cli.py: default skin banner tiny_line (0.12 新加)",
    ),

    # ---------- 0.12 新增: cli.py goodbye 副本 ----------
    # cli.py:9343-9345 在 hermes_cli/skin_engine.py 之外又写了一份 goodbye fallback.
    # 用精确的赋值/调用左侧绑死, 防跟 skin_engine.py 的 '"goodbye": "Goodbye! ⚕"'
    # 字面量重叠 (那条是 'goodbye": ' 前缀, 这两条是 get_active_goodbye(...) 跟
    # goodbye = ...).
    (
        "cli.py",
        'get_active_goodbye("Goodbye! ⚕")',
        'get_active_goodbye("再见 🐟")',
        "cli.py: get_active_goodbye 的 default 入参 (0.12 新加)",
    ),
    (
        "cli.py",
        'goodbye = "Goodbye! ⚕"',
        'goodbye = "再见 🐟"',
        "cli.py: goodbye fallback 赋值 (0.12 新加)",
    ),

    # ---------- 0.12 新增: hermes_cli/main.py --version 输出 ----------
    # hermes 0.12 加 cmd_version 子命令在 main.py:5099 直接 print 版本字符串.
    # 跟 banner.py:327 那条 'f"Hermes Agent v{VERSION}..."' 用的不是同一个变量
    # (main.py 用 __version__ / __release_date__, banner.py 用 VERSION / RELEASE_DATE),
    # 所以是独立字面量, 必须单独加规则.
    (
        "hermes_cli/main.py",
        'print(f"Hermes Agent v{__version__} ({__release_date__})")',
        'print(f"鲶鱼 v{__version__} ({__release_date__})")',
        "main.py: --version 子命令输出 (0.12 新加)",
    ),

    # ---------- 0.12 新增: rl_cli.py 退出语 ----------
    # rl_cli 是 RL 训练用的入口, 普通员工不直接进, 但脱敏一致性还是要改.
    (
        "rl_cli.py",
        '"\\n👋 Goodbye!"',
        '"\\n👋 再见!"',
        "rl_cli.py: 正常退出语 (0.12 新加)",
    ),
    (
        "rl_cli.py",
        '"\\n\\n👋 Interrupted. Goodbye!"',
        '"\\n\\n👋 中断. 再见!"',
        "rl_cli.py: 中断退出语 (0.12 新加)",
    ),

    # ---------- skin_engine.py skin 品牌字符串（同一字符串多处一次性替换）----------
    # default / mono / slate / light 四个 skin 都有 `"agent_name": "Hermes Agent"`
    # content.replace() 会把所有 occurrences 都改掉
    (
        "hermes_cli/skin_engine.py",
        '"agent_name": "Hermes Agent"',
        '"agent_name": "鲶鱼"',
        "skin_engine: agent_name 全部改成鲶鱼（4~5 处）",
    ),
    # default / mono / slate / light 的 goodbye 都是 "Goodbye! ⚕"
    (
        "hermes_cli/skin_engine.py",
        '"goodbye": "Goodbye! ⚕"',
        '"goodbye": "再见 🐟"',
        "skin_engine: goodbye ⚕ 改鲶鱼（4 处）",
    ),
    # paperwhite skin 的 goodbye 用了 unicode escape
    (
        "hermes_cli/skin_engine.py",
        '"goodbye": "Goodbye! \\u2695"',
        '"goodbye": "再见 🐟"',
        "skin_engine: goodbye \\u2695 改鲶鱼",
    ),
    # (已回滚) 原本想改 default skin 的 prompt / dim 提高对比度，
    # 但 default skin 是为**深色终端**设计的。正确做法是让员工用 /skin daylight
    # 或 /skin warm-lightmode 切换到浅色终端专用 skin。
    # 所以保留 default skin 原色（#FFF8DC 象牙白 / #B8860B 暗金）。
    # docstring 里的示例字符串（影响很小，但保持一致性）
    (
        "hermes_cli/skin_engine.py",
        'print(skin.get_branding("agent_name"))  # "Hermes Agent"',
        'print(skin.get_branding("agent_name"))  # "鲶鱼"',
        "skin_engine: docstring 示例",
    ),
]


# ============================================================
# 整函数替换：极简 build_welcome_banner
# ============================================================
# 原函数 220 行，构造左右双列 banner 含完整 Tools/Skills 列表。
# 员工每次启动都被 40 行信息刷屏，空间浪费 + 颜色对比度差。
# 新函数 4 行 Panel：标题 / 模型+路径 / 总览数字 / session。

NEW_BUILD_WELCOME_BANNER = '''def build_welcome_banner(console, model: str, cwd: str,
                         tools=None,
                         enabled_toolsets=None,
                         session_id=None,
                         get_toolset_for_tool=None,
                         context_length: int = None):
    """鲶鱼极简启动 banner：4 行 Panel + 高对比度颜色。"""
    from pathlib import Path as _Path

    # --- 模型简名 ---
    model_short = model.split("/")[-1] if "/" in model else model
    if model_short.endswith(".gguf"):
        model_short = model_short[:-5]
    if len(model_short) > 40:
        model_short = model_short[:37] + "..."

    # --- 路径简化（把 $HOME 替换成 ~）---
    cwd_short = str(cwd) if cwd else ""
    home = str(_Path.home())
    if cwd_short.startswith(home):
        cwd_short = "~" + cwd_short[len(home):]
    if len(cwd_short) > 60:
        cwd_short = "..." + cwd_short[-57:]

    # --- context 显示 ---
    ctx = f" · {_format_context_length(context_length)} ctx" if context_length else ""

    # --- 总览：工具数 + MCP server 数 ---
    n_tools = len(tools or [])
    n_mcp = 0
    try:
        from tools.mcp_tool import get_registered_mcp_servers
        n_mcp = len(get_registered_mcp_servers() or [])
    except Exception:
        n_mcp = 0

    # --- 构造极简内容（3~4 行）---
    lines = [
        f"[bold cyan]{model_short}[/][dim]{ctx}[/]  [dim cyan]·[/]  [bold]鲶鱼平台[/]",
        f"[dim]{cwd_short}[/]",
        f"[dim]{n_tools} tools · {n_mcp} MCP server{'s' if n_mcp != 1 else ''} · /help 看全部命令[/]",
    ]
    if session_id:
        lines.append(f"[dim]Session: {session_id}[/]")

    content = "\\n".join(lines)
    title = format_banner_version_label()

    outer_panel = Panel(
        content,
        title=f"[bold cyan]{title}[/]",
        border_style="cyan",
        padding=(0, 2),
        expand=True,
    )

    console.print()
    console.print(outer_panel)


'''


# 鲶鱼 tips 替换块（替换 hermes_cli/tips.py 的整个 TIPS 列表）
CATFISH_TIPS_BLOCK = '''TIPS = [
    "鲶鱼是你的副手，不是工具 —— 说\\"帮我做 X\\"，不是\\"怎么做 X\\"",
    "catfish doctor 一键看平台健康：gateway / Chrome / 索引 / 身份",
    "找文件用自然语言：\\"帮我找上周那份合同\\"。catfish-search 比 find 快 100 倍，还能读 PDF/Word。",
    "context 到 70% 自动压缩，不用手动 /compress",
    "长上下文切 catfish-public-gemini-pro（2M tokens）",
    "浏览器任务直接说：\\"帮我去 Jira 看这 sprint 所有 close 的 ticket\\"",
    "飞书消息过滤：catfish-feishu 帮你看哪条领导在问你，生成草稿给你审核",
    "需要周报？鲶鱼代写，你审核后本人从飞书发 —— 领导不知道有 AI 帮忙",
    "你的数据归你：memory / 搜索索引 / 草稿全在 ~/.catfish/，不上传任何地方",
    "skill 可自生成 + 自测 + 自修复 + 自动 cronjob 注册（Self-Evolution）",
]'''


# 多行 ASCII block 的 regex 替换规则（一条规则干掉一整个变量赋值）
# 格式：(文件路径, pattern, replacement, 说明, detect_pattern)
#   detect_pattern 用于"已 patched" 检测：如果这个已经出现，跳过
REGEX_RULES: list[tuple[str, str, str, str, str]] = [
    # 删掉顶部那个 "HERMES-AGENT" 巨字 ASCII（6 行蓝字）
    (
        "hermes_cli/banner.py",
        r'HERMES_AGENT_LOGO = """.*?"""',
        'HERMES_AGENT_LOGO = ""',
        "banner.py: 清空 HERMES-AGENT 大字 LOGO",
        'HERMES_AGENT_LOGO = ""',
    ),
    # 删掉中间那个蛇杖 ASCII（15 行 braille）
    (
        "hermes_cli/banner.py",
        r'HERMES_CADUCEUS = """.*?"""',
        'HERMES_CADUCEUS = ""',
        "banner.py: 清空蛇杖 ASCII",
        'HERMES_CADUCEUS = ""',
    ),
    # tips.py TIPS 列表整块替换成鲶鱼 10 条
    (
        "hermes_cli/tips.py",
        r'TIPS = \[.*?\n\]',
        CATFISH_TIPS_BLOCK,
        "tips.py: 整个 TIPS 列表替换为鲶鱼 10 条",
        '鲶鱼是你的副手',  # detect marker
    ),
]


# ============================================================
# verify: 检查 branding 还在不在 (CI / hook 用)
# ============================================================
#
# 思路: 找几个**显眼到员工一眼能看出问题**的关键字符串. 任意一条还是英文 hermes
#      字面量, 就报错退出 1. hook 据此决定要不要重跑 --apply.
#
# 选关键字标准:
#   - 出现在启动 banner / 退出语 / 状态栏 — 员工每天看
#   - 改了之后字符串完全不一样 — 不会跟其他东西误命中
#   - 跨多个 hermes 版本都稳定存在 — 不会因为 0.13 把它删了报假错

VERIFY_MARKERS: list[tuple[str, list[str], list[str]]] = [
    # (rel_path, must_contain_any_of, must_NOT_contain_any_of)
    (
        "hermes_cli/banner.py",
        ["鲶鱼"],
        [
            'base = f"Hermes Agent v{VERSION}',  # banner 标题没改
            'HERMES_AGENT_LOGO = """',  # 大字 LOGO 没清
        ],
    ),
    (
        "hermes_cli/skin_engine.py",
        ["鲶鱼", "再见 🐟"],
        [
            '"agent_name": "Hermes Agent"',
            '"goodbye": "Goodbye! ⚕"',
        ],
    ),
    (
        "cli.py",
        ["鲶鱼", "再见 🐟"],
        [
            '"⚕ NOUS HERMES"',
            'goodbye = "Goodbye! ⚕"',
        ],
    ),
    (
        "ui-tui/src/components/branding.tsx",
        ["鲶鱼平台"],
        ["Nous Research · Messenger of the Digital Gods"],
    ),
]
