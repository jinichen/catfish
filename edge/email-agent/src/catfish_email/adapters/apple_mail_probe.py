"""Apple Mail 可用性探测 —— 判断这台机器上这个 adapter 值不值得算进候选。

8/8 新加。数据目录探测复用 `apple_mail_emlx._detect_mail_data_dir` —— 不复制一份:
同一个判据两份实现, 迟早会漂成两个答案, 而这类漂移只在现场才发现。

## 为什么需要"探测"这个概念

`inbox.get_all_adapters()` 原来是**无条件**把 apple-mail 算进候选的 ——
`_get_adapter_explicit` 只是 `return AppleMailAdapter()`, 构造时什么都不查。
于是在一台**从来没配过 Mail.app** 的机器上 (`~/Library/Mail/` 里连 V* 目录都没有),
每一次列邮件、每一个账号、每一次刷新, 都会多出一条注定失败的记录:

    ⚠ [apple_mail] 列账号失败: Mail.app 没在跑且没本地 EMLX 缓存. 先打开 Mail 再调.

8/8 鸿波实测的机器就是这样: Foxmail 那条路一直好好的 (直读 SQLite, 客户端开不开
都行), 邮件却"一堆错误" —— 全是 apple_mail 的噪音, 跟能不能收信毫无关系。
更糟的是刷新按钮: `_cmd_check` 里 apple_mail 进 errs、foxmail 走基类的
NotSupportedError 进 unsupported, `ok_names` 空 → 退出码 4 → 错误直接冒到界面。

而"这台机器用不用 Apple Mail"是**一次本地文件检查**就能判的, 不需要等它失败。

## 判据

    有 EMLX 数据目录        → 可用 (Mail.app 开不开都能读)
    没有, 但 Mail.app 在跑   → 可用 (走 AppleScript)
    都不是                  → **不可用**, 跳过

两条是"或"的关系, 因为 adapter 本来就有两条路 (AppleScript / EMLX 只读兜底),
任一条通就有价值。

## 出错时一律判"可用"

探测本身失败 (pgrep 不在、超时、权限古怪) 时返 True, 不返 False。

两种误判的代价不对称:
  · 误判不可用 → 真在用 Mail.app 的员工**收不到邮件**, 而且界面上什么都不会说
  · 误判可用   → 回到今天这个样子, 多几条噪音日志

所以宁可留着。这个探测的定位是"砍掉确定无用的候选", 不是"精确判断"。
"""
from __future__ import annotations

import logging
import subprocess

from .apple_mail_emlx import _detect_mail_data_dir

logger = logging.getLogger(__name__)

# 说明: `_detect_mail_data_dir()` 返 None 有两种可能 —— 目录真不存在, 或者**没有
# 完全磁盘访问权限** (macOS TCC 把权限拒绝表现成 `is_dir()` False)。两种都返 None,
# 对这里来说结论一样: EMLX 这条路走不通, 再看 Mail.app 在不在跑。


def mail_app_running() -> bool:
    """Mail.app 进程在不在。

    用 `pgrep -x Mail` 而不是 osascript: 后者启动就要 100ms 起, 而且 Mail 没开时
    要等它超时 —— 恰恰是这个探测想避免的开销。pgrep 是毫秒级。

    `-x` 是精确匹配进程名, 免得被 "Mailplane" / "MailMate" 之类误命中。

    探测不出来时返 True, 理由见模块 docstring。
    """
    try:
        result = subprocess.run(
            ["pgrep", "-x", "Mail"],
            capture_output=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.debug("pgrep 探测 Mail.app 失败 (当作在跑, 不跳过): %s", e)
        return True
    # 0 = 找到; 1 = 没找到; 其余 = pgrep 自己出错 → 当作在跑
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    logger.debug("pgrep 返回 %d (非 0/1), 当作在跑不跳过", result.returncode)
    return True


def apple_mail_available() -> bool:
    """这台机器上 apple-mail adapter 值不值得算进候选。"""
    if _detect_mail_data_dir() is not None:
        return True
    if mail_app_running():
        return True
    logger.info(
        "apple-mail 跳过: ~/Library/Mail 下没有 V* 数据目录, Mail.app 也没在跑 "
        "—— 这台机器没在用 Apple Mail (或没给完全磁盘访问权限)"
    )
    return False


def unavailable_reason() -> str:
    """跳过时给调用方的说明。说清楚是"没配过"还是"没开", 不要笼统说"先打开 Mail"。"""
    if _detect_mail_data_dir() is None:
        return (
            "这台机器没在用 Apple Mail: ~/Library/Mail 下没有 V* 数据目录。"
            "如果确实在用, 检查是否给了完全磁盘访问权限 "
            "(系统设置 → 隐私与安全性 → 完全磁盘访问权限)。"
        )
    return "Mail.app 没在跑, 且本地 EMLX 缓存不可读。"
