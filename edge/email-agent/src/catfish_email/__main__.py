"""catfish-email CLI 入口。

子命令:
    catfish-email list      列收件箱 (按筛选条件)
    catfish-email read      读单封邮件全文
    catfish-email search    全文搜索

输出格式:
    --json    机器可读 JSON (默认), SKILL helper 用这个
    --human   markdown 表格 / 卡片, 员工自己跑命令时友好

退出码:
    0 = 正常
    1 = adapter 不可用 (没装客户端 / 没账号)
    2 = 参数错
    3 = 邮件不存在 (read --id 找不到)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, is_dataclass
from typing import Any

from .adapters.base import (
    DataNotFoundError,
    EmailAdapter,
    EmailAdapterError,
    ListFilter,
    NotSupportedError,
)
from .inbox import get_adapter


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING if not args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        adapter = get_adapter(args.client)
    except DataNotFoundError as e:
        _err(f"找不到可用的邮件客户端: {e}")
        return 1
    except (ValueError, NotImplementedError) as e:
        _err(f"adapter 选择错: {e}")
        return 2

    if args.cmd == "list":
        return _cmd_list(adapter, args)
    if args.cmd == "read":
        return _cmd_read(adapter, args)
    if args.cmd == "search":
        return _cmd_search(adapter, args)
    if args.cmd == "accounts":
        return _cmd_accounts(adapter, args)

    parser.print_help()
    return 2


# ============================================================
# 命令实现
# ============================================================


def _cmd_accounts(adapter: EmailAdapter, args) -> int:
    accs = adapter.list_accounts()
    if args.json:
        print(json.dumps([asdict(a) for a in accs], ensure_ascii=False, indent=2))
    else:
        print(f"客户端: {adapter.name}, 共 {len(accs)} 个账号:")
        for a in accs:
            mark = "★" if a.is_default else " "
            print(f"  {mark} {a.address}")
    return 0


def _cmd_list(adapter: EmailAdapter, args) -> int:
    filt = ListFilter(
        folder=args.folder,
        account=args.account,
        since=args.since,
        until=args.until,
        sender_contains=args.sender,
        subject_contains=args.subject,
        body_contains=args.body,
        unread_only=args.unread,
        limit=args.limit,
    )
    try:
        msgs = adapter.list_messages(filt)
    except EmailAdapterError as e:
        _err(f"列邮件失败: {e}")
        return 1

    if args.json:
        print(json.dumps([_msg_to_dict(m) for m in msgs], ensure_ascii=False, indent=2))
    else:
        if not msgs:
            print("(没邮件)")
            return 0
        print(f"# {filt.folder}, {len(msgs)} 封")
        print()
        print("| 状态 | 时间 | 主题 | 发件人 |")
        print("|------|------|------|--------|")
        for m in msgs:
            state = "○" if m.is_read else "●"
            star = "⭐" if False else ""  # star 字段在 list snippet 里没暴露, 暂留
            date = (m.date or "")[:16]
            subj = m.subject[:40].replace("|", "\\|")
            sender = m.sender[:30].replace("|", "\\|")
            print(f"| {state}{star} | {date} | {subj} | {sender} |")
    return 0


def _cmd_read(adapter: EmailAdapter, args) -> int:
    try:
        m = adapter.read_message(args.id)
    except DataNotFoundError as e:
        _err(f"邮件不存在: {e}")
        return 3
    except EmailAdapterError as e:
        _err(f"读邮件失败: {e}")
        return 1

    if args.json:
        print(json.dumps(_msg_to_dict(m), ensure_ascii=False, indent=2))
    else:
        print(f"# {m.subject}")
        print()
        print(f"- 发件人: {m.sender}")
        print(f"- 收件人: {', '.join(m.recipients)}")
        if m.cc:
            print(f"- 抄送: {', '.join(m.cc)}")
        print(f"- 时间: {m.date}")
        print(f"- 文件夹: {m.folder}")
        print(f"- 状态: {'已读' if m.is_read else '未读'}")
        if m.has_attachments:
            print(f"- 附件 ({len(m.attachments)}):")
            for att in m.attachments:
                print(f"   - {att.filename} ({att.size_bytes} bytes, {att.content_type})")
        print()
        print("---")
        print()
        print(m.body_text)
    return 0


def _cmd_search(adapter: EmailAdapter, args) -> int:
    try:
        hits = adapter.search(
            args.query,
            account=args.account,
            folder=args.folder,
            limit=args.limit,
        )
    except EmailAdapterError as e:
        _err(f"搜索失败: {e}")
        return 1

    if args.json:
        print(json.dumps([_msg_to_dict(m) for m in hits], ensure_ascii=False, indent=2))
    else:
        if not hits:
            print(f"(没找到 '{args.query}')")
            return 0
        print(f"# 搜索 '{args.query}', 找到 {len(hits)} 封")
        print()
        for m in hits:
            state = "○" if m.is_read else "●"
            print(f"- {state} [{m.date[:16]}] {m.subject}  ← {m.sender}")
    return 0


# ============================================================
# 参数解析
# ============================================================


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="catfish-email",
        description="鲶鱼邮件 agent — 读 Apple Mail / Foxmail / Outlook 桌面客户端的邮件",
    )
    p.add_argument(
        "--client",
        help="显式指定 client (apple-mail / foxmail-mac / outlook-win / foxmail-win); 默认按平台自动选 (macOS → apple-mail 优先, foxmail-mac 兜底)",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="打开 INFO 日志")

    sub = p.add_subparsers(dest="cmd", required=False)

    # accounts
    pa = sub.add_parser("accounts", help="列所有邮箱账号")
    pa.add_argument("--json", action="store_true", default=True, help="(默认) JSON 输出")
    pa.add_argument("--human", dest="json", action="store_false", help="markdown 输出给员工看")

    # list
    pl = sub.add_parser("list", help="列收件箱 (或其它文件夹)")
    pl.add_argument("--folder", default="Inbox", help="文件夹名 (Inbox/Sent/Drafts/收件箱/...)")
    pl.add_argument("--account", help="账号地址 (默认第一个)")
    pl.add_argument("--since", help="ISO date '2026-04-26', 含当天 00:00 起")
    pl.add_argument("--until", help="ISO date '2026-04-27', 不包含")
    pl.add_argument("--sender", help="发件人含此关键词")
    pl.add_argument("--subject", help="主题含此关键词")
    pl.add_argument("--body", help="正文/摘要含此关键词")
    pl.add_argument("--unread", action="store_true", help="只看未读")
    pl.add_argument("--limit", type=int, default=20, help="最多返回多少条 (默认 20)")
    pl.add_argument("--json", action="store_true", default=True)
    pl.add_argument("--human", dest="json", action="store_false")

    # read
    pr = sub.add_parser("read", help="读单封邮件全文")
    pr.add_argument("--id", required=True, help="message id (从 list 输出里拿)")
    pr.add_argument("--json", action="store_true", default=True)
    pr.add_argument("--human", dest="json", action="store_false")

    # search
    ps = sub.add_parser("search", help="全文搜索")
    ps.add_argument("query", help="搜索关键词")
    ps.add_argument("--account")
    ps.add_argument("--folder", default="*", help="* = 跨所有文件夹搜")
    ps.add_argument("--limit", type=int, default=20)
    ps.add_argument("--json", action="store_true", default=True)
    ps.add_argument("--human", dest="json", action="store_false")

    return p


# ============================================================
# 工具
# ============================================================


def _msg_to_dict(m) -> dict[str, Any]:
    """Message dataclass → dict, attachments 也展开。"""
    d = asdict(m)
    return d


def _err(msg: str) -> None:
    print(f"catfish-email: {msg}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
