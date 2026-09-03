"""跨 adapter 的契约不变式 —— 能力 flag 不许说谎。

# 为什么有这个文件

`outlook_win.py` 曾经写着 `supports_drafts = True`, 注释是
"CreateItem(0).Save() 存 Drafts (W3 集成阶段实现)" —— 把**打算实现**当成
**已经实现**写进了 flag, 而 `create_draft` 一直没 override。

`__main__.py:_cmd_draft` 正是靠这个 flag 挑 adapter。Windows 上候选只有
outlook_win, 于是它必被选中, 然后 create_draft 落到基类抛 NotSupportedError。
员工点"起草回复"拿到"该 adapter 不支持起草", 而 flag 一直在说支持。

这类 bug 靠人看不出来: flag 和方法在同一个文件里隔着三百多行, 而且**在
非 Windows 机器上永远不会触发** —— macOS 上 OutlookWinAdapter 连实例化都
过不去。所以它躲过了全部单测和日常使用, 只在 Windows 员工手里炸。

# 这里检查什么

`supports_drafts is True` ⟹ 这个类 (或它的某个非 EmailAdapter 祖先) 真的
override 了 `create_draft`。

adapter 是**自动发现**的 —— 往 adapters/ 里加新 adapter, 不用改这个文件就
自动纳入检查。这是刻意的: 要是得手工登记, 下一个 adapter 的作者一样会忘。

# 这里刻意不检查什么

反方向 (`supports_drafts is False` 但 create_draft 有实现) 不查。因为
FoxmailMacAdapter 正是这样 —— 它 override create_draft 只为了给一条更清楚的
错误消息, 这是对的。要区分"override 成真实现" 和 "override 成抛错" 得去
AST 看函数体, 而那个方向从来没出过事, 代价换不来收益。
"""
from __future__ import annotations

import importlib
import pkgutil

import pytest

from catfish_email import adapters as adapters_pkg
from catfish_email.adapters.base import EmailAdapter


# 能力 flag → 它承诺已实现的方法名。以后加新 flag 在这里加一行就行。
CAPABILITY_CONTRACT: dict[str, str] = {
    "supports_drafts": "create_draft",
}


def _all_adapter_classes() -> list[type[EmailAdapter]]:
    """把 adapters 包全 import 一遍, 收集所有 EmailAdapter 子类。

    子模块必须真 import 成功才会被发现 —— 所以 import 失败要**抛出来**,
    不能 try/except 吞掉。吞掉的话这个测试会退化成"没发现 adapter, 于是
    没有违规, 于是全绿", 那就跟没写一样。
    """
    for mod in pkgutil.iter_modules(adapters_pkg.__path__):
        importlib.import_module(f"{adapters_pkg.__name__}.{mod.name}")

    found: list[type[EmailAdapter]] = []

    def walk(cls: type) -> None:
        for sub in cls.__subclasses__():
            found.append(sub)
            walk(sub)

    walk(EmailAdapter)
    return found


def _liars(classes: list[type]) -> list[str]:
    """返回违反契约的 (类名, flag, 方法) 描述列表。空 list = 都守规矩。"""
    out: list[str] = []
    for cls in classes:
        for flag, method in CAPABILITY_CONTRACT.items():
            if not getattr(cls, flag, False):
                continue
            if getattr(cls, method) is getattr(EmailAdapter, method):
                out.append(
                    f"{cls.__module__}.{cls.__name__}: {flag}=True "
                    f"但没有 override {method}() —— 调用时会从基类抛 "
                    f"NotSupportedError, 而 flag 说它支持"
                )
    return out


def test_adapter_discovery_actually_finds_adapters():
    """先证明发现机制是活的。

    没有这一条, 下面那个测试在"一个 adapter 都没发现"时会假绿 —— 而那正是
    最容易发生的失效方式 (改包结构 / 改 import 路径都可能悄悄打断发现)。
    """
    classes = _all_adapter_classes()
    names = {c.__name__ for c in classes}
    assert len(classes) >= 3, f"只发现 {len(classes)} 个 adapter, 发现机制可能断了: {names}"
    # 已知 adapter 必须在里面 —— 少任何一个都说明发现漏了
    for expected in (
        "AppleMailAdapter",
        "FoxmailMacAdapter",
        "FoxmailWinAdapter",
        "OutlookWinAdapter",
    ):
        assert expected in names, f"{expected} 没被发现, 实际发现: {sorted(names)}"


def test_no_adapter_claims_a_capability_it_did_not_implement():
    """能力 flag 必须对应真实现。这是本文件的正事。"""
    problems = _liars(_all_adapter_classes())
    assert not problems, "能力 flag 说谎:\n  " + "\n  ".join(problems)


def test_outlook_win_drafts_flag_is_false_until_really_implemented():
    """钉住这次修的具体那一条。

    单独写一条而不是只靠上面的通用检查: 通用检查告诉你"有 adapter 说谎",
    这一条告诉你"是 outlook_win, 而且它现在应该是 False"。将来谁真在
    Windows 上实现了 create_draft, 会同时看到这条测试要改 —— 那是个提醒,
    不是障碍。
    """
    from catfish_email.adapters.outlook_win import OutlookWinAdapter

    implemented = (
        OutlookWinAdapter.create_draft is not EmailAdapter.create_draft
    )
    assert OutlookWinAdapter.supports_drafts is implemented, (
        "outlook_win 的 supports_drafts 跟 create_draft 是否实现对不上。"
        "实现了就把 flag 翻 True (记得先在真 Windows 机器上手测过)。"
    )


def test_contract_check_catches_a_liar():
    """变异测试: 造一个说谎的 adapter, 确认检查真的会响。

    没有这一条, 上面的检查可能因为写错 (getattr 拿错名字 / 比较写反) 而
    永远返回空 list —— 那种失效跟"全都守规矩"长得一模一样, 在 CI 上是同一片绿。
    """

    class LyingAdapter(EmailAdapter):
        name = "lying"
        supports_drafts = True  # 说支持, 但下面根本没实现 create_draft

        def list_accounts(self):  # pragma: no cover - 不会被调
            return []

        def list_messages(self, filt):  # pragma: no cover
            return []

        def read_message(self, message_id):  # pragma: no cover
            raise NotImplementedError

        def search(self, query, *, account=None, folder="Inbox", limit=30):  # pragma: no cover
            return []

    problems = _liars([LyingAdapter])
    assert len(problems) == 1, f"检查没抓到说谎的 adapter: {problems}"
    assert "LyingAdapter" in problems[0]
    assert "create_draft" in problems[0]


def test_contract_check_passes_an_honest_adapter():
    """反向确认: 老实实现了的不该被误报, 否则这检查会变成噪音被人关掉。"""

    class HonestAdapter(EmailAdapter):
        name = "honest"
        supports_drafts = True

        def list_accounts(self):  # pragma: no cover
            return []

        def list_messages(self, filt):  # pragma: no cover
            return []

        def read_message(self, message_id):  # pragma: no cover
            raise NotImplementedError

        def search(self, query, *, account=None, folder="Inbox", limit=30):  # pragma: no cover
            return []

        def create_draft(self, **kw):  # 真 override 了
            return "draft-1"

    assert _liars([HonestAdapter]) == []


@pytest.mark.parametrize("flag,method", sorted(CAPABILITY_CONTRACT.items()))
def test_contract_table_points_at_real_base_methods(flag: str, method: str):
    """契约表里的名字必须在基类上真实存在。

    打错一个字 (e.g. "create_drafts") 会让 getattr(EmailAdapter, method) 抛
    AttributeError, 但那是在 _liars 里、只有存在说谎 adapter 时才走到的分支 ——
    平时全绿, 真出事时反而崩。这里提前钉死。
    """
    assert hasattr(EmailAdapter, flag), f"基类没有 {flag} 这个 flag"
    assert callable(getattr(EmailAdapter, method, None)), f"基类没有 {method}() 方法"
