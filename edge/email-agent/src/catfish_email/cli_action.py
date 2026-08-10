"""有副作用的子命令 —— draft / send / delete / mark-read / check / attachment.

# 跟 cli_read.py 的分界

这 6 个对应 adapters/base.py 的 **6 个可选 method**: 基类默认实现就是
raise NotSupportedError, adapter 想支持才 override。所以每一条命令都必须
写"这个客户端不支持"的降级路径 —— 只有 Apple Mail 六个全实现, Foxmail Mac
只有 mark_read, Outlook Windows 一个都还没有。

共同点也在这里: 这 6 条都要处理 NotSupportedError, 都要按 id 前缀路由到
正确的 adapter, 失败时都要给员工一句"去客户端自己做"。cli_read.py 那 4 条
一条都不需要。

红线保留在原处 (send 只能由 Companion 两步 confirm 触发; delete 只软删到
Trash; draft 落 Drafts 不发送) —— 见各函数 docstring。

函数体从 __main__.py 原样搬过来, 一个字节没改 (含函数内的局部 import ——
刻意不动, 拆分只改位置不改行为)。
"""
from __future__ import annotations

import json

from .adapters.base import (
    DataNotFoundError,
    EmailAdapter,
    EmailAdapterError,
)
from .cli_output import _err


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
        # 这条文案原来写死成 "Apple Mail.app 支持; Foxmail Mac 不支持" —— 两个
        # 都是 macOS 客户端, 而这条分支在 Windows 上同样会走到 (那边只有
        # outlook_win, 它不支持起草), 员工会看到一句跟自己机器无关的话。
        # 而且它跟 supports_drafts 是同一类东西: 把某个时刻的能力抄进字符串,
        # 能力变了它不会跟着变。所以只说**当下可验证**的事 —— 哪些 adapter
        # 在场、它们都不支持 —— 别去枚举别的平台的能力。
        names = ", ".join(a.name for a in adapters) or "一个都没有"
        _err(
            f"当前可用的邮件客户端都不支持起草到草稿箱 (可用: {names})。"
            f"请在客户端里自己新建邮件, 正文可以从 catfish 复制粘贴。",
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


def _cmd_check(adapters: list[EmailAdapter], args) -> int:
    """P3.5.204.c (7/9 鸿波 catch "客户端还没同步的邮件, 在鲶鱼里无法激活客户端去同步"):
    触发客户端立即从服务器 fetch 新邮件.

    account=None → 全部账号一起同步; 指定 → 只同步该账号.
    每个 adapter try, 支持的执行, 不支持的 NotSupportedError 收集. 只要有一个成功
    就返 0 (员工能理解: 至少一个客户端拉了一遍); 全失败返非 0.
    """
    from .adapters.base import NotSupportedError, ClientNotRunningError  # noqa: PLC0415

    ok_names: list[str] = []
    unsupported_names: list[str] = []
    errs: list[tuple[str, str]] = []
    for a in adapters:
        try:
            a.check_new_mail(account=args.account)
            ok_names.append(a.name)
        except NotSupportedError:
            unsupported_names.append(a.name)
        except ClientNotRunningError as e:
            errs.append((a.name, f"客户端没在跑: {e}"))
        except Exception as e:  # noqa: BLE001
            errs.append((a.name, str(e)))

    if args.json:
        print(json.dumps(
            {
                "ok": len(ok_names) > 0,
                "triggered": ok_names,
                "unsupported": unsupported_names,
                "errors": [{"adapter": n, "msg": m} for n, m in errs],
                "account": args.account or "(all)",
            },
            ensure_ascii=False,
        ))
    else:
        if ok_names:
            print(f"✓ 已触发同步 ({', '.join(ok_names)}). 等 3-10 秒等客户端拉完.")
        if unsupported_names:
            print(f"⚠ {', '.join(unsupported_names)} 不支持触发同步 (需手动 refresh)")
        for name, msg in errs:
            print(f"✗ [{name}] {msg}")

    return 0 if ok_names else 4


def _cmd_attachment(adapters: list[EmailAdapter], args) -> int:
    """P3.5.103 (6/24 鸿波 catch '附件不能点'): 导出附件到本地 tmp 文件.

    跟 _cmd_read 同 id 路由 + 友好兜底. JSON 输出 {"path": "/tmp/.../报告.pdf"}.
    Companion 拿到 path 调 open_file Tauri command 系统默认 app 打开.
    """
    from .adapters.base import NotSupportedError  # noqa: PLC0415

    msg_id = args.id

    if not msg_id or msg_id.lower() in {"null", "undefined", "none"}:
        _err(f"邮件 id 不能为空 (收到 {msg_id!r})")
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
            path = a.export_attachment(msg_id, args.filename)
            print(json.dumps(
                {"adapter": a.name, "id": msg_id, "filename": args.filename,
                 "path": str(path), "ok": True},
                ensure_ascii=False,
            ))
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
            _err(f"[{a.name}] 导出附件失败: {e}")
            return 1

    if last_value_err is not None and last_err is None and last_not_supported is None:
        _err(f"邮件 id 格式不对: {last_value_err}")
        return 2
    if last_not_supported is not None and last_err is None:
        _err(
            f"邮件所在的客户端不支持导出附件: {last_not_supported}. "
            "请去客户端 (Foxmail / Outlook / 等) 自己下载."
        )
        return 4
    if target_adapter is not None:
        _err(f"[{target_adapter.name}] 邮件 / 附件不存在: {last_err}")
    else:
        _err(f"邮件 / 附件不存在 (跨 {len(candidates)} 客户端都没找到): {last_err}")
    return 3
