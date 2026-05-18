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
from .inbox import get_adapter, get_all_adapters


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING if not args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # 5/18 BL-EMAIL-MULTI-CLIENT: --client 显式 → 单 adapter; 没传 → 全部 adapter
    # (e.g. Mail.app + Foxmail 同时跑). 防 factory 短路漏 Foxmail 数据.
    try:
        if args.client:
            adapters: list[EmailAdapter] = [get_adapter(args.client)]
        else:
            adapters = get_all_adapters()
            if not adapters:
                _err("找不到任何可用邮件客户端 (Mail.app 没开 / Foxmail 没装)")
                return 1
    except DataNotFoundError as e:
        _err(f"找不到可用的邮件客户端: {e}")
        return 1
    except (ValueError, NotImplementedError) as e:
        _err(f"adapter 选择错: {e}")
        return 2

    if args.cmd == "list":
        return _cmd_list(adapters, args)
    if args.cmd == "read":
        return _cmd_read(adapters, args)
    if args.cmd == "search":
        return _cmd_search(adapters, args)
    if args.cmd == "accounts":
        return _cmd_accounts(adapters, args)
    if args.cmd == "draft":
        return _cmd_draft(adapters, args)

    parser.print_help()
    return 2


# ============================================================
# 命令实现
# ============================================================


def _cmd_accounts(adapters: list[EmailAdapter], args) -> int:
    """5/18 BL-EMAIL-MULTI-CLIENT: 跨所有 adapter (Mail.app + Foxmail) 列账号."""
    all_accs = []
    for adapter in adapters:
        try:
            accs = adapter.list_accounts()
        except EmailAdapterError as e:
            print(f"⚠ {adapter.name} 列账号失败: {e}", file=sys.stderr)
            continue
        # 每个 account dict 加 client 字段标识从哪来
        for a in accs:
            d = asdict(a)
            d["client"] = adapter.name
            all_accs.append(d)

    if args.json:
        print(json.dumps(all_accs, ensure_ascii=False, indent=2))
    else:
        print(f"共 {len(all_accs)} 个账号 (跨 {len(adapters)} 个客户端):")
        for d in all_accs:
            mark = "★" if d.get("is_default") else " "
            print(f"  {mark} [{d['client']}] {d['address']}")
    return 0


def _cmd_list(adapters: list[EmailAdapter], args) -> int:
    """5/18 BL-EMAIL-MULTI-CLIENT: 跨所有 adapter (Mail.app + Foxmail) + 所有账号合并查.

    BL-EMAIL-MULTI-ACCOUNT (同日): --account 没传 → 遍历每个 adapter 的所有账号;
    显式传 --account 还是单账号 (员工只看某个账号时用).
    """
    msgs = []
    errors: list[str] = []
    for adapter in adapters:
        # 这个 adapter 里要查哪些账号
        if args.account:
            accounts_to_query: list[str | None] = [args.account]
        else:
            try:
                accs = adapter.list_accounts()
                accounts_to_query = [a.address for a in accs] or [None]
            except EmailAdapterError as e:
                errors.append(f"[{adapter.name}] 列账号失败: {e}")
                continue

        for acc_addr in accounts_to_query:
            filt = ListFilter(
                folder=args.folder,
                account=acc_addr,
                since=args.since,
                until=args.until,
                sender_contains=args.sender,
                subject_contains=args.subject,
                body_contains=args.body,
                unread_only=args.unread,
                # 每账号取 limit, 最后再 trim. 防某账号占满 limit 把其他账号挤掉.
                limit=args.limit,
            )
            try:
                msgs.extend(adapter.list_messages(filt))
            except EmailAdapterError as e:
                # 单账号失败不阻塞 (Gmail INBOX 名兼容性 / Foxmail 没数据等), 记下继续
                errors.append(f"[{adapter.name}] {acc_addr}: {e}")
                continue

    # 跨账号按 date 降序合并, 再 trim 到 limit
    msgs.sort(key=lambda m: m.date or "", reverse=True)
    msgs = msgs[: args.limit]

    # 错误进 stderr, stdout 留 JSON / markdown (跨 adapter / 跨账号场景, 部分挂不阻塞)
    for err in errors:
        print(f"⚠ {err}", file=sys.stderr)

    if args.json:
        print(json.dumps([_msg_to_dict(m) for m in msgs], ensure_ascii=False, indent=2))
    else:
        if not msgs:
            print("(没邮件)")
            return 0
        print(f"# {args.folder}, {len(msgs)} 封 (跨 {len(adapters)} 个客户端)")
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


def _cmd_read(adapters: list[EmailAdapter], args) -> int:
    """5/18 BL-EMAIL-MULTI-CLIENT: 不知道 id 来自哪个 adapter, 逐个 try.
    Apple Mail id (AS message id 整数) 跟 Foxmail id (文件路径 hash) 不会撞.
    """
    last_err: Exception | None = None
    for adapter in adapters:
        try:
            m = adapter.read_message(args.id)
            break  # 找到了
        except DataNotFoundError as e:
            last_err = e
            continue  # 试下一个
        except EmailAdapterError as e:
            _err(f"[{adapter.name}] 读邮件失败: {e}")
            return 1
    else:
        _err(f"邮件不存在 (跨 {len(adapters)} 个客户端都没找到): {last_err}")
        return 3

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


def _cmd_search(adapters: list[EmailAdapter], args) -> int:
    """5/18 BL-EMAIL-MULTI-CLIENT: 跨所有 adapter 搜, 合并 + 按 date 排."""
    hits = []
    errors: list[str] = []
    for adapter in adapters:
        try:
            hits.extend(adapter.search(
                args.query,
                account=args.account,
                folder=args.folder,
                limit=args.limit,
            ))
        except EmailAdapterError as e:
            errors.append(f"[{adapter.name}] 搜索失败: {e}")
            continue

    hits.sort(key=lambda m: m.date or "", reverse=True)
    hits = hits[: args.limit]

    for err in errors:
        print(f"⚠ {err}", file=sys.stderr)

    if args.json:
        print(json.dumps([_msg_to_dict(m) for m in hits], ensure_ascii=False, indent=2))
    else:
        if not hits:
            print(f"(没找到 '{args.query}')")
            return 0
        print(f"# 搜索 '{args.query}', 找到 {len(hits)} 封 (跨 {len(adapters)} 个客户端)")
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

    # draft (BL-COMPANION-EMAIL-TAB-STEP2 5/18): 起草邮件落 Drafts, 不发送 (红线).
    # 不调 LLM 生成 (那是 Companion / hermes 的事), 这里只是 thin wrapper 把
    # to/cc/bcc/subject/body 落到客户端 Drafts. 调用方负责正文内容.
    pd = sub.add_parser("draft", help="起草邮件到客户端 Drafts (不发送)")
    pd.add_argument("--to", required=True, help="收件人, 多人用逗号分隔")
    pd.add_argument("--cc", default="", help="抄送, 多人逗号")
    pd.add_argument("--bcc", default="", help="密送, 多人逗号")
    pd.add_argument("--subject", required=True)
    pd.add_argument("--body", help="正文 (字符串). 跟 --body-file 二选一")
    pd.add_argument("--body-file", help="正文从文件读 (避 shell 转义坑, --body 长时用)")
    pd.add_argument("--in-reply-to", help="原邮件 id (回复时引用, 让客户端串 thread)")
    pd.add_argument("--account", help="从哪个账号起草 (默认第一个)")
    pd.add_argument("--json", action="store_true", default=True)
    pd.add_argument("--human", dest="json", action="store_false")

    return p


def _cmd_draft(adapters: list[EmailAdapter], args) -> int:
    """5/18 BL-COMPANION-EMAIL-TAB-STEP2: 起草到 Drafts, 不发送.

    红线: 这是只读+落 Drafts 的语义, 永不调 send. 员工自己去客户端点发送.
    多 adapter 时: 找第一个 supports_drafts=True 的 (Foxmail Mac 不支持).
    """
    from .adapters.base import NotSupportedError  # noqa: PLC0415

    # 拿 body
    if args.body and args.body_file:
        _err("--body 跟 --body-file 二选一, 不能同时给")
        return 2
    if args.body_file:
        try:
            with open(args.body_file, encoding="utf-8") as f:
                body = f.read()
        except OSError as e:
            _err(f"读 {args.body_file} 失败: {e}")
            return 2
    else:
        body = args.body or ""

    # 找一个 supports_drafts=True 的 adapter (Apple Mail 支持, Foxmail Mac 不支持)
    target = None
    for a in adapters:
        if getattr(a, "supports_drafts", False):
            target = a
            break
    if target is None:
        _err(
            f"没找到支持起草的 adapter (现有: {', '.join(a.name for a in adapters)}). "
            f"Apple Mail.app 支持; Foxmail Mac 不支持 (设计限制, 写入不可靠).",
        )
        return 1

    to_list = [t.strip() for t in args.to.split(",") if t.strip()]
    cc_list = [t.strip() for t in (args.cc or "").split(",") if t.strip()]
    bcc_list = [t.strip() for t in (args.bcc or "").split(",") if t.strip()]

    try:
        msg_id = target.create_draft(
            to=to_list,
            cc=cc_list,
            bcc=bcc_list,
            subject=args.subject,
            body=body,
            in_reply_to=args.in_reply_to,
            account=args.account,
        )
    except NotSupportedError as e:
        _err(f"该 adapter 不支持起草: {e}")
        return 1
    except EmailAdapterError as e:
        _err(f"起草失败: {e}")
        return 1

    if args.json:
        print(json.dumps(
            {"draft_id": msg_id, "adapter": target.name, "to": to_list, "subject": args.subject},
            ensure_ascii=False, indent=2,
        ))
    else:
        print(f"✓ 草稿已落 {target.name} Drafts (id={msg_id})")
        print(f"  收件人: {', '.join(to_list)}")
        print(f"  主题:   {args.subject}")
        print(f"  → 打开 Mail.app Drafts 文件夹 review + 点发送")
    return 0


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
