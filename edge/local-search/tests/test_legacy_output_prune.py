"""单数 ~/.catfish/output 只给有历史文件的老员工留着 (10/21)。

鸿波 10/21 看着搜索范围面板问: 为什么缺省就有 outputs 和 output 两个几乎同名的目录?

- output/ (单数) 是 8 月前的产出目录, catfish-output-2026-07 迁移给老员工加进 yaml;
  8/14 统一到 outputs/ (复数) 之后, 它只剩"让历史文件能搜到"这一个用处。
- 但新员工生成 yaml 时 _applied_migrations 是空的, 那条迁移会在新员工身上也跑一遍,
  于是新员工面板里也多一个永远空着的 output/。

这里钉两件事:
  1. 新员工的 yaml 里没有单数 output/
  2. 老员工的 output/ 空的 / 不存在 → 摘掉 (连同迁移插的说明注释); 有文件 → 留着
"""
from __future__ import annotations

import importlib
import tempfile
from pathlib import Path

import pytest
import yaml

from catfish_search import config as cfgmod

LEGACY = "~/.catfish/output"


@pytest.fixture()
def home(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setenv("HOME", d)
        importlib.reload(cfgmod)
        (Path(d) / ".catfish").mkdir(parents=True, exist_ok=True)
        yield Path(d)
    importlib.reload(cfgmod)


def _include() -> list[str]:
    return yaml.safe_load(cfgmod.CONFIG_FILE.read_text(encoding="utf-8"))["include"] or []


def _old_employee_yaml() -> str:
    """8 月前的老员工: 默认配置 + 两条迁移都跑过 (迁移插的注释 + 条目都在)。"""
    return (
        "include:\n"
        "  - ~/.catfish/uploads\n"
        "\n"
        "  # 鲶鱼替你生成的文档（周报 / 汇报 / 报告）—— 文书风格就是从这里学的\n"
        "  - ~/.catfish/output\n"
        "\n"
        "  # 鲶鱼替你生成的文档（统一产出目录）—— 文书风格就是从这里学的\n"
        "  - ~/.catfish/outputs\n"
        "exclude:\n"
        "  - ~/Library\n"
        "\n"
        f"{cfgmod._MIGRATION_BANNER}\n"
        f"{cfgmod._MIGRATION_KEY}:\n"
        "  - catfish-output-2026-07\n"
        "  - catfish-outputs-2026-08\n"
    )


def test_新员工没有单数_output(home):
    cfgmod.load_config()
    inc = _include()
    assert LEGACY not in inc, f"新员工面板里又出现了单数 output/: {inc}"
    assert "~/.catfish/outputs" in inc and "~/.catfish/uploads" in inc
    applied = yaml.safe_load(cfgmod.CONFIG_FILE.read_text(encoding="utf-8"))[cfgmod._MIGRATION_KEY]
    assert set(applied) == {m for m, _, _ in cfgmod.MIGRATIONS}, "新建时没把现有迁移标成已执行"


def test_新员工加载两次_配置不变(home):
    cfgmod.load_config()
    once = cfgmod.CONFIG_FILE.read_text(encoding="utf-8")
    cfgmod.load_config()
    assert cfgmod.CONFIG_FILE.read_text(encoding="utf-8") == once


@pytest.mark.parametrize("state", ["不存在", "空目录", "只有空子目录"])
def test_老员工_output_没文件就摘掉(home, state):
    if state != "不存在":
        (home / ".catfish" / "output").mkdir()
    if state == "只有空子目录":
        (home / ".catfish" / "output" / "2026-07-01").mkdir()
    cfgmod.CONFIG_FILE.write_text(_old_employee_yaml(), encoding="utf-8")

    cfgmod.load_config()

    txt = cfgmod.CONFIG_FILE.read_text(encoding="utf-8")
    assert LEGACY not in _include(), f"[{state}] 空的单数 output/ 没摘掉:\n{txt}"
    assert "周报 / 汇报 / 报告" not in txt, f"[{state}] 迁移插的那行说明注释留下了:\n{txt}"
    assert {"~/.catfish/uploads", "~/.catfish/outputs"} <= set(_include()), "别的条目被误删"
    # 迁移 marker 还在 → 不会被下次迁移塞回来
    cfgmod.load_config()
    assert LEGACY not in _include(), f"[{state}] 摘掉后又被迁移加回来了"


def test_老员工_output_有历史文件就留着(home):
    old = home / ".catfish" / "output" / "2026-07-30"
    old.mkdir(parents=True)
    (old / "周报.docx").write_text("x", encoding="utf-8")
    cfgmod.CONFIG_FILE.write_text(_old_employee_yaml(), encoding="utf-8")

    cfgmod.load_config()

    assert LEGACY in _include(), "有历史文件的单数 output/ 被摘了 —— 那些文档就搜不到了"


def test_面板重写过的顶格风格也能摘(home):
    """Companion 面板改过目录后文件被 yaml.safe_dump 重写成顶格、注释全没了。"""
    cfgmod.CONFIG_FILE.write_text(
        "include:\n- ~/.catfish/uploads\n- ~/.catfish/output\n- ~/.catfish/outputs\n"
        "exclude:\n- ~/Library\n"
        "_applied_migrations:\n- catfish-output-2026-07\n- catfish-outputs-2026-08\n",
        encoding="utf-8",
    )
    cfgmod.load_config()
    assert _include() == ["~/.catfish/uploads", "~/.catfish/outputs"]
