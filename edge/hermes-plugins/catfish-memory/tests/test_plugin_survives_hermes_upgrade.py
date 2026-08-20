"""catfish-memory 必须装在 hermes 树**外**, 否则升级当天静默消失。

# 来历 (8/20)

审批鉴权那条修完之后清点遗留项, 发现两条 catfish 插件的软链位置是不对称的:

    ~/.hermes/plugins/catfish-xcatfish-user               树外 → 升级后存活
    ~/.hermes/hermes-agent/plugins/memory/catfish-memory  树内 → 随树一起被换掉

而大版本升级换的正是整棵 `hermes-agent/`。对照两个升级脚本:
`catfish-xcatfish-user` 在 upgrade-hermes-v019.sh / v020.sh 里都点了名,
`catfish-memory` **一个都没有** —— 只在安装脚本和日志脚本里出现过。

## 失败形状是静默的

升级之后 `config.yaml` 里 `memory.provider: catfish-memory` 还在, 软链没了:

    plugins/memory/__init__.py:201
        \"\"\"Returns None if the provider is not found or fails to load.\"\"\"
        logger.warning("Failed to load memory provider '%s': %s", name, e)
        return None

一条 warning, 然后返 None。记忆静默停止工作, agent 照常回答, 只是不记事了 ——
而 agent.log 里 warning 本来就是几百条量级, 多一条不会有人注意。

## 为什么是"移出树"而不是"往升级脚本加一行"

上游本来就支持树外的记忆 provider, 不需要我们额外约定:

    hermes_cli/plugins.py            扫 bundled / user(~/.hermes/plugins) / project 三个根
    hermes_cli/plugins.py:1613       user 装的记忆 provider auto-coerce 成 kind="exclusive"
    plugins/memory/_iter_provider_dirs()  第 2 步就是扫 user 目录
    plugins/memory/find_provider_dir()    load_memory_provider 用它解析, 也查 user 目录

8/20 用模拟 HERMES_HOME 跑过上游真实代码路径, `catfish-memory` 放在
`$HERMES_HOME/plugins/` 下确实跟 8 个内置 provider 并列被发现。

放树外之后, 升级脚本**不需要知道它** —— 少一处"必须记得同步"的地方。

# 这个文件钉什么

钉我们新依赖的那几个上游行为, 外加我们自己这侧的位置约定。判据都故意宽一格:
只验"这个行为还在", 不验实现细节 —— 上游重构是常态, 这些断言只该在**行为消失**
时红。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

_PLUGIN_DIR = Path(__file__).resolve().parent.parent
_HERMES_ROOT = Path(
    os.environ.get("HERMES_ROOT", Path.home() / ".hermes" / "hermes-agent")
)
_HERMES_HOME = _HERMES_ROOT.parent

_needs_hermes = pytest.mark.skipif(
    not (_HERMES_ROOT / "plugins" / "memory" / "__init__.py").exists(),
    reason=f"没有 hermes 树可对照 ({_HERMES_ROOT}) —— CI/沙箱里跳过",
)


# ─────────────────────────────────────────────────────────────────────
# 1. 我们这侧: 软链的位置
# ─────────────────────────────────────────────────────────────────────
@_needs_hermes
def test_软链不许待在hermes树内():
    """`hermes-agent/plugins/memory/` 底下不许再有 catfish 的东西。

    这条是**病因的直接判据** —— 有人把链建回树里, 病就回来了, 而且要等到
    下次升级才发作。
    """
    memory_dir = _HERMES_ROOT / "plugins" / "memory"
    offenders = [
        str(c.relative_to(_HERMES_ROOT))
        for c in memory_dir.iterdir()
        if "catfish" in c.name.lower()
    ]
    assert not offenders, (
        "catfish 插件又被装进 hermes 树里了: " + ", ".join(offenders) + "\n"
        "大版本升级会把整棵 hermes-agent/ 换掉, 这条链跟着没 —— 而且是静默的:\n"
        "  provider 加载失败只打一条 warning 然后返 None, 记忆停摆但 agent 照常回答。\n"
        "改用 ~/.hermes/plugins/ (树外). 见 install-catfish-memory.sh。"
    )


@_needs_hermes
def test_树外那条链在且指得对():
    """`~/.hermes/plugins/catfish-memory` 必须存在, 且指向本仓库这份源码。

    判据分两段, 是有原因的:

      · **链存在 + 目标路径名对** —— 任何环境都验得了
      · **目标真能解析** —— 只在"目标够得着"时才验

    第二段要加条件, 是因为软链的 target 是 macOS 绝对路径
    (`/Users/<u>/person_task/...`)。在别的挂载视角下 (比如容器里按别的路径
    挂同一份仓库) 这条链是断的 —— 那是**观察环境**的问题, 不是被测对象坏了。
    无条件断言"能解析"会让测试在那种环境里红, 而红的原因跟它要守的东西无关。

    但也不能因此就只验"链存在" —— 那就宽得能放过"指向一个完全无关的目录"。
    所以路径名那一段是无条件的。
    """
    link = _HERMES_HOME / "plugins" / "catfish-memory"
    assert link.is_symlink() or link.exists(), (
        f"{link} 不存在 —— catfish-memory 没装到树外。跑 install-catfish-memory.sh。"
    )

    target = os.readlink(link) if link.is_symlink() else str(link)
    assert target.rstrip("/").endswith(
        os.path.join("edge", "hermes-plugins", "catfish-memory")
    ), (
        f"{link} 指向 {target} —— 不是仓库里那份 catfish-memory。"
    )

    resolved = link.resolve()
    if resolved.exists():
        assert (resolved / "__init__.py").exists(), (
            f"{link} 指向 {resolved}, 但那里没有 __init__.py —— 指错目录了。"
        )


# ─────────────────────────────────────────────────────────────────────
# 2. 上游行为: 树外发现这条路还通吗
# ─────────────────────────────────────────────────────────────────────
@_needs_hermes
def test_上游仍然扫user目录找记忆provider():
    """`_iter_provider_dirs()` 必须还有"扫 user plugins 目录"这一步。

    上游哪天只扫 bundled 了, catfish-memory 就不再被发现 —— 故障形状跟当年
    一模一样 (静默返 None), 而且我们这侧一行代码都不会报错。
    """
    src = (_HERMES_ROOT / "plugins" / "memory" / "__init__.py").read_text(
        encoding="utf-8", errors="replace"
    )
    assert "_get_user_plugins_dir" in src, (
        "上游 plugins/memory 不再有 _get_user_plugins_dir —— "
        "树外的记忆 provider 可能不再被扫到。重新评估安装位置。"
    )
    assert re.search(r"def _iter_provider_dirs", src), (
        "_iter_provider_dirs 不见了 —— provider 发现机制变了, 去读实现。"
    )
    # 行为判据: user 目录那一段必须真的被 iterate
    m = re.search(
        r"user_dir\s*=\s*_get_user_plugins_dir\(\)(.{0,600})", src, re.S
    )
    assert m and "iterdir" in m.group(1), (
        "拿了 user plugins 目录却不再遍历它 —— 树外 provider 不会被发现。"
    )


@_needs_hermes
def test_上游find_provider_dir也查user目录():
    """`load_memory_provider` 走的是 `find_provider_dir` —— 它也得查 user 目录。

    只有 `_iter_provider_dirs` 查而 `find_provider_dir` 不查的话, 表现会很怪:
    `hermes memory list` 里看得见 catfish-memory, 真加载却返 None。
    """
    src = (_HERMES_ROOT / "plugins" / "memory" / "__init__.py").read_text(
        encoding="utf-8", errors="replace"
    )
    m = re.search(r"def find_provider_dir\(.*?\n(.*?)(?=\ndef )", src, re.S)
    assert m, "find_provider_dir 不见了 —— 结构变了, 重新评估"
    body = m.group(1)
    assert "_get_user_plugins_dir" in body or "user" in body.lower(), (
        "find_provider_dir 不再查 user plugins 目录 —— "
        "树外的 catfish-memory 会「看得见但加载不了」。"
    )


# ─────────────────────────────────────────────────────────────────────
# 3. 那条 8192 字节的判据 —— 一个会静默失效的边
# ─────────────────────────────────────────────────────────────────────
@_needs_hermes
def test_上游认provider的判据还是那两个词():
    """`_is_memory_provider_dir` 的判据: 前 8192 字节里出现两个标记之一。

    判据变了 (换关键词 / 改成读 manifest) 而我们不知道的话, catfish-memory
    会从 user 目录的扫描结果里被静默剔除。
    """
    src = (_HERMES_ROOT / "plugins" / "memory" / "__init__.py").read_text(
        encoding="utf-8", errors="replace"
    )
    m = re.search(r"def _is_memory_provider_dir\(.*?\n(.*?)(?=\ndef )", src, re.S)
    assert m, "_is_memory_provider_dir 不见了 —— 判据变了, 去读实现"
    body = m.group(1)
    assert "register_memory_provider" in body and "MemoryProvider" in body, (
        "上游认 memory provider 的关键词变了 —— catfish-memory/__init__.py "
        "要跟着改, 否则会被静默跳过。"
    )
    assert "8192" in body, (
        "上游改了扫描窗口大小 —— test_标记必须落在扫描窗口内 的 8192 要同步更新。"
    )


def test_标记必须落在扫描窗口内():
    """我们自己的 `__init__.py`: 两个标记必须在前 8192 字节内。

    这条不需要 hermes 树, 所以不 skip —— CI 里也要跑。

    为什么值得单独钉: 这是个**静默失效的边**。有人在文件顶部加一段长注释,
    把 `register_memory_provider` 挤过 8192 字节, `_is_memory_provider_dir()`
    就不再认它 —— 插件从 user 目录扫描结果里消失, 记忆停摆, 没有任何报错。

    8/20 实测: MemoryProvider 在第 29 字节, register_memory_provider 在第 339
    字节, 文件总长 7770 字节。余量很大, 但"余量大"不是"不会发生"。
    """
    WINDOW = 8192
    raw = (_PLUGIN_DIR / "__init__.py").read_bytes()
    hits = {
        m.decode(): raw.find(m)
        for m in (b"register_memory_provider", b"MemoryProvider")
    }
    found = {k: v for k, v in hits.items() if 0 <= v < WINDOW}
    assert found, (
        f"__init__.py 前 {WINDOW} 字节里一个标记都没有 (位置: {hits}, "
        f"文件长 {len(raw)} 字节) —— hermes 的 _is_memory_provider_dir() 不会认它, "
        "插件会从 user 目录扫描结果里静默消失。把标记往文件前面挪。"
    )
