"""users.py / users_admin.py 拆分后的守卫 (8/15)。

# 病历

users.py 832 行, 过了 CLAUDE.md §1 的 800 红线。里面是一个 756 行的
`UserRegistry` 类。8/15 按"谁在用"切成两半:

    users.py        载入与查询 —— routes.py / routes_token.py 每次登录都走的热路径
    users_admin.py  admin CRUD —— 只有 admin_router.py 调, IT 管理员的低频操作

用 mixin (`class UserRegistry(UserAdminMixin)`) 而不是模块级函数, 是为了
`registry.create_user(...)` 这些调用点一个字不用改。

# 这个文件钉两件事

## 1. `from .db import get_pool` 必须留在函数体里

users.py / users_admin.py 里有 10 处这样的写法:

    async def create_user(self, ...):
        from .db import get_pool  # noqa: PLC0415

看着像能提到模块顶部的样板代码。**不能提。**
tests/test_change_password.py 是这么打桩的:

    monkeypatch.setattr("catfish_identity.db.get_pool", _fake_get_pool)

它改的是 **db 模块上的那个绑定**。函数体内 import 在**调用时**才去 db 取,
取到的是被 patch 过的假货; 提到模块顶部就成了 import 时的快照, patch 再也
影响不到。这一点直接验过:

    db.get_pool = _fake
    函数体内 import →  拿到桩 ✓
    模块级 import   →  拿到原来的真货 ✗

## ⚠ 但这条守卫在 CI / 沙箱里**变异测不出来**, 所以更需要它

8/15 拆完之后做了变异验证: 把 change_password 里那行 import 提到模块顶部,
本守卫红了, 而 **test_change_password.py 7 条全绿**。

一开始以为是守卫多余。查下来是环境的锅 —— 那台机器没装 asyncpg, 真
`get_pool()` 走 fallback 也返 `None`, 跟桩返回的 `None` **完全不可区分**。
换句话说: 在没有 PG 的环境里, 这个 bug 是隐形的。

而 test_change_password.py 自己的 fixture docstring 写着:

    军规 7/20: 用户本机 5432 PG 已跑 · db.get_pool() 会返真 pool · 且
    _POOL module-level cache 跨 test 复用 · 撞出 'another operation is
    in progress'

也就是说这个 bug **只在开发者本机现形**, 而且现形的样子是一条看不出根因的
"another operation is in progress"。这正是要用静态守卫钉住它的理由: 行为测试
在这件事上是瞎的。

(同一天在 catfish-cli 上踩的是同一条病的另一面: 那边是 monkeypatch 打在
re-export 的绑定上, 这边是 import 提错时机。病根都是 `from X import name`
建的是新绑定不是别名。)

## 2. 拆出去的方法一个都不能少

mixin 的坏处是"少搬一个方法"不会立刻报错 —— 直到某个 admin 接口被调到才
AttributeError, 而那可能是生产上 IT 管理员点"新建用户"的时候。所以这里对着
一张显式名单核。
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from catfish_identity.users import UserRegistry
from catfish_identity.users_admin import UserAdminMixin

_SRC = Path(__file__).resolve().parent.parent / "src" / "catfish_identity"

#: 5/10 BL-ARCH1 P1 定下的 admin CRUD 接口。加接口要来这里加一行 ——
#: 是个必须有人动手的动作, 不是自动推导。
ADMIN_METHODS = [
    "list_users", "create_user", "update_user", "lock_user",
    "delete_user", "reset_password", "change_password", "list_audit",
]

#: 登录热路径。这几个搬走的话性能和依赖方向都要重新想。
CORE_METHODS = [
    "reload", "seed_pg_from_yaml_if_empty", "reload_from_pg",
    "find", "verify_password",
]


@pytest.mark.parametrize("name", ADMIN_METHODS)
def test_admin_方法都还在(name: str):
    assert hasattr(UserRegistry, name), (
        f"UserRegistry 少了 {name} —— mixin 拆漏了。这种漏不会在 import 时报错, "
        "要等 admin_router 真调到才 AttributeError。"
    )
    assert name in vars(UserAdminMixin), (
        f"{name} 不在 UserAdminMixin 里 —— 是搬回 users.py 了吗? "
        "搬回去 users.py 就会重新逼近 800 红线。"
    )


@pytest.mark.parametrize("name", CORE_METHODS)
def test_热路径方法留在users_py(name: str):
    assert name in vars(UserRegistry), (
        f"{name} 从 UserRegistry 本体挪走了。它是每次登录都要走的路径 "
        "(routes.py / routes_token.py), 挪之前先想清楚。"
    )


def test_mixin在MRO里且顺序对():
    assert UserAdminMixin in UserRegistry.__mro__, "UserRegistry 没继承 UserAdminMixin"
    # UserRegistry 自己的定义必须优先于 mixin —— 万一将来两边同名, 本体说了算
    assert UserRegistry.__mro__.index(UserRegistry) < UserRegistry.__mro__.index(UserAdminMixin)


def test_两边没有同名方法():
    """同名会被 MRO 静默遮蔽 —— 遮蔽本身不报错, 只是有一份代码永远不执行。"""
    own = {k for k, v in vars(UserRegistry).items()
           if callable(v) and not k.startswith("__")}
    mix = {k for k, v in vars(UserAdminMixin).items()
           if callable(v) and not k.startswith("__")}
    assert not (own & mix), (
        f"users.py 和 users_admin.py 各定义了一份: {sorted(own & mix)} —— "
        "mixin 那份会被静默遮蔽, 永远不执行。"
    )


# ── get_pool 的 import 时机 ────────────────────────────────


@pytest.mark.parametrize("fname", ["users.py", "users_admin.py"])
def test_get_pool_只许在函数体里import(fname: str):
    """★★★ 提到模块顶部 = test_change_password 的桩静默失效, 测试去连真 PG。"""
    tree = ast.parse((_SRC / fname).read_text(encoding="utf-8"))
    bad = [
        ast.unparse(n) for n in tree.body
        if isinstance(n, (ast.Import, ast.ImportFrom)) and "get_pool" in ast.unparse(n)
    ]
    assert not bad, (
        f"{fname} 在模块级 import 了 get_pool: {bad}\n"
        "必须留在函数体里 —— tests/test_change_password.py 打的是 "
        "`catfish_identity.db.get_pool`, 模块级 import 拿的是快照, 桩会失效, "
        "测试会去连真 PG。"
    )


def test_get_pool_的函数体import一处没少():
    """拆分前 10 处。少了说明某个方法悄悄改成走别的路子了 —— 值得看一眼。"""
    total = 0
    for fname in ("users.py", "users_admin.py"):
        tree = ast.parse((_SRC / fname).read_text(encoding="utf-8"))
        top = {id(n) for n in tree.body}
        total += sum(
            1 for n in ast.walk(tree)
            if isinstance(n, (ast.Import, ast.ImportFrom))
            and id(n) not in top and "get_pool" in ast.unparse(n)
        )
    assert total == 10, (
        f"函数体内的 get_pool import 变成了 {total} 处 (拆分时是 10)。\n"
        "多了少了都行, 但请顺手把这个数字改掉, 并确认新增的那处也在函数体里。"
    )


def test_两个文件都在红线以下():
    for fname in ("users.py", "users_admin.py"):
        n = len((_SRC / fname).read_text(encoding="utf-8").splitlines())
        assert n < 800, f"{fname} {n} 行, 过了 CLAUDE.md §1 的 800 红线"


def test_mixin不该被单独实例化():
    """UserAdminMixin 靠 UserRegistry 提供 _users 和 verify_password。

    单独 new 一个不会报错 (Python 不检查), 但一调方法就 AttributeError。
    这条不是拦 bug, 是把这个约定写在测试里 —— 免得有人看它是个 class 就直接用。
    """
    m = UserAdminMixin()
    assert not hasattr(m, "_users"), (
        "UserAdminMixin 自己有 _users 了? 那状态就有两个来源了, "
        "UserRegistry.__init__ 里那份和这里这份会打架。"
    )
    assert not hasattr(m, "verify_password"), "verify_password 应该由 UserRegistry 提供"


#: 8 个 admin 方法在**拆分前**的签名, 逐字从 users.py 拆分前那一版取出来的。
#:
#: ⚠ 第一版我是凭印象手写的, `list_users` 写成了
#:   `..., limit: int = 500) -> list[dict]` —— 实际它没有 limit 参数,
#:   返回的也是 `list[IdentityUser]`。测试当场红了。
#:   这种表只能从代码里取, 不能凭印象敲。
_SIGNATURES_BEFORE_SPLIT = {
    "list_users":
        "(self, *, include_deleted: 'bool' = False, department_filter: 'str | None' = None,"
        " role_filter: 'str | None' = None) -> 'list[IdentityUser]'",
    "create_user":
        "(self, *, email: 'str', password: 'str', name: 'str' = '', department: 'str' = '',"
        " role: 'str' = 'employee', managed_departments: 'list[str] | None' = None,"
        " created_by: 'str' = 'system', must_change_password: 'bool' = True) -> 'tuple[bool, str]'",
    "update_user":
        "(self, target_email: 'str', *, by_email: 'str', name: 'str | None' = None,"
        " department: 'str | None' = None, role: 'str | None' = None,"
        " managed_departments: 'list[str] | None' = None,"
        " allowed_models: 'list[str] | str | None' = None,"
        " allowed_tools: 'list[str] | str | None' = None,"
        " allowed_skills: 'list[str] | str | None' = None) -> 'tuple[bool, str]'",
    "lock_user":
        "(self, target_email: 'str', *, by_email: 'str', locked: 'bool') -> 'tuple[bool, str]'",
    "delete_user":
        "(self, target_email: 'str', *, by_email: 'str') -> 'tuple[bool, str]'",
    "reset_password":
        "(self, target_email: 'str', *, by_email: 'str', new_password: 'str',"
        " force_change: 'bool' = True) -> 'tuple[bool, str]'",
    "change_password":
        "(self, email: 'str', *, old_password: 'str', new_password: 'str') -> 'tuple[bool, str]'",
    "list_audit":
        "(self, limit: 'int' = 100) -> 'list[dict]'",
}


@pytest.mark.parametrize("name", sorted(_SIGNATURES_BEFORE_SPLIT))
def test_搬过去的方法签名没变(name: str):
    """纯搬运的意思是签名逐字不变。改签名是接口改动, 该单独一个 commit。

    admin_router.py 是唯一的调用方, 它按关键字传参 —— 改个默认值不会报错,
    只会让某个 admin 接口的行为悄悄变了。
    """
    got = str(inspect.signature(getattr(UserRegistry, name)))
    want = _SIGNATURES_BEFORE_SPLIT[name]
    assert got == want, f"{name} 签名变了:\n  拆分前 {want}\n  现在   {got}"
