#!/usr/bin/env python3
"""setup.sh 和 setup.ps1 必须以同样的方式调用 tools/ 下那三个脚本。

# 这个测试守什么

9/22 把装机逻辑重构成: 复杂判断放 tools/*.py (两平台共用), 两个 wrapper
只干平台相关的事。这样"两份实现跑偏"这件事就只剩一个入口 ——

    **wrapper 调用共享脚本时, 参数传漏了。**

漏一个参数不会报错。比如 setup.ps1 忘了传 --pg-volume, 后果是:
Windows 上重装时既有数据库卷那道闸失效, 生成新 PG_PASSWORD, 四个服务
连不上已有的库, 而装机脚本一路绿灯 —— 跟这道闸当初要防的一模一样。

所以这里不比"行为", 比**参数集合**: 同一个工具, 两边传的 flag 必须一致。

# 为什么是静态比对

理想做法是两边都跑一遍再 diff 结果。但 CI 的 Linux runner 上没有
PowerShell, 装一个要 70MB 且经常拉不动。静态比对抓得住"漏传参数"这个
真实的失效模式, 而且不需要任何运行时。

跑法: python3 test_wrapper_parity.py
退出码: 0 = 一致 / 1 = 不一致
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
SH = HERE / "setup.sh"
PS1 = HERE / "setup.ps1"
TOOLS = HERE / "tools"

# 这些 flag 只在某一边出现是**合理**的, 不算跑偏:
#   --dir      两边都传, 但写法不同 (sh 在数组里, ps1 在 Invoke-Tool 里固定)
#   --user     不是工具的参数, 是 docker run 的 (见 setup.ps1 里那段注释:
#              Windows 上传了反而会让容器进程失去写权限)
IGNORED = {"--dir"}


def declared_flags(tool_src: str) -> tuple[set[str], set[str]]:
    """工具自己 add_argument 声明的参数。返回 (全部, 其中必填的)。

    以**工具的声明**为准集, 而不是从 wrapper 里瞎抓 `--xxx` ——
    第一版就是后者, 结果把 docker 自己的 --rm / --user / --pull /
    --force-recreate 都算成了"工具参数", 三个工具全报不一致。
    准集来自工具本身, 就没有这个问题。
    """
    all_flags = set(re.findall(r'add_argument\(\s*"(--[a-z][a-z0-9-]+)"', tool_src))
    required = {
        m.group(1)
        for m in re.finditer(
            r'add_argument\(\s*"(--[a-z][a-z0-9-]+)"[^)]*required=True', tool_src
        )
    }
    return all_flags - IGNORED, required - IGNORED


def uses(text: str, flag: str) -> bool:
    """脚本里有没有出现这个 flag。

    用词边界匹配 —— 直接 `in` 的话 `--https` 会被 `--https-port` 命中,
    于是"漏传 --https"这种情况反而检测不出来。
    """
    return re.search(rf"{re.escape(flag)}(?![a-z0-9-])", text) is not None


def main() -> int:
    for p in (SH, PS1):
        if not p.exists():
            print(f"❌ 找不到 {p.name}")
            return 1

    sh, ps1 = SH.read_text(), PS1.read_text()
    fail = 0

    for tool in ("envgen.py", "seedgen.py", "certgen.py"):
        src_path = TOOLS / tool
        if not src_path.exists():
            print(f"❌ tools/{tool} 不存在, 但 wrapper 在调它")
            fail = 1
            continue
        if tool not in sh or tool not in ps1:
            who = "setup.sh" if tool not in sh else "setup.ps1"
            print(f"❌ {who} 根本没调 tools/{tool}")
            fail = 1
            continue

        declared, required = declared_flags(src_path.read_text())
        in_sh = {f for f in declared if uses(sh, f)}
        in_ps = {f for f in declared if uses(ps1, f)}

        if in_sh == in_ps:
            print(f"  ✓ {tool}: 两边都传了同样的 {len(in_sh)} 个参数")
        else:
            fail = 1
            print(f"❌ {tool}: 两个 wrapper 传的参数不一样")
            if in_sh - in_ps:
                print(f"     只有 setup.sh 传:  {', '.join(sorted(in_sh - in_ps))}")
            if in_ps - in_sh:
                print(f"     只有 setup.ps1 传: {', '.join(sorted(in_ps - in_sh))}")
            print("     漏传一个参数不会报错, 只会让某一边少一道保护 ——")
            print("     比如 --pg-volume 漏了, 既有数据库卷那道闸就失效了。")

        missing = required - (in_sh & in_ps)
        if missing:
            print(f"❌ {tool}: 声明为必填却没有两边都传: {', '.join(sorted(missing))}")
            fail = 1
        dead = declared - in_sh - in_ps
        if dead:
            print(f"  ⓘ {tool}: 没有 wrapper 在用 (有默认值, 不算错): {', '.join(sorted(dead))}")

    print("")
    print("✓ wrapper 参数一致" if not fail else "✗ wrapper 之间有分歧")
    return fail


if __name__ == "__main__":
    sys.exit(main())
