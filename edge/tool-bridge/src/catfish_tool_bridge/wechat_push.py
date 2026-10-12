"""提醒同步到微信 (10/12): Windows 闹钟到点时, 除了系统通知再推一条到员工绑定的微信。

Windows 的提醒在 Catfish 自己的任务库里 (见 local_pim.py), 手机上看不到; 员工本机的
hermes 已经是他自己的微信机器人 (腾讯 iLink Bot, gateway/platforms/weixin.py), 所以
直接借这条通道: 本机 → iLink → 员工手机微信, 不经过中央端。

- 员工自己开 (~/.catfish/notify_prefs.json 里 wechat_reminders=true), 默认关。
- 只发提醒标题 + 时间, 不发正文 —— 标题会进员工的个人微信 (腾讯服务器)。
- 微信机器人的配置在 hermes 的 .env 里, 只有 hermes 进程会加载; tool-bridge 拿不到
  环境变量, 所以这里自己读 .env 再显式传给 send_weixin_direct。
- iLink 要求每条外发带上该用户最近一条消息里的 context_token; 员工很久没跟机器人说话
  可能被拒 (会话过期)。失败只记日志 + 写 notify_status.json 给界面提示, 系统通知照旧。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from .hermes_paths import hermes_home

logger = logging.getLogger(__name__)

PREFS_KEY = "wechat_reminders"


def _catfish_dir() -> Path:
    return Path.home() / ".catfish"


def prefs_path() -> Path:
    return _catfish_dir() / "notify_prefs.json"


def status_path() -> Path:
    return _catfish_dir() / "notify_status.json"


def enabled() -> bool:
    try:
        return bool(json.loads(prefs_path().read_text(encoding="utf-8")).get(PREFS_KEY))
    except (OSError, ValueError, AttributeError):
        return False


def read_hermes_env(env_file: Path | None = None) -> dict[str, str]:
    """hermes .env 里的 WEIXIN_* (KEY=VALUE, 允许引号 / export 前缀 / 注释)。"""
    path = env_file or hermes_home() / ".env"
    out: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        if not key.startswith("WEIXIN_"):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out


def target(env: dict[str, str] | None = None) -> dict[str, str] | None:
    """推送目标: 机器人凭据 + 员工绑定的微信 (home channel)。没绑定返回 None。"""
    e = read_hermes_env() if env is None else env
    token, account, chat = e.get("WEIXIN_TOKEN", ""), e.get("WEIXIN_ACCOUNT_ID", ""), e.get("WEIXIN_HOME_CHANNEL", "")
    if not (token and account and chat):
        return None
    return {"token": token, "account_id": account, "chat_id": chat, "base_url": e.get("WEIXIN_BASE_URL", "")}


def _write_status(ok: bool, error: str | None) -> None:
    try:
        status_path().parent.mkdir(parents=True, exist_ok=True)
        status_path().write_text(
            json.dumps({"last_at": time.time(), "ok": ok, "error": error}, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        logger.debug("写 notify_status.json 失败", exc_info=True)


def last_status() -> dict[str, Any]:
    try:
        return json.loads(status_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _friendly(err: str) -> str:
    # iLink 会话过期: errcode/ret = -14, 或 -2 "unknown error" (weixin.py _is_stale_session_ret)。
    # 10/12 实测: 员工 7 天没跟机器人说话, 主动推送被 iLink 拒成 ret=-2, hermes 报成
    # "rate limited; cooldown" —— 说一句话就恢复, 所以同样按会话过期给提示。
    low = err.lower()
    if re.search(r"(errcode|ret)\D{0,3}-(14|2)\b", err) or "expired" in low or "rate limited" in low:
        return "微信会话过期了: 在微信里给小鲶发一句话, 之后的提醒就能推过来"
    return err[:200]


async def _send(tgt: dict[str, str], text: str) -> dict[str, Any]:
    from gateway.platforms.weixin import check_weixin_requirements, send_weixin_direct  # noqa: PLC0415 —— hermes venv 里才有

    if not check_weixin_requirements():
        return {"error": "缺少微信通道依赖 (aiohttp / cryptography)"}
    extra = {"account_id": tgt["account_id"], "base_url": tgt["base_url"]}
    return await send_weixin_direct(extra=extra, token=tgt["token"], chat_id=tgt["chat_id"], message=text)


def push(text: str, tgt: dict[str, str] | None = None) -> bool:
    """推一条到员工微信。在 to_thread 里被调 (local_pim.alarm_loop), 所以可以 asyncio.run。"""
    tgt = tgt or target()
    if tgt is None:
        _write_status(False, "还没绑定微信 (先在「微信账号绑定」里扫码连上小鲶)")
        return False
    try:
        result = asyncio.run(_send(tgt, text))
    except ImportError:
        result = {"error": "当前环境里没有 hermes 的微信通道"}
    except Exception as e:  # noqa: BLE001 —— 网络 / 协议异常都只影响这一条推送
        result = {"error": f"{type(e).__name__}: {e}"}
    if result.get("success"):
        _write_status(True, None)
        return True
    err = str(result.get("error") or "未知错误")
    logger.warning("提醒推送到微信失败: %s", err)
    _write_status(False, _friendly(err))
    return False


def push_reminder(title: str, line: str) -> bool:
    """闹钟到点时调: 开了才推。"""
    if not enabled():
        return False
    # 一行发: hermes 的微信适配器会把多行拆成几个气泡 (10/12 真机测试看到的)
    return push(f"{title}: {line}")
