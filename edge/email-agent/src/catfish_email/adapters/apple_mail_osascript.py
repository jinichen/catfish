"""Apple Mail · osascript 执行层与输出解析。

从 apple_mail.py 抽出来的一组模块级纯函数 (8/13)。上一次拆分是 5/20
(BL-AM-SPLIT, 1538 → ~1045 行, AS 模板进 apple_mail_scripts.py), 之后主文件
又长回 1160 行, 越过 800 行红线。

这里放的是**跟 adapter 状态无关**的那一半 —— "怎么调 osascript、怎么把它吐出来
的字节串解析回结构"。判据很干脆: 这些函数一个 `self` 都不碰。剩在 apple_mail.py
的是 AppleMailAdapter 类本身。

分层:

    apple_mail_scripts.py    AS 源码模板 (字符串常量)
    apple_mail_osascript.py  ← 本模块: 跑模板 + 解析 stdout + 错误映射
    apple_mail_emlx.py       .emlx 文件格式解析 (不给 Automation 权限时的兜底)
    apple_mail.py            AppleMailAdapter —— 把上面三层编排起来

# 关于 monkeypatch

apple_mail.py 会把这里的名字重新 import 进它自己的命名空间。类方法在调用时是从
**apple_mail 的 globals** 里查名字的, 所以测试里 `monkeypatch.setattr(am, "_run_osascript", ...)`
照样生效 —— 跟 apple_mail_emlx 那批名字是同一个套路 (测试里已经在这么用
`am._detect_mail_data_dir`)。
"""
from __future__ import annotations

import logging
import os
import subprocess

from .apple_mail_scripts import _AS_PING
from .base import (
    ClientNotRunningError,
    DataNotFoundError,
    EmailAdapterError,
)

logger = logging.getLogger("catfish_email.adapters.apple_mail")

# ASCII 控制字符做分隔符 — 邮件正文不可能出现
FS = "\x1f"  # field separator (单元分隔符)
RS = "\x1e"  # record separator (记录分隔符)

# P3.3.66 (6/13 鸿波): 30s → 60s. 大邮箱同步 / 慢网 / Mail.app 正在拉新邮件
# 时偶发 30s 不够撞 TimeoutExpired. 60s 给更宽松窗口. 集团 / 员工本机如果
# 还需要调可走 env CATFISH_OSASCRIPT_TIMEOUT (秒, 整数).
def _resolve_osascript_timeout() -> float:
    env = os.environ.get("CATFISH_OSASCRIPT_TIMEOUT", "").strip()
    if env:
        try:
            v = float(env)
            if v > 0:
                return v
        except ValueError:
            pass
    return 60.0

_OSASCRIPT_TIMEOUT_SECS = _resolve_osascript_timeout()


def _run_osascript(
    script: str, *, timeout: float = _OSASCRIPT_TIMEOUT_SECS,
) -> str:
    """跑 AppleScript 返 stdout (去末尾换行). 错误映射到 EmailAdapterError 子类."""
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise EmailAdapterError(
            f"Apple Mail AppleScript 超时 ({timeout}s) — "
            f"Mail 卡死或同步太慢? 重启 Mail.app 再试.",
        ) from e
    except FileNotFoundError as e:
        raise EmailAdapterError(
            "找不到 osascript — 不在 macOS 上跑? "
            "Apple Mail adapter 仅支持 macOS.",
        ) from e

    if result.returncode != 0:
        err = (result.stderr or "").strip()
        err_lower = err.lower()
        if "MESSAGE_NOT_FOUND" in err or "8001" in err:
            raise DataNotFoundError(
                "Mail 里找不到这条消息 (id 错 / 邮件已删 / 不在该账号下).",
            )
        if "not allowed" in err_lower or "not authorized" in err_lower or "1743" in err:
            raise ClientNotRunningError(
                "macOS 没给 catfish '控制 Mail' 的权限. 去 "
                "System Settings → Privacy & Security → Automation, "
                "找运行 catfish 的 terminal / catfish-companion, 勾上 Mail. "
                "(macOS 第一次调 osascript 应该已弹过这个窗.)",
            )
        if "Application isn't running" in err or "(-600)" in err or "isn't running" in err_lower:
            raise ClientNotRunningError(
                "Mail.app 没在跑. 先打开 Mail 再调 catfish-email.",
            )
        # 5/18 BL-EMAIL-APPLEMAIL-INVALID-INDEX (-1719): "不能获得 account 1
        # whose name = X 无效的索引". 真因是 id 里塞的 account name 在 Mail.app
        # 找不到 (jini.chen@icloud.com 这种邮箱地址 ≠ Mail 内部账号名 "iCloud").
        # 翻译成友好提示 + 引导走 list_accounts 拿真实名.
        if "-1719" in err or "无效的索引" in err or "Invalid index" in err:
            raise DataNotFoundError(
                "Apple Mail 找不到这个账号 (id 里的 account name 不对). "
                "Mail.app 内部账号名跟邮箱地址可能不同 "
                "(比如 jini.chen@icloud.com 对应的内部名是 'iCloud'). "
                "用 `catfish-email accounts --json` 看真实账号名, "
                "或直接从 `catfish-email list --json` 拷完整 id."
            )
        raise EmailAdapterError(
            f"AppleScript 失败 (exit={result.returncode}): {err[:300]}",
        )
    return result.stdout.rstrip("\n")


def _is_mail_running() -> bool:
    """检查 Mail.app 进程在跑. 不抛 (探测用)."""
    try:
        out = _run_osascript(_AS_PING, timeout=5)
        return out.strip().lower() == "true"
    except EmailAdapterError:
        return False


def _parse_thread_headers(raw_headers: str) -> tuple[str | None, str | None, str | None]:
    """从 RFC 822 raw headers 字符串 parse 出 (Message-ID, In-Reply-To, References).

    P3.5.58 (6/22 鸿波 catch "有回复了为啥还让小鲶处理, 是不是重复了"):
    thread 检测三件套, 让前端 isReplied() 算
        ∃ R: R.in_reply_to == M.message_id OR M.message_id ∈ R.references.split()

    headers 形如:
        Message-ID: <abc123@domain.com>
        In-Reply-To: <parent456@domain.com>
        References: <root789@domain.com> <middle@x.com> <parent456@domain.com>

    缺/坏 返 None. 不抛 — 老邮件可能没 References / 内部转发可能没 Message-ID.

    # ★ 8/6: 从 email.parser 改成逐行扫

    原来用 `Parser(policy=compat32).parsestr(raw, headersonly=True)`。看着最标准,
    实际在真实邮件上大面积失败 —— 鸿波本机 5 封实测, **3 封连 Message-ID 都读不出来**,
    而 rawHdrs 明明有 5000+ 字符。

    真因: `email.parser` 见到**一行既没有冒号、又不是以空白开头的续行**, 就认为
    header 段结束了, 剩下的全当 body。而营销/通知类邮件的 X- 头里塞满了 JSON、
    base64、tracking 串, 被中转 MTA 硬折一次就会出现这种裸行。折断点之后的
    Message-ID / In-Reply-To / References 就再也读不到:

        >>> Parser(...).parsestr("A: 1\\n裸行没冒号\\nMessage-ID: <x>\\n", headersonly=True).keys()
        ['A']                      # ← Message-ID 掉进 body 了

    而且这个坑一直没被发现, 因为上层有兜底:

        final_msg_id = parsed_mid or (rfc_msg_id.strip() if rfc_msg_id else None)

    message_id 靠 AppleScript 的独立字段 `message id of m` 救回来了, 只有
    in_reply_to / references 没兜底 → 前端看到的就是「Message-ID 有值、另外两个
    永远是空」, 而 isReplied() 失败时只是角标不亮, 看起来跟「这封确实没人回」
    一模一样, 不报错也不刺眼。

    现在改成只认这三行, 中间有什么脏东西都不管:
      - header 名大小写不敏感 (RFC 5322 §2.2)
      - 支持折行续行 (下一行以 space/tab 开头就接上去)
      - 同名头取第一个 (RFC 说这三个应当唯一; 真出现多个, 第一个最接近原始)
    """
    if not raw_headers:
        return (None, None, None)
    want = {"message-id": None, "in-reply-to": None, "references": None}
    cur: str | None = None
    try:
        for line in raw_headers.splitlines():
            if line[:1] in (" ", "\t"):
                # 折行续行: 接到当前正在收集的头上
                if cur is not None and want[cur] is not None:
                    want[cur] += " " + line.strip()
                continue
            cur = None
            idx = line.find(":")
            if idx <= 0:
                continue          # 裸行 / 空行 —— 跳过, **不当作 header 段结束**
            name = line[:idx].strip().lower()
            if name in want and want[name] is None:
                want[name] = line[idx + 1 :].strip()
                cur = name
    except Exception as e:  # noqa: BLE001
        logger.debug("_parse_thread_headers fail: %s", e)
        return (None, None, None)
    return (
        want["message-id"] or None,
        want["in-reply-to"] or None,
        want["references"] or None,
    )


def _read_thread_headers_from_source_file(source_path: str) -> tuple[str | None, str | None, str | None]:
    """从 read_message AS dump 出的 RFC822 source 文件 parse 三件套.

    P3.5.58 helper for read_message_as. 文件首段是 headers + 空行 + body, 用
    email.parser headersonly=True 只 parse 头. 文件不存在/读失败返全 None.
    """
    try:
        with open(source_path, encoding="utf-8", errors="replace") as f:
            # 只读到第一个空行 = header 边界. 不全读省内存 (附件大的可能 MB 级).
            header_lines: list[str] = []
            for line in f:
                if line.strip() == "":
                    break
                header_lines.append(line)
            raw = "".join(header_lines)
        return _parse_thread_headers(raw)
    except OSError as e:
        logger.debug("read thread headers fail: %s", e)
        return (None, None, None)


def _parse_records(text: str, n_fields: int) -> list[list[str]]:
    """osascript stdout split 成 records of fields. 末尾空记录 / 短记录跳过.

    P3.5.101 (6/24 鸿波 catch '彻底治 pre-existing fail'): strip 显式列要去的
    字符, 不用 .strip() 默认行为.

    # 真因 (找了 P3.5.58 ship 时漏的 bug)

    Python str.strip() 默认 strip 全部 isspace()=True 的字符, 包括 ASCII
    控制字符 \\x1c-\\x1f (File/Group/Record/Unit Separator). 这跟
    string.whitespace 6 个 (space/tab/cr/lf/vt/ff) **不一致**.

    P3.5.58 (6/22) 升 AS list 6→8 字段时, 老 Mail.app 不暴露 all headers,
    rawHdrs 字段返空字符串. AS 输出末尾形如 `...{FS}<rfcId>{FS}{RS}`. strip()
    默认行为吃掉末尾 `\\x1f` (FS = chr(31) isspace=True), split 出 7 字段
    而非 8, _parse_records 全部跳过, list_messages 返 []. test fail 因此暴露.

    修法: 显式只 strip 4 个常见 whitespace (space/tab/cr/lf), 不动 \\x1c-\\x1f.
    """
    records: list[list[str]] = []
    for rec in text.split(RS):
        rec = rec.strip(" \t\n\r")
        if not rec:
            continue
        fields = rec.split(FS)
        if len(fields) < n_fields:
            logger.debug(
                "跳过格式错的记录 (字段 %d < %d): %r",
                len(fields), n_fields, rec[:100],
            )
            continue
        records.append(fields[:n_fields])
    return records

def _escape_as_string(s: str) -> str:
    """把 Python 字符串 escape 成可安全 inline 到 AS 双引号字面量的形式.

    AS 字符串只需 escape `"` 和 `\\`. 不允许 raw newline (会破 AS 语法),
    替成空格.
    """
    return (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", " ")
        .replace("\r", " ")
    )
