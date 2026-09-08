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
import logging
import os
import sys

from .adapters.base import DataNotFoundError, EmailAdapter
from .inbox import get_adapter, get_all_adapters

# 命令实现按"必须 method / 可选 method"拆成两个模块 (原 __main__.py 913 行,
# 超了 800 红线)。这里 import 回来有两个作用:
#   1. main() 的 dispatch 要按名字调它们
#   2. 保持 `from catfish_email.__main__ import _cmd_xxx` 仍然可用 ——
#      tests/test_cli_main.py 就是这么 import 的, 拆分不该让调用方改代码
# _msg_to_dict 在本文件里没人用, 纯粹是为了第 2 条; 别当成死导入删掉,
# 删了 test_cli_main.py 会 ImportError。__all__ 把这个意图写死。
from .cli_action import (
    _cmd_attachment,
    _cmd_check,
    _cmd_delete,
    _cmd_draft,
    _cmd_mark_read,
    _cmd_send,
)
from .cli_output import _err, _msg_to_dict
from .cli_read import _cmd_accounts, _cmd_list, _cmd_read, _cmd_search
from .discovery import discover_human, discover_payload

__all__ = [
    "main",
    "_cmd_accounts", "_cmd_list", "_cmd_read", "_cmd_search",
    "_cmd_draft", "_cmd_send", "_cmd_delete", "_cmd_mark_read",
    "_cmd_check", "_cmd_attachment",
    "_msg_to_dict", "_err",
]


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING if not args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.cmd == "discover":
        if args.json:
            import json

            print(json.dumps(discover_payload(), ensure_ascii=False, indent=2))
        else:
            print(discover_human())
        return 0

    # 5/18 BL-EMAIL-MULTI-CLIENT: --client 显式 → 单 adapter; 没传 → 全部 adapter
    # (e.g. Mail.app + Foxmail 同时跑). 防 factory 短路漏 Foxmail 数据.
    # Windows Foxmail 自定义目录由 Companion 通过环境变量传入；此时 factory
    # 会只选择 Foxmail，避免 Outlook COM 错误污染结果。
    selected_client = args.client or os.environ.get("CATFISH_EMAIL_CLIENT", "").strip() or None
    try:
        if selected_client:
            adapters: list[EmailAdapter] = [get_adapter(selected_client)]
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
    if args.cmd == "mark-read":
        return _cmd_mark_read(adapters, args)
    if args.cmd == "delete":
        return _cmd_delete(adapters, args)
    if args.cmd == "send":
        return _cmd_send(adapters, args)
    if args.cmd == "attachment":
        return _cmd_attachment(adapters, args)
    # P3.5.204.c (7/9 鸿波): 触发客户端立即从服务器 fetch new mail
    if args.cmd == "check":
        return _cmd_check(adapters, args)

    parser.print_help()
    return 2



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

    # P3.5.204.c (7/9): check — 触发客户端立即从服务器 fetch new mail
    pc = sub.add_parser("check", help="触发客户端立即从邮箱服务器 fetch 新邮件 (不等定时同步)")
    pc.add_argument("--account", help="账号地址 (默认全部账号一起同步)")
    pc.add_argument("--json", action="store_true", default=True)
    pc.add_argument("--human", dest="json", action="store_false")

    # accounts
    pa = sub.add_parser("accounts", help="列所有邮箱账号")
    pa.add_argument("--json", action="store_true", default=True, help="(默认) JSON 输出")
    pa.add_argument("--human", dest="json", action="store_false", help="markdown 输出给员工看")

    # discover — 不读取邮件正文，只报告 Windows 客户端和账号可用性。
    pdiscover = sub.add_parser(
        "discover", help="自动发现 Outlook/Foxmail 客户端和邮箱账号"
    )
    pdiscover.add_argument("--json", action="store_true", default=True)
    pdiscover.add_argument("--human", dest="json", action="store_false")

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
    # 5/18 BL-EMAIL-MARK-READ: 默认读完自动标已读 (跟邮件客户端一致); --no-mark-read 关
    pr.add_argument(
        "--no-mark-read",
        dest="mark_read",
        action="store_false",
        default=True,
        help="不要把这封标记为已读 (默认: 读完自动标已读)",
    )

    # mark-read (5/18 BL-EMAIL-MARK-READ): 独立 subcommand 也能批量标 (不读正文)
    pm = sub.add_parser("mark-read", help="标记邮件已读/未读")
    pm.add_argument("--id", required=True, help="message id")
    pm.add_argument(
        "--unread", action="store_true", help="反向: 标回未读"
    )
    pm.add_argument("--json", action="store_true", default=True)
    pm.add_argument("--human", dest="json", action="store_false")

    # delete (5/18 BL-EMAIL-DELETE): 移邮件到客户端 Trash (软删, 不彻底)
    pdel = sub.add_parser(
        "delete",
        help="把邮件移到客户端 Trash (软删 — Trash 30 天内可恢复)",
    )
    pdel.add_argument("--id", required=True, help="message id")
    pdel.add_argument("--json", action="store_true", default=True)
    pdel.add_argument("--human", dest="json", action="store_false")

    # send (5/18 BL-EMAIL-COMPOSE-SEND): 真发 Drafts 里的草稿
    # 红线: AI 永不应该直接调这个, 必须 Companion UI 人工 confirm 之后才调.
    psend = sub.add_parser(
        "send",
        help="把 Drafts 里的草稿真发出去 (红线: 不要 AI 直接调, 必须人工确认)",
    )
    psend.add_argument("--id", required=True, help="草稿的 message id")
    psend.add_argument("--json", action="store_true", default=True)
    psend.add_argument("--human", dest="json", action="store_false")

    # attachment (P3.5.103 6/24 鸿波 catch "附件不能点"): 导出附件到本地 tmp,
    # 返 path. 前端 Companion 拿到 path 调 open_file 系统默认 app 打开.
    patt = sub.add_parser(
        "attachment",
        help="导出邮件附件到本地 tmp 文件, JSON 返 {path}",
    )
    patt.add_argument("--id", required=True, help="邮件 id (含 client 前缀)")
    patt.add_argument("--filename", required=True, help="附件 filename (从 read 返的 attachments 里挑)")
    patt.add_argument("--json", action="store_true", default=True)

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


if __name__ == "__main__":
    sys.exit(main())
