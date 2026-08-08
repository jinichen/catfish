#!/usr/bin/env python3
"""检查一个解释器链的 SQLite 有没有 WAL-reset 损坏漏洞。

用法 (要用**被检查的那个解释器**跑, 不是本机 python3):

    "$EMBED_PY" scripts/check-embedded-sqlite.py

退出码 0 = 安全, 1 = 有洞。

## 为什么要有这道闸

打进包的 CPython 由 build-mac-resources.sh 的 PYTHON_BUILD_TAG 决定, 而
**同一个 CPython 版本号, 不同的 python-build-standalone 发布链的 SQLite 不一样**:
3.11.15+20260623 链 3.50.4 (有洞), 3.11.15+20260807 链 3.53.1 (没洞)。

PYTHON_BUILD_TAG 是个手写日期。谁哪天为了别的原因调它 (追新补丁、复现旧问题),
很可能顺手换回一个 SQLite 有洞的发布 —— 而后果要等员工机上 state.db 损坏才知道,
那时候不可逆。

别绕过: hermes 自带的缓解**只拒绝给新库开 WAL** (hermes_state.py:647,
"Never downgrades to DELETE if the on-disk DB header reports WAL")。已经是 WAL
的库一律不管 —— 而现场那些跑了几个月的 state.db 全是 WAL。

判据抄 hermes 的 hermes_cli/sqlite_runtime.py:is_sqlite_wal_reset_vulnerable,
不自己发明区间: 上游怎么判我们怎么判, 它改了我们跟着改。
"""

import sqlite3
import sys


def is_vulnerable(version_info) -> bool:
    """跟 hermes_cli/sqlite_runtime.py 同判据。

    有洞 = `>=3.7.0` 且 `<3.51.3`, 且不在两个 backport 区间
    `[3.50.7, 3.51.0)` / `[3.44.6, 3.45.0)` 内。
    """
    parts = [int(x) for x in list(version_info)[:3]]
    parts += [0] * (3 - len(parts))
    v = tuple(parts)
    if v < (3, 7, 0):
        return False
    if v >= (3, 51, 3):
        return False
    if (3, 50, 7) <= v < (3, 51, 0):
        return False
    if (3, 44, 6) <= v < (3, 45, 0):
        return False
    return True


def main() -> int:
    ver = sqlite3.sqlite_version
    if not is_vulnerable(sqlite3.sqlite_version_info):
        print(f"    ✓ SQLite {ver} 不在漏洞范围内")
        return 0
    print(f"    ❌ 内嵌 Python 链的 SQLite {ver} 有 WAL-reset 损坏漏洞")
    print("       需要 3.51.3+, 或 backport 3.50.7 / 3.44.6")
    print("       改 build-mac-resources.sh 的 PYTHON_BUILD_TAG 换一个更新的发布")
    print("       (CPython 版本号可以不变 —— 变的是它链的 SQLite)")
    return 1


if __name__ == "__main__":
    # `--self-test` 只验判据本身, 不看当前解释器 —— 本机 python3 就能跑。
    if "--self-test" in sys.argv:
        CASES = [
            ((3, 50, 4), True, "20260623 那版打进包的"),
            ((3, 53, 1), False, "20260807"),
            ((3, 51, 3), False, "官方修复起点"),
            ((3, 51, 2), True, "差一个补丁"),
            ((3, 50, 7), False, "backport"),
            ((3, 50, 6), True, "backport 前一个"),
            ((3, 44, 6), False, "老 backport"),
            ((3, 44, 5), True, "老 backport 前一个"),
            ((3, 6, 9), False, "太老, 还没这个 bug"),
        ]
        bad = 0
        for v, want, note in CASES:
            got = is_vulnerable(v)
            if got != want:
                bad += 1
            print(f"  {'✓' if got == want else '✗'} {'.'.join(map(str, v)):<8} "
                  f"有洞={str(got):<5} 期望={want}   {note}")
        print("\n全部通过" if not bad else f"\n{bad} 条不符")
        sys.exit(1 if bad else 0)
    sys.exit(main())
