"""只读查询类子命令 —— accounts / list / read / search.

# 为什么按这条线拆

这 4 个正好对应 adapters/base.py 里的 **4 个 abstract method**
(list_accounts / list_messages / read_message / search) —— 每个 adapter 都
必须实现, 所以这 4 条命令在任何客户端上都能用, 谁也不会抛 NotSupportedError。

另外 6 条 (draft / send / delete / mark-read / check / attachment) 对应 6 个
**可选 method**, 随时可能 NotSupportedError, 每一条都得写降级路径 —— 那是
另一类活, 放 cli_action.py。

这不是我新定的分类: adapters/base.py 和 outlook_win.py 的注释本来就按
"4 个必须 / 6 个可选" 在说话, 这里只是让文件结构跟上。

函数体从 __main__.py 原样搬过来, 一个字节没改。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import asdict
from typing import Any

from .adapters.base import (
    DataNotFoundError,
    EmailAdapter,
    EmailAdapterError,
    ListFilter,
)
from .cli_output import _err, _msg_to_dict


def _message_dedupe_key(adapter_name: str, message: Any) -> tuple[str, ...]:
    """Return the safest identity available for one aggregated message.

    ``Message.id`` is only stable inside an adapter.  Apple Mail can expose
    the same RFC message once through AppleScript and once through its EMLX
    index, so the adapter id is deliberately not the primary key here.
    Subject/date are not safe identities: different messages can share both.
    """
    message_id = (message.message_id or "").strip()
    if message_id:
        return ("rfc822", message.folder, message_id.casefold())
    return ("adapter", adapter_name, message.folder, message.id)


def _source_priority(adapter_name: str, message: Any) -> int:
    """Prefer a live-client id over a local-cache id when records collide.

    The EMLX record is still a valid fallback and remains usable when it is
    the only record.  When both representations exist, the AppleScript id
    keeps the normal account label and the existing Mail.app action route.
    """
    del adapter_name  # reserved for future adapter-specific priorities
    return 0 if "|emlx:" in message.id else 1


def _dedupe_messages(messages: list[tuple[str, Any]]) -> list[tuple[str, Any]]:
    """Deduplicate messages after all adapters/accounts have been queried.

    Keep input order for unique messages and replace a duplicate only when a
    higher-quality source is available.  This makes the result deterministic
    while preserving the adapter/id pair needed by ``read`` and actions.
    """
    result: list[tuple[str, Any]] = []
    positions: dict[tuple[str, ...], int] = {}
    for adapter_name, message in messages:
        key = _message_dedupe_key(adapter_name, message)
        existing_position = positions.get(key)
        if existing_position is None:
            positions[key] = len(result)
            result.append((adapter_name, message))
            continue
        current_adapter, current_message = result[existing_position]
        if _source_priority(adapter_name, message) > _source_priority(
            current_adapter, current_message
        ):
            result[existing_position] = (adapter_name, message)
    return result


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

    # 同一 RFC 邮件可能同时来自 AppleScript 和 EMLX 索引；先去重，再排序
    # 和 trim，否则同一封邮件会占用两个列表位置。
    msgs = _dedupe_messages(msgs)

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

    hits = _dedupe_messages(hits)
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
