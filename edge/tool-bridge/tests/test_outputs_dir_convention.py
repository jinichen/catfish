"""产出目录只有一个约定: ~/.catfish/outputs/<YYYY-MM-DD>/ (8/14)。

# 病历

鸿波: 「生成文件的目录不要跳来跳去」。查下来同时活着**六种**写法:

    output/  平铺                       创意 skill (guizang / huashu)
    output/<YYYY-MM-DD>/<HHMMSS>_slug/  weekly-report / leadership-briefing
    output/<YYYYMMDD>/                  LLM 自己发挥的 (见下)
    output/<ts>-立项-x.docx             catfish_tool_schemas.py 的例子
    outputs/<YYYY-MM-DD>/               advisor_io.py
    outputs/  平铺                      drafts.rs / briefing_context.rs

**真正的源头不在代码里, 在提示词里**: catfish_memory.py 每轮注入
「文件输出到 ~/.catfish/output/<日期>/」—— `<日期>` 没规定格式, LLM 每次自己发挥,
于是 output/20260813/ 和 output/2026-08-07/ 同时长在员工机器上。

后果不只是乱: `catfish_list_my_outputs` 只读 output/, 而 8 月产出 65 个在
outputs/、13 个在 output/ —— 那个工具漏掉近期 83% 的东西。而且它还是**非递归**的,
连 output/<日期>/<时间>/ 里那两个 skill 的产出也一个列不出来。

# 这个文件钉什么

统一之后最容易发生的事是**再漂回去** —— 下一个人加个新 skill, 顺手写
`~/.catfish/output/`, 没人拦。所以钉的是"仓里不许再出现单数 output 的产出路径",
而不只是"这几处改对了"。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from catfish_tool_bridge import recent_outputs as ro

_REPO = Path(__file__).resolve().parents[3]


# ── 约定本身 ────────────────────────────────────────────────


def test_仓里没有新的单数_output_产出路径():
    """★★★ 防再漂。

    扫真代码 (跳过测试 / 注释 / 归档), 找 `.catfish/output` 后面**不是** s 的写法。

    允许的例外只有两处, 都是"读历史数据"而不是"往那儿写":
      · recent_outputs.py 的 _OUTPUT_DIRS —— 老目录只读兼容
      · local-search config.py 的 catfish-output-2026-07 迁移条目 —— 按仓里的
        规矩"永远不改已有迁移 id", 老员工 yaml 里那条得留着
    """
    ALLOW = {
        "edge/tool-bridge/src/catfish_tool_bridge/recent_outputs.py",
        "edge/local-search/src/catfish_search/config.py",
        # 5/26 砍掉的 stub —— 整个文件就是一句"gateway 不读员工 .catfish/output/"
        # 的 fail-loud 说明, 它提到这个路径是在讲**为什么不能读**。
        "central/llm-gateway/src/catfish_gateway/recent_outputs.py",
    }
    SKIP_DIRS = {"node_modules", "venv", ".venv", "target", "dist", "build",
                 ".git", "__pycache__", "archive", "tests", "test"}
    pat = re.compile(r"\.catfish[/\"'\s]*[/,]?\s*[\"']?output(?!s)[/\"']")

    hits: list[str] = []
    for root, dirs, files in os.walk(_REPO):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in files:
            if not fn.endswith((".py", ".rs", ".ts", ".tsx")):
                continue
            if fn.startswith("test_") or fn.endswith((".test.ts", ".test.tsx")):
                continue
            p = Path(root) / fn
            rel = str(p.relative_to(_REPO))
            if rel in ALLOW:
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for i, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith(("#", "//", "*", "/*")):
                    continue          # 注释里提历史写法是正当的
                if pat.search(line):
                    hits.append(f"{rel}:{i}  {stripped[:90]}")
    assert not hits, (
        "又出现单数 `~/.catfish/output/` 的产出路径 —— 约定是 outputs (复数):\n  "
        + "\n  ".join(hits)
    )


def test_提示词里给了明确的日期格式():
    """★★ LLM 目录乱飞的真正来源是提示词没规定格式。

    `<日期>` 这种占位符等于让模型自己发挥。钉住必须写成 YYYY-MM-DD。
    """
    f = _REPO / "edge/hermes-plugins/catfish-memory/catfish_memory.py"
    if not f.exists():
        pytest.skip(f"没有 catfish-memory ({f})")
    text = f.read_text(encoding="utf-8")
    line = next((l for l in text.splitlines()
                 if "文件输出到" in l and not l.strip().startswith("#")), None)
    assert line, "提示词里那句「文件输出到 …」不见了 —— 契约变了, 这条要更新"
    assert "outputs/" in line, f"提示词还在教 LLM 写单数 output/: {line.strip()}"
    assert "YYYY-MM-DD" in line, (
        f"提示词没给日期格式, LLM 会自己发挥 (20260813 就是这么来的): {line.strip()}"
    )


# ── 读取方 ──────────────────────────────────────────────────


@pytest.fixture()
def two_dirs(tmp_path, monkeypatch):
    """新旧两个产出目录, 各放一个**嵌在子目录里**的文件。"""
    new = tmp_path / "outputs" / "2026-08-14"
    old = tmp_path / "output" / "2026-05-01" / "120000_周报"
    for d in (new, old):
        d.mkdir(parents=True)
    (new / "新的.md").write_text("x", encoding="utf-8")
    (old / "老的.xlsx").write_text("x", encoding="utf-8")
    (tmp_path / "outputs" / ".DS_Store").write_text("junk", encoding="utf-8")
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    return tmp_path


def test_递归扫子目录(two_dirs):
    """★★★ 原来是 iterdir() 非递归, 子目录直接跳过。

    而 weekly-report / leadership-briefing 写的**就是** <日期>/<时间>_slug/xxx。
    也就是说这个工具从来列不出那两个 skill 的产出 —— 员工问"我那份汇报呢",
    LLM 拿到一份看着完整、实际漏掉整类文件的清单。比报错更难发现。
    """
    names = {x["name"] for x in ro.list_recent(hours_back=24 * 365, limit=99)}
    assert "新的.md" in names, f"outputs/<日期>/ 下的文件没列出来: {names}"
    assert "老的.xlsx" in names, f"嵌两层的历史文件没列出来 (非递归?): {names}"


def test_老目录仍然读得到(two_dirs):
    """★★ 改名不能让员工 309 个历史产出在 LLM 眼里凭空消失。"""
    paths = [x["path"] for x in ro.list_recent(hours_back=24 * 365, limit=99)]
    assert any("/output/" in p for p in paths), f"老目录 output/ 没在扫: {paths}"
    assert any("/outputs/" in p for p in paths), f"新目录 outputs/ 没在扫: {paths}"


def test_跳过隐藏文件(two_dirs):
    names = {x["name"] for x in ro.list_recent(hours_back=24 * 365, limit=99)}
    assert ".DS_Store" not in names, "把 .DS_Store 当产出列给员工了"


def test_两个目录的结果都进同一个列表且按时间排(two_dirs):
    r = ro.list_recent(hours_back=24 * 365, limit=99)
    assert len(r) == 2, r
    assert r[0]["mtime"] >= r[1]["mtime"], "没按 mtime 倒序"
