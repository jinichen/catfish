"""迁移插入必须适配员工 yaml 的**实际缩进风格** (8/14)。

# 病历 —— 这是一次真实的生产事故

8/14 加 `catfish-outputs-2026-08` 迁移之后, 鸿波机器上索引直接跑不起来:

    yaml.parser.ParserError: while parsing a block mapping
      expected <block end>, but found '-'   (line 5)

写坏的文件长这样:

    include:

      # 鲶鱼替你生成的文档（统一产出目录）
      - ~/.catfish/outputs      ← 迁移插的, 2 空格缩进
    - ~/.catfish/uploads        ← 原有的, 顶格
    - ~/.catfish/output
    - ~/.catfish

两种缩进混在同一层, YAML 直接废掉。

# 两个 bug 叠在一起, 都在 7/27 就埋下了

1. `_ensure_in_include` 判断 include 段结束的条件是「顶格 + 非注释 + 非空」。
   而 YAML 允许 block sequence 跟 key 同列 (`include:` 下面顶格写 `- x`),
   **这正是 yaml.safe_dump 的默认输出**。于是第一个 `- ~/...` 被当成"下一个
   顶层 key" → 认为 include 段里一条目都没有。

2. 插入时硬编码 `f"  - {path}"` 两格缩进。

单独看每个都像小事, 叠在一起就是"配置文件被写成非法 YAML"。

# 为什么 7/27 那次没炸

那时文件还是 DEFAULT_CONFIG 的缩进风格。员工在 Companion 面板上加/删过目录
之后, Tauri 那边是 load→改→dump 重写整个文件, 文件就变成顶格风格了 ——
**下一次迁移才会踩到**。也就是说这个雷是"面板操作 + 新迁移"两件事凑齐才响。

# 这个文件钉什么

不是"这次改对了", 是"以后往 MIGRATIONS 里加条目不会再炸"。所以按**风格**参数化:
顶格 / 两格 / 四格 / 空 include 段, 每种都必须插完仍是合法 YAML 且条目齐全。
"""
from __future__ import annotations

import importlib
import tempfile
from pathlib import Path

import pytest
import yaml

from catfish_search import config as cfgmod


STYLES = {
    # yaml.safe_dump 的默认输出 —— 面板改过目录之后就是这个样子。
    # 这一种就是 8/14 炸掉的那个。
    "顶格": "include:\n- ~/.catfish/uploads\n- ~/.catfish/output\nexclude:\n- ~/Library\n",
    # DEFAULT_CONFIG 的风格
    "两格": "include:\n  - ~/.catfish/uploads\n  - ~/.catfish/output\nexclude:\n  - ~/Library\n",
    # 员工手写也可能是四格
    "四格": "include:\n    - ~/.catfish/uploads\nexclude:\n    - ~/Library\n",
    # include 段是空的 (员工把目录全删了)
    "空段": "include:\nexclude:\n- ~/Library\n",
}


@pytest.fixture()
def home(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setenv("HOME", d)
        importlib.reload(cfgmod)
        (Path(d) / ".catfish").mkdir(parents=True, exist_ok=True)
        for sub in ("uploads", "output", "outputs"):
            (Path(d) / ".catfish" / sub).mkdir(parents=True, exist_ok=True)
        yield Path(d)
    importlib.reload(cfgmod)


@pytest.mark.parametrize("style", list(STYLES), ids=list(STYLES))
def test_插完仍是合法_yaml(home, style):
    """★★★ 事故本体: 混缩进会让整个配置文件解析失败。"""
    cfgmod.CONFIG_FILE.write_text(STYLES[style], encoding="utf-8")
    cfgmod.load_config()
    txt = cfgmod.CONFIG_FILE.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(txt)
    except yaml.YAMLError as e:
        pytest.fail(f"[{style}] 迁移把 yaml 写坏了:\n{txt}\n\n{e}")
    assert isinstance(data, dict), f"[{style}] 顶层不是 mapping:\n{txt}"


@pytest.mark.parametrize("style", list(STYLES), ids=list(STYLES))
def test_插完条目都在_原有的没丢(home, style):
    """★★ 光"合法"不够 —— 原有目录不能被挤掉, 新目录得真加上。"""
    before = yaml.safe_load(STYLES[style]) or {}
    old = set(before.get("include") or [])

    cfgmod.CONFIG_FILE.write_text(STYLES[style], encoding="utf-8")
    cfgmod.load_config()
    after = set(yaml.safe_load(cfgmod.CONFIG_FILE.read_text(encoding="utf-8"))["include"] or [])

    assert old <= after, f"[{style}] 原有条目丢了: 少了 {old - after}"
    for _, path, _ in cfgmod.MIGRATIONS:
        assert path in after, f"[{style}] 迁移条目 {path} 没加进去:\n{after}"


@pytest.mark.parametrize("style", list(STYLES), ids=list(STYLES))
def test_新条目跟现有条目同列(home, style):
    """★★ 直接钉缩进 —— 混列就是事故本身, 别等 yaml 解析器来告诉我们。"""
    cfgmod.CONFIG_FILE.write_text(STYLES[style], encoding="utf-8")
    cfgmod.load_config()
    lines = cfgmod.CONFIG_FILE.read_text(encoding="utf-8").splitlines()

    start = next(i for i, ln in enumerate(lines) if ln.rstrip() == "include:")
    indents = set()
    for ln in lines[start + 1:]:
        if not ln.strip():
            continue
        if ln.strip().startswith("- "):
            indents.add(len(ln) - len(ln.lstrip()))
            continue
        if not ln.startswith((" ", "\t", "#")):
            break        # 下一个顶层 key
    assert len(indents) <= 1, (
        f"[{style}] include 段里出现了 {len(indents)} 种缩进 {sorted(indents)} —— "
        "混列 = yaml 解析失败, 就是 8/14 那次事故"
    )


def test_跑两次不重复插(home):
    """幂等 —— marker 记住了就不该再插一遍。"""
    cfgmod.CONFIG_FILE.write_text(STYLES["顶格"], encoding="utf-8")
    cfgmod.load_config()
    once = cfgmod.CONFIG_FILE.read_text(encoding="utf-8")
    cfgmod.load_config()
    assert cfgmod.CONFIG_FILE.read_text(encoding="utf-8") == once
