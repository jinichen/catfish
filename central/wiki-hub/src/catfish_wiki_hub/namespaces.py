"""部门 namespace 规则 + 可见性判定 (9/30).

# namespace 从哪来

`dept/<部门>`, 其中 <部门> **就是发布者身份里的 department 原文**
(gateway 注入的 X-Catfish-User-Dept, 来自 identity-server 用户表)。
服务端推出来, 不再由员工手填。

原来是员工在分享框里手填 `dept/finance`, 有两个问题:

1. 跟身份里的部门没有任何对应关系 —— 身份里是 "研发部" / "总裁办" 这种中文,
   手填的是小写英文, 拿不到可比较的东西, 部门隔离也就无从谈起。
2. 打错一个字母就多出一个部门。

部门名可以是中文, 所以这里的校验只挡路径和 URL 上会出事的字符, 不限字符集。

# 可见性

- admin / sysadmin: 全部
- 其他人: 只看自己部门 (`dept/<自己的 department>`)
- 身份里没有部门的员工: 什么都看不到, 也不能发布
"""
from __future__ import annotations

ADMIN_ROLES = ("admin", "sysadmin")

#: 路径分隔符 / Windows 文件名非法字符 / URL 里有特殊含义的字符
_FORBIDDEN_CHARS = set('/\\<>:"|?*#%')
_MAX_DEPT_LEN = 64


def validate_dept(dept: str) -> str | None:
    """返 None 表示合法, 否则返错误说明."""
    if not dept or not dept.strip():
        return "部门名为空"
    if dept != dept.strip():
        return f"部门名首尾不能有空白: {dept!r}"
    if len(dept) > _MAX_DEPT_LEN:
        return f"部门名太长 ({len(dept)} > {_MAX_DEPT_LEN})"
    if dept.startswith(".") or ".." in dept:
        return f"部门名不能以 . 开头或含 ..: {dept!r}"
    bad = sorted({c for c in dept if c in _FORBIDDEN_CHARS or ord(c) < 32})
    if bad:
        return f"部门名含不允许的字符 {bad!r}: {dept!r}"
    return None


def namespace_for(dept: str) -> str:
    return f"dept/{dept}"


def dept_of(namespace: str) -> str | None:
    """`dept/<部门>` → <部门>; 格式不对返 None."""
    if not namespace.startswith("dept/"):
        return None
    dept = namespace[len("dept/"):]
    if validate_dept(dept) is not None:
        return None
    return dept


def is_admin(user: dict) -> bool:
    return user.get("role") in ADMIN_ROLES


def can_see(user: dict, namespace: str) -> bool:
    if is_admin(user):
        return True
    dept = (user.get("dept") or "").strip()
    return bool(dept) and namespace == namespace_for(dept)
