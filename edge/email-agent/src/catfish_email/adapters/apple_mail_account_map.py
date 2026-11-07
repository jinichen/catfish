"""Apple Mail 账号编号 → 账号名 / 邮箱 (11/07)。

macOS 14+ 的 ~/Library/Mail/V10 下, 每个账号的目录名是一串编号 (= Mail 里 account 的 id), 不是邮箱地址。
Mail 开着时走 AppleScript, 账号名、邮箱都拿得到; Mail 没开 (或没给自动化权限) 时走 EMLX 只读兜底,
只看得到目录名 —— 结果界面上账号一栏显示成「36984457-CE3B-…」, 更要紧的是「本单位邮箱域名」也拿不到
(鸿波 11/07: 信息安全中心 <ff_nic@chinatelecom.cn> 的安全预警被标成钓鱼, 因为那一轮本单位域名是空的)。

做法: AppleScript 能用的时候, 顺手把 {编号: {name, address}} 存到 ~/.catfish/apple_mail_accounts.json
(一小时最多问一次 Mail)。EMLX 兜底时用这张表把编号换回账号名和邮箱。没有表 (从没在 Mail 开着时跑过) 就照旧。
只存账号名和邮箱地址, 不存密码、不存服务器设置。
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger("catfish_email.adapters.apple_mail")

REFRESH_EVERY = 3600

AS_ACCOUNT_IDS = """
tell application "Mail"
    set FS to (character id 31)
    set RS to (character id 30)
    set out to ""
    repeat with acc in every account
        set addr to ""
        try
            set addrList to email addresses of acc
            if (count of addrList) > 0 then set addr to (item 1 of addrList) as string
        end try
        set out to out & ((id of acc) as string) & FS & ((name of acc) as string) & FS & addr & RS
    end repeat
    return out
end tell
"""


def map_path() -> Path:
    env = os.environ.get("CATFISH_HOME", "").strip()
    return (Path(env) if env else Path.home() / ".catfish") / "apple_mail_accounts.json"


def load() -> dict[str, dict[str, str]]:
    try:
        data = json.loads(map_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    accounts = data.get("accounts") if isinstance(data, dict) else None
    if not isinstance(accounts, dict):
        return {}
    return {str(k): {"name": str(v.get("name") or ""), "address": str(v.get("address") or "")}
            for k, v in accounts.items() if isinstance(v, dict)}


def save(accounts: dict[str, dict[str, str]]) -> None:
    p = map_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"saved_at": time.time(), "accounts": accounts}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    except OSError as e:
        logger.debug("存 Mail 账号编号表失败: %s", e)


def parse(out: str) -> dict[str, dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    for rec in out.split("\x1e"):
        parts = rec.strip("\r\n").split("\x1f")
        if len(parts) >= 3 and parts[0].strip():
            found[parts[0].strip()] = {"name": parts[1].strip(), "address": parts[2].strip()}
    return found


def refresh(run_osascript) -> None:
    """Mail 能用时调: 表过期了 (或没有) 就问一次 Mail 存下来。出错不影响调用方。"""
    p = map_path()
    try:
        if p.exists() and time.time() - p.stat().st_mtime < REFRESH_EVERY:
            return
    except OSError:
        pass
    try:
        found = parse(run_osascript(AS_ACCOUNT_IDS))
    except Exception as e:  # noqa: BLE001 — 只是锦上添花
        logger.debug("问 Mail 账号编号失败: %s", e)
        return
    if found:
        save(found)


def resolve(dir_name: str, table: dict[str, dict[str, str]] | None = None) -> dict[str, str] | None:
    """目录名 (编号) → {name, address}; 不认识 = None。"""
    return (table if table is not None else load()).get(dir_name)
