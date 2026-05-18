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
import sqlite3
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
    if args.cmd == "mark-read":
        return _cmd_mark_read(adapters, args)
    if args.cmd == "delete":
        return _cmd_delete(adapters, args)
    if args.cmd == "send":
        return _cmd_send(adapters, args)

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
    # 5/18 BL-EMAIL-LIST-ADAPTER-FIELD: 跟踪每条 msg 来自哪个 adapter,
    # 输出 JSON 时注入 adapter 字段, 方便 jq group_by(.adapter) / debug.
    msgs: list[tuple[str, Any]] = []  # (adapter_name, Message)
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
                msgs.extend((adapter.name, m) for m in adapter.list_messages(filt))
            except EmailAdapterError as e:
                # 单账号失败不阻塞 (Gmail INBOX 名兼容性 / Foxmail 没数据等), 记下继续
                errors.append(f"[{adapter.name}] {acc_addr}: {e}")
                continue
            except (FileNotFoundError, OSError, sqlite3.Error) as e:  # noqa: PERF203
                # 5/18 BL-EMAIL-ACCOUNT-CROSS-ADAPTER-CRASH: 兜底防御 — adapter
                # 没把底层 IO/DB 异常包成 EmailAdapterError 就直接漏到 _cmd_list.
                # 单 adapter 漏的应该改 adapter (raise DataNotFoundError), 这里
                # 兜一层保跨 adapter 流不挂. 真正想看哪挂用 --debug 看 traceback.
                errors.append(f"[{adapter.name}] {acc_addr}: {type(e).__name__}: {e}")
                continue

    # 跨账号按 date 降序合并, 再 trim 到 limit
    msgs.sort(key=lambda am: am[1].date or "", reverse=True)
    msgs = msgs[: args.limit]

    # 错误进 stderr, stdout 留 JSON / markdown (跨 adapter / 跨账号场景, 部分挂不阻塞)
    for err in errors:
        print(f"⚠ {err}", file=sys.stderr)

    if args.json:
        print(json.dumps(
            [_msg_to_dict(m, adapter_name=a) for (a, m) in msgs],
            ensure_ascii=False, indent=2,
        ))
    else:
        if not msgs:
            print("(没邮件)")
            return 0
        print(f"# {args.folder}, {len(msgs)} 封 (跨 {len(adapters)} 个客户端)")
        print()
        print("| 客户端 | 状态 | 时间 | 主题 | 发件人 |")
        print("|--------|------|------|------|--------|")
        for (a, m) in msgs:
            state = "○" if m.is_read else "●"
            star = "⭐" if False else ""  # star 字段在 list snippet 里没暴露, 暂留
            date = (m.date or "")[:16]
            subj = m.subject[:40].replace("|", "\\|")
            sender = m.sender[:30].replace("|", "\\|")
            print(f"| {a} | {state}{star} | {date} | {subj} | {sender} |")
    return 0


def _cmd_read(adapters: list[EmailAdapter], args) -> int:
    """5/18 BL-EMAIL-MULTI-CLIENT + 5/18 BL-EMAIL-READ-ROUTING-BY-PREFIX:
    id 含 client 前缀 (`foxmail-mac|...|...` / `apple_mail|...`) → 直接路由对应 adapter.
    无前缀 → 兼容老 2 段格式, 逐个 try.

    背景实盘 5/18: 老逻辑"逐个 try" 把 `foxmail-mac|...` id 先丢 Apple Mail,
    Apple Mail _unpack_id 用 `|` 切, account_name 错成 'foxmail-mac', AS 找不到这
    账号挂 -1719. 改成按 id 前缀显式路由就不会跨 adapter 试错.
    """
    msg_id = args.id
    # 5/18 BL-EMAIL-ID-EMPTY-SENTINEL: 防 shell 里 `$(... | jq -r '.[0].id')` 在空
    # list 时给"null" 字符串 → 直接报无效 id 友好提示, 不浪费一次 adapter loop.
    if not msg_id or msg_id.lower() in {"null", "undefined", "none"}:
        _err(
            f"邮件 id 不能为空 (收到 {msg_id!r}). "
            "如果你跑的是 `$(catfish-email list ... | jq -r '.[0].id')` 而 jq 返了 null, "
            "意味着 list 返了空数组 (没未读邮件). 用 `jq -r '.[0].id // empty'` "
            "防 shell 拿到 'null' 字面量."
        )
        return 2
    # 探测前缀路由: 第一段匹配某 adapter.name → 该 adapter.
    # 注: id 保持完整传给 adapter — 各 adapter 的 _unpack_id 验证整段格式,
    # 不能剥前缀 (Foxmail 的 _unpack_id 要 3 段含前缀才认).
    target_adapter: EmailAdapter | None = None
    if "|" in msg_id:
        prefix = msg_id.split("|", 1)[0]
        for a in adapters:
            # adapter.name 是 'apple_mail' / 'foxmail_mac' (下划线),
            # 前缀也可能写 'apple-mail' / 'foxmail-mac' (横线), 都认.
            if a.name == prefix or a.name.replace("_", "-") == prefix:
                target_adapter = a
                break

    adapter_used: str | None = None  # 5/18 BL-EMAIL-LIST-ADAPTER-FIELD
    if target_adapter is not None:
        try:
            m = target_adapter.read_message(msg_id)
            adapter_used = target_adapter.name
        except DataNotFoundError as e:
            _err(f"邮件不存在 [{target_adapter.name}]: {e}")
            return 3
        except ValueError as e:
            # 5/18 BL-EMAIL-ID-FORMAT-UX: adapter 的 _unpack_id 漏 ValueError
            # (员工手工拼 id 拼错 / 用了 list 文档里的占位符 "...")
            _err(
                f"邮件 id 格式不对: {e}. "
                f"用 `catfish-email list --json` 拷完整 id, 不要手工拼."
            )
            return 2
        except EmailAdapterError as e:
            _err(f"[{target_adapter.name}] 读邮件失败: {e}")
            return 1
    else:
        # 无前缀 / 不认识的前缀 → 兼容老 2 段格式, 逐 adapter try
        last_err: Exception | None = None
        last_value_err: ValueError | None = None
        m = None
        for adapter in adapters:
            try:
                m = adapter.read_message(msg_id)
                adapter_used = adapter.name
                break
            except DataNotFoundError as e:
                last_err = e
                continue
            except ValueError as e:
                # 同上 UX 修
                last_value_err = e
                continue
            except EmailAdapterError as e:
                _err(f"[{adapter.name}] 读邮件失败: {e}")
                return 1
        if m is None:
            # 所有 adapter 都因 id 格式不认 → 友好提示而不是 "不存在"
            if last_err is None and last_value_err is not None:
                _err(
                    f"邮件 id 格式不对 (所有客户端都不认): {last_value_err}. "
                    f"用 `catfish-email list --json` 拷完整 id."
                )
                return 2
            _err(f"邮件不存在 (跨 {len(adapters)} 个客户端都没找到): {last_err}")
            return 3

    # 5/18 BL-EMAIL-MARK-READ: 读完默认自动标已读 (跟普通邮件客户端体验一致),
    # --no-mark-read 关. 失败不阻塞输出 — 已经把正文拉回来了, 标已读挂 stderr 警告
    # 不让 read 命令返非零. (Apple Mail EMLX fallback 模式不支持时 NotSupportedError
    # 也走这条 stderr 路径.)
    if getattr(args, "mark_read", False) and not m.is_read and adapter_used is not None:
        owner_adapter = next((a for a in adapters if a.name == adapter_used), None)
        if owner_adapter is not None:
            try:
                owner_adapter.mark_read(args.id, read=True)
                m = m.__class__(**{**asdict(m), "is_read": True})  # 让 JSON 输出反映新状态
            except EmailAdapterError as e:
                print(f"⚠ [{adapter_used}] 标已读失败 (正文已读取): {e}", file=sys.stderr)

    if args.json:
        print(json.dumps(_msg_to_dict(m, adapter_name=adapter_used), ensure_ascii=False, indent=2))
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


def _cmd_mark_read(adapters: list[EmailAdapter], args) -> int:
    """5/18 BL-EMAIL-MARK-READ: 独立 subcommand. 不读正文只改状态.

    跟 _cmd_read 同 id 路由 (按 client 前缀): foxmail_mac|... → foxmail adapter.
    无前缀 → 逐个 try (兼容老 id).
    """
    msg_id = args.id
    read = not args.unread

    # 5/18 BL-EMAIL-ID-EMPTY-SENTINEL: 同 _cmd_read, 防 jq null
    if not msg_id or msg_id.lower() in {"null", "undefined", "none"}:
        _err(
            f"邮件 id 不能为空 (收到 {msg_id!r}). "
            "用 `jq -r '.[0].id // empty'` 防空 list 返 'null' 字面量."
        )
        return 2

    target_adapter: EmailAdapter | None = None
    if "|" in msg_id:
        prefix = msg_id.split("|", 1)[0]
        for a in adapters:
            if a.name == prefix or a.name.replace("_", "-") == prefix:
                target_adapter = a
                break

    if target_adapter is not None:
        candidates = [target_adapter]
    else:
        candidates = list(adapters)

    last_err: Exception | None = None
    last_value_err: ValueError | None = None
    for a in candidates:
        try:
            a.mark_read(msg_id, read=read)
            if args.json:
                print(json.dumps(
                    {"adapter": a.name, "id": msg_id, "read": read, "ok": True},
                    ensure_ascii=False,
                ))
            else:
                print(f"✓ [{a.name}] 标{'已读' if read else '未读'}: {msg_id}")
            return 0
        except DataNotFoundError as e:
            last_err = e
            continue  # 试下个 adapter
        except ValueError as e:
            # 5/18 BL-EMAIL-ID-FORMAT-UX: id 格式错 (员工手抠 id / 用 "..." 占位符)
            last_value_err = e
            continue
        except EmailAdapterError as e:
            _err(f"[{a.name}] mark_read 失败: {e}")
            return 1

    # 5/18 BL-EMAIL-MARK-READ-MSG: 文案区分按前缀路由 vs 跨所有 adapter 搜.
    # 按前缀精确路由时只在那一个客户端里找, 报错应该明说"在 X 客户端里没找到",
    # 不是误导性的"跨 1 个客户端" (听着像广撒网失败实际是单点查无).
    if last_err is None and last_value_err is not None:
        _err(
            f"邮件 id 格式不对: {last_value_err}. "
            f"用 `catfish-email list --json` 拷完整 id."
        )
        return 2
    if target_adapter is not None:
        _err(f"[{target_adapter.name}] 邮件不存在: {last_err}")
    else:
        _err(f"邮件不存在 (跨 {len(candidates)} 个客户端都没找到): {last_err}")
    return 3


def _cmd_delete(adapters: list[EmailAdapter], args) -> int:
    """5/18 BL-EMAIL-DELETE: 移邮件到客户端 Trash 文件夹 (软删).

    跟 _cmd_mark_read 同 id 路由: 按前缀 → 单 adapter, 无前缀 → 逐 adapter try.
    Foxmail Mac 不支持 → NotSupportedError → 友好提示让用户去客户端删.
    """
    from .adapters.base import NotSupportedError  # noqa: PLC0415

    msg_id = args.id

    # 5/18 BL-EMAIL-ID-EMPTY-SENTINEL: 同 _cmd_read, 防 jq null
    if not msg_id or msg_id.lower() in {"null", "undefined", "none"}:
        _err(
            f"邮件 id 不能为空 (收到 {msg_id!r}). "
            "用 `jq -r '.[0].id // empty'` 防空 list 返 'null' 字面量."
        )
        return 2

    target_adapter: EmailAdapter | None = None
    if "|" in msg_id:
        prefix = msg_id.split("|", 1)[0]
        for a in adapters:
            if a.name == prefix or a.name.replace("_", "-") == prefix:
                target_adapter = a
                break

    candidates = [target_adapter] if target_adapter is not None else list(adapters)
    last_err: Exception | None = None
    last_value_err: ValueError | None = None
    last_not_supported: NotSupportedError | None = None
    for a in candidates:
        try:
            a.delete_message(msg_id)
            if args.json:
                print(json.dumps(
                    {"adapter": a.name, "id": msg_id, "deleted": True, "ok": True},
                    ensure_ascii=False,
                ))
            else:
                print(f"✓ [{a.name}] 已移到 Trash: {msg_id}")
            return 0
        except DataNotFoundError as e:
            last_err = e
            continue
        except ValueError as e:
            last_value_err = e
            continue
        except NotSupportedError as e:
            # 这 adapter 不支持 (e.g. Foxmail Mac) — 记下试下个, 都不支持才报
            last_not_supported = e
            continue
        except EmailAdapterError as e:
            _err(f"[{a.name}] 删邮件失败: {e}")
            return 1

    if last_value_err is not None and last_err is None and last_not_supported is None:
        _err(
            f"邮件 id 格式不对: {last_value_err}. "
            f"用 `catfish-email list --json` 拷完整 id."
        )
        return 2
    if last_not_supported is not None and last_err is None:
        # 路由到的 adapter 全是 NotSupportedError (典型: 用户传 foxmail-mac| id)
        _err(
            f"邮件所在的客户端不支持自动删除: {last_not_supported}. "
            "请去客户端 (Foxmail / Outlook 等) 自己删."
        )
        return 4  # 区分: 4=不支持, 跟 1/2/3 都不同
    if target_adapter is not None:
        _err(f"[{target_adapter.name}] 邮件不存在: {last_err}")
    else:
        _err(f"邮件不存在 (跨 {len(candidates)} 个客户端都没找到): {last_err}")
    return 3


def _cmd_send(adapters: list[EmailAdapter], args) -> int:
    """5/18 BL-EMAIL-COMPOSE-SEND: 把 Drafts 里的草稿真发出去.

    红线: AI 永不应该直接调这个 — 必须是 Companion compose panel 里
    员工**人工点 "发送" 按钮 + 两步 confirm** 之后才调.

    跟 _cmd_delete 同 id 路由 + NotSupported 友好兜底.
    """
    from .adapters.base import NotSupportedError  # noqa: PLC0415

    msg_id = args.id

    if not msg_id or msg_id.lower() in {"null", "undefined", "none"}:
        _err(
            f"草稿 id 不能为空 (收到 {msg_id!r}). "
            "用 `catfish-email draft ... --json | jq -r '.draft_id'` 拿真 id."
        )
        return 2

    target_adapter: EmailAdapter | None = None
    if "|" in msg_id:
        prefix = msg_id.split("|", 1)[0]
        for a in adapters:
            if a.name == prefix or a.name.replace("_", "-") == prefix:
                target_adapter = a
                break

    candidates = [target_adapter] if target_adapter is not None else list(adapters)
    last_err: Exception | None = None
    last_value_err: ValueError | None = None
    last_not_supported: NotSupportedError | None = None
    for a in candidates:
        try:
            a.send_message(msg_id)
            if args.json:
                print(json.dumps(
                    {"adapter": a.name, "id": msg_id, "sent": True, "ok": True},
                    ensure_ascii=False,
                ))
            else:
                print(f"✓ [{a.name}] 已发送: {msg_id}")
            return 0
        except DataNotFoundError as e:
            last_err = e
            continue
        except ValueError as e:
            last_value_err = e
            continue
        except NotSupportedError as e:
            last_not_supported = e
            continue
        except EmailAdapterError as e:
            _err(f"[{a.name}] 发送失败: {e}")
            return 1

    if last_value_err is not None and last_err is None and last_not_supported is None:
        _err(
            f"草稿 id 格式不对: {last_value_err}. "
            "用 `catfish-email list --json` 看 Drafts 文件夹拷 id."
        )
        return 2
    if last_not_supported is not None and last_err is None:
        _err(
            f"草稿所在的客户端不支持自动发送: {last_not_supported}. "
            "请去客户端 (Foxmail / 等) 自己发."
        )
        return 4
    if target_adapter is not None:
        _err(f"[{target_adapter.name}] 草稿不存在: {last_err}")
    else:
        _err(f"草稿不存在 (跨 {len(candidates)} 个客户端都没找到): {last_err}")
    return 3


def _cmd_search(adapters: list[EmailAdapter], args) -> int:
    """5/18 BL-EMAIL-MULTI-CLIENT: 跨所有 adapter 搜, 合并 + 按 date 排."""
    hits: list[tuple[str, Any]] = []  # 5/18 BL-EMAIL-LIST-ADAPTER-FIELD: 同 _cmd_list
    errors: list[str] = []
    for adapter in adapters:
        try:
            hits.extend((adapter.name, m) for m in adapter.search(
                args.query,
                account=args.account,
                folder=args.folder,
                limit=args.limit,
            ))
        except EmailAdapterError as e:
            errors.append(f"[{adapter.name}] 搜索失败: {e}")
            continue
        except (FileNotFoundError, OSError, sqlite3.Error) as e:  # noqa: PERF203
            # 5/18 BL-EMAIL-ACCOUNT-CROSS-ADAPTER-CRASH 同 _cmd_list 兜底
            errors.append(f"[{adapter.name}] 搜索失败 ({type(e).__name__}): {e}")
            continue

    hits.sort(key=lambda am: am[1].date or "", reverse=True)
    hits = hits[: args.limit]

    for err in errors:
        print(f"⚠ {err}", file=sys.stderr)

    if args.json:
        print(json.dumps(
            [_msg_to_dict(m, adapter_name=a) for (a, m) in hits],
            ensure_ascii=False, indent=2,
        ))
    else:
        if not hits:
            print(f"(没找到 '{args.query}')")
            return 0
        print(f"# 搜索 '{args.query}', 找到 {len(hits)} 封 (跨 {len(adapters)} 个客户端)")
        print()
        for (a, m) in hits:
            state = "○" if m.is_read else "●"
            print(f"- {state} [{a}] [{m.date[:16]}] {m.subject}  ← {m.sender}")
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


def _msg_to_dict(m, adapter_name: str | None = None) -> dict[str, Any]:
    """Message dataclass → dict, attachments 也展开.

    5/18 BL-EMAIL-LIST-ADAPTER-FIELD: 可选 adapter_name 注入到 dict 里 (放最前面),
    让 `jq group_by(.adapter)` / 调试 / 跨 adapter 联调能区分这条来自 Mail.app
    还是 Foxmail. 老调用方不传 adapter_name 时不带 key (向后兼容).
    """
    d: dict[str, Any] = {}
    if adapter_name is not None:
        d["adapter"] = adapter_name
    d.update(asdict(m))
    return d


def _err(msg: str) -> None:
    print(f"catfish-email: {msg}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
