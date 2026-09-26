"""Adapter 工厂 —— 平台 + 客户端检测, 返回合适的 EmailAdapter。

调用方 (CLI / SKILL helper) 不直接 import adapter, 一律走这里:
    adapter = get_adapter()                  # 自动选当前平台默认
    adapter = get_adapter("foxmail-mac")     # 指定具体客户端
    adapter = get_adapter("apple-mail")      # macOS 原生 Mail.app

5/18 BL-EMAIL-APPLEMAIL: 加 apple-mail (替原 outlook-mac), macOS 默认改成
['apple-mail', 'foxmail-mac'] 候选顺序. 理由: Mail.app 100% 装机, 优先尝试.
"""
from __future__ import annotations

import logging
import os
import platform

from .adapters.base import ClientNotRunningError, DataNotFoundError, EmailAdapter

logger = logging.getLogger("catfish_email.inbox")


def _imap_configured() -> bool:
    """IMAP 配好了没 —— 跟 ImapAdapter 用同一个判据 (config_from_env)。

    9/26: 原来只看三个环境变量。Windows 上 tool-bridge 起的 CLI 没有这些变量,
    但 IMAP 其实配好了 (凭据在凭据管理器里), 判据不一致就等于"没配"。
    """
    from .adapters.imap_config import config_from_env  # noqa: PLC0415

    return config_from_env() is not None


def _eml_dir_configured() -> bool:
    """用户是不是已经指定了邮件目录 (新键名或老键名)。

    老键名 ``CATFISH_FOXMAIL_ROOT`` 以前指 Foxmail 的 Storage 目录; 9/18 之后
    那条线删了 (7.2 把邮件加密了, 读它的私有存储没意义), 语义变成"邮件目录"。
    已经配过的机器升级后不该突然找不到邮件, 所以两个都认。
    """
    from .adapters.eml_dir import LEGACY_ROOT_ENV, ROOT_ENV  # noqa: PLC0415

    return any(os.environ.get(var, "").strip() for var in (ROOT_ENV, LEGACY_ROOT_ENV))


def _windows_candidates() -> list[str]:
    """Windows 的取数来源。9/26 鸿波「理论上 Windows 下不再使用客户端了吧?」—— 对。

    9/18 做 IMAP 就是因为 Windows 上两条客户端路都走不通 (ccc2ecb): Foxmail 7.2
    邮件文件加密, 新版 Outlook 没有 COM、本地也没有邮件。同一天 d7833dc 为了 macOS
    (Apple Mail 能删能标已读, 不该让只读的 IMAP 抢先) 把 IMAP 挪到候选最后, 这个
    改动顺手也套在了 Windows 上, 于是 Windows 又变成先问 Outlook —— 跟挪动时写下
    的理由自相矛盾。结果: Outlook 装了没配账号的机器上, 读信先吃一个 COM 错误。

    现在: 配了 IMAP 就以 IMAP 为准, 不再碰 Outlook。员工在界面上明确选过的 .eml
    导出目录 (多半是迁移过来的旧邮件) 仍然保留, 排在 IMAP 后面。没配 IMAP 的老
    机器保持原来的自动探测, 不让它们升级后突然一封信都没有。
    """
    if _imap_configured():
        return ["imap"] + (["eml-dir"] if _eml_dir_configured() else [])
    return ["eml-dir"] if _eml_dir_configured() else ["outlook-win", "eml-dir"]


def get_adapter(client: str | None = None) -> EmailAdapter:
    """工厂: 返回合适的 adapter。

    Args:
        client: 显式指定 'apple-mail' / 'foxmail-mac' / 'outlook-win' / 'eml-dir' / 'imap'
                None 则按平台自动挑 (Mac → 优先 Apple Mail, 没装就 Foxmail; Win 同理)

    Raises:
        DataNotFoundError: 当前平台一个能用的 adapter 都没找到
        ValueError: 显式指定的 client 名字非法
    """
    if client is not None:
        return _get_adapter_explicit(client)

    # 自动选: 按平台 + 是否已装客户端的优先级
    # 5/18 BL-EMAIL-APPLEMAIL: macOS 默认 Apple Mail.app 优先 (100% 装机), Foxmail 兜底
    system = platform.system()
    candidates: list[str]
    # 配了 IMAP 就把它加进候选, 但**排在本地客户端后面**。
    #
    # 9/18 最初写的是排第一, 理由"它是唯一不看客户端脸色的路径"。那个理由
    # 在 Windows 上成立 (新版 Outlook 无 COM、Foxmail 加密), 在 macOS 上不
    # 成立 —— Apple Mail 好好的, 而且它能删能标已读, IMAP 只能看。让只读的
    # 来源赢是拿功能换了个没必要的"独立性"。
    #
    # 本地客户端取不到时它们会抛异常, 自然落到 IMAP, 所以排最后不影响兜底。
    # (以上只说 macOS。Windows 配了 IMAP 就只用 IMAP, 见 _windows_candidates。)
    imap_last = ["imap"] if _imap_configured() else []
    if system == "Darwin":
        candidates = ["apple-mail", "foxmail-mac"] + imap_last
    elif system == "Windows":
        candidates = _windows_candidates()
    else:
        raise DataNotFoundError(
            f"catfish-email 暂不支持 {system} 平台 (仅 macOS / Windows)"
        )

    last_err: Exception | None = None
    for c in candidates:
        try:
            return _get_adapter_explicit(c)
        except (DataNotFoundError, ImportError, NotImplementedError) as e:
            last_err = e
            logger.debug("adapter %s 不可用: %s", c, e)
            continue

    raise DataNotFoundError(
        f"{system} 上没找到可用的邮件来源 (尝试过: {', '.join(candidates)})。"
        f"Windows 上请在邮件页配置 IMAP (推荐), 或选择 .eml 导出目录。"
        f"最后一个错: {last_err}"
    )


def get_all_adapters() -> list[EmailAdapter]:
    """5/18 BL-EMAIL-MULTI-CLIENT: 返当前平台 *所有* 能用的 adapter, 不短路.

    跟 get_adapter() 的差别:
        - get_adapter(): 找第一个能用的就返 (默认行为, Apple Mail 永远赢)
        - get_all_adapters(): 全跑一遍, 返所有能用的 list

    用途: CLI `_cmd_list` 不传 --client 时跨客户端合并查邮件. 跟立项前提一致 ——
    员工同时用 Mail.app (iCloud/Gmail) + Foxmail (公司企业邮箱) 是常见组合.

    Returns:
        所有能初始化的 adapter list (按平台候选顺序). 全挂返空 list (caller 自决怎么报).
    """
    system = platform.system()
    # macOS 上 IMAP 排最后, 理由同 get_adapter(); Windows 见 _windows_candidates()。这里是合并列表, 顺序只影响去重时的
    # 先后, 而去重本身已经按"能不能执行动作"定优先级 (cli_read._source_priority)。
    imap_last = ["imap"] if _imap_configured() else []
    if system == "Darwin":
        candidates = ["apple-mail", "foxmail-mac"] + imap_last
    elif system == "Windows":
        candidates = _windows_candidates()
    else:
        return []

    adapters: list[EmailAdapter] = []
    for c in candidates:
        try:
            adapters.append(_get_adapter_explicit(c))
        except (DataNotFoundError, ImportError, NotImplementedError, ClientNotRunningError) as e:
            logger.debug("adapter %s 不可用 (skip): %s", c, e)
            continue
    return adapters


def _get_adapter_explicit(client: str) -> EmailAdapter:
    """按客户端名字 dispatch. 每个分支 lazy import, 减少不需要的依赖。"""
    if client == "apple-mail":
        # 5/18 BL-EMAIL-APPLEMAIL: macOS Mail.app AppleScript adapter
        #
        # 8/8: 先探一下这台机器到底用不用 Apple Mail。
        #
        # 原来这里直接 `return AppleMailAdapter()` —— 构造不查任何东西, 于是
        # `get_all_adapters()` 永远把它算进候选。在一台从来没配过 Mail.app 的机器上
        # (`~/Library/Mail/` 下连 V* 目录都没有), 结果是每次列邮件 / 每个账号 /
        # 每次刷新都多一条注定失败的记录, 而 Foxmail 那条路其实一直好好的。
        # 员工看到的就是"邮件页一堆错误", 且被引导去开一个他根本没在用的客户端。
        #
        # 抛 DataNotFoundError 是刻意选的: get_adapter() 和 get_all_adapters() 都
        # 已经 catch 它并 continue, 所以自动候选那条路无需改动就会跳过;
        # 而显式 `--client apple-mail` 会拿到这条异常, 带着说清楚原因的文案 ——
        # 想排查的人仍然问得出"为什么跳过它"。
        # 探测本身**绝不许**把邮件整个搞挂。8/8 第一版就是这么翻的车:
        # apple_mail_available() 里的 iterdir() 抛 PermissionError (Companion
        # 拉起的进程没有完全磁盘访问权限), 而下面两个 caller 的 except 不含
        # OSError, 异常冒穿整个 CLI —— 邮件页从"有噪音"变成"拉取失败 + traceback"。
        #
        # 根因已在 _detect_mail_data_dir 里堵掉 (读不到返 None 不抛), 这里再兜一层:
        # 一个**用来减少噪音**的优化, 不该有任何机会变成致命错误。出意外时按
        # "可用"走 —— 退回 8/8 之前的行为 (有噪音但能收信), 而不是什么都收不到。
        from .adapters.apple_mail_probe import apple_mail_available, unavailable_reason
        try:
            available = apple_mail_available()
        except Exception as e:  # noqa: BLE001
            logger.warning("apple-mail 可用性探测出错, 按可用处理 (退回老行为): %s", e)
            available = True
        if not available:
            raise DataNotFoundError(f"apple-mail 不可用: {unavailable_reason()}")
        from .adapters.apple_mail import AppleMailAdapter
        return AppleMailAdapter()
    if client == "foxmail-mac":
        from .adapters.foxmail_mac import FoxmailMacAdapter
        return FoxmailMacAdapter()
    if client == "outlook-mac":
        # 5/17 BL-EMAIL-APPLEMAIL: outlook-mac 改 apple-mail. 这里留兼容 alias.
        from .adapters.apple_mail import AppleMailAdapter
        logger.warning(
            "client='outlook-mac' deprecated, 5/18 起 macOS 改 Apple Mail. "
            "用 'apple-mail' 显式指定."
        )
        return AppleMailAdapter()
    if client == "outlook-win":
        # W2 BL-EMAIL-OUTLOOK-WIN (7/11): outlook_win.py 骨架完成. 非 Win 平台
        # __init__ 里 _import_pywin32 抛 NotSupportedError (继承 NotImplementedError),
        # inbox.py 会自动 fallback 到 eml-dir 候选.
        from .adapters.outlook_win import OutlookWinAdapter
        return OutlookWinAdapter()
    if client == "imap":
        # 9/18: 唯一不依赖邮件客户端的路径。两条本地路都被厂商堵死了 ——
        # Foxmail 7.2 加密邮件文件, 新版 Outlook 既无 COM 也无本地数据。
        #
        # 用带索引的那个: 列清单是热路径 (后台每隔几分钟就要问一次), 走索引
        # 之后稳定期一次对账只剩一个 FETCH FLAGS 往返。读单封仍旧直连服务器
        # (继承自基类) —— 正文不进索引, 索引里只有列表要用的那些字段。
        from .adapters.imap_sync import ImapSyncAdapter  # noqa: PLC0415
        return ImapSyncAdapter()
    if client in ("eml-dir", "foxmail-win"):
        # 9/18: foxmail-win 这条线删了 —— Foxmail 7.2 把邮件文件加密了,
        # 本地解不出正文, 读它的私有存储没有意义。改成读客户端导出的 .eml。
        # 老名字留作 alias, 免得升级后已有配置直接报"未知 client"。
        if client == "foxmail-win":
            logger.warning("client='foxmail-win' 已废弃, 改用 'eml-dir' (读导出的 .eml 目录)")
        from .adapters.eml_dir import EmlDirAdapter
        return EmlDirAdapter()
    raise ValueError(
        f"未知 client: {client!r} "
        f"(合法: apple-mail / foxmail-mac / outlook-win / eml-dir / imap)",
    )
