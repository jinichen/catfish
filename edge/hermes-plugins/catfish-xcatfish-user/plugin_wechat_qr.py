"""P30 · 微信扫码登录的三个 endpoint + 会话清理 + .env 同步。

8/8 从 plugin.py 拆出来 —— 那个文件 4898 行, 军规红线是 800。

## 为什么先拆这一块

它跟 plugin.py 其余部分**只有 logger 一处耦合**: 区块里出现的
`_PATCH_TARGETS` / `_APPROVE_ALIASES` 都只在注释里, 不是真引用。拆一个
零代码依赖的块, 风险最低, 也最容易验证拆对了。

## logger 名字故意不变

仍然是 `catfish.xcatfish_user.plugin` —— 拆文件不该改日志的样子。现场排查
靠 grep 这个前缀, 换成新名字等于把已有的排查手法废掉一半。

## 谁在用

`plugin.py` 顶部按军规的 re-export 协议把这里的符号原样吐回去, 所以老代码
`from plugin import _WECHAT_QR_SESSION_TTL` 照旧能拿到。
"""

import logging

logger = logging.getLogger("catfish.xcatfish_user.plugin")

_WECHAT_QR_SESSION_TTL = 600.0  # 10 min. 二维码本身 35s 过期, 给 UI 留缓冲.


def _wechat_qr_sweep_expired() -> None:
    """清掉超过 TTL 的 in-memory session. start 时 call 一次."""
    import time as _t
    now = _t.time()
    expired = [k for k, v in _wechat_qr_sessions.items()
               if (now - v.get("created_at", 0.0)) > _WECHAT_QR_SESSION_TTL]
    for k in expired:
        _wechat_qr_sessions.pop(k, None)


async def _handle_wechat_qr_start(self, request):
    """POST /api/platforms/wechat/qr_login/start

    Body: none
    Response: {qrcode, qrcode_url, scan_data}  |  502 (ilink 挂)
    """
    import time as _t
    auth_err = self._check_auth(request)
    if auth_err:
        return auth_err
    try:
        import aiohttp  # noqa: PLC0415
        from gateway.platforms.weixin import (  # noqa: PLC0415
            ILINK_BASE_URL, EP_GET_BOT_QR, QR_TIMEOUT_MS,
            _api_get, _make_ssl_connector,
        )
    except ImportError as e:
        return web.json_response(
            {"error": f"hermes weixin 模块 import 失败: {e}"}, status=500,
        )

    _wechat_qr_sweep_expired()

    try:
        async with aiohttp.ClientSession(
            trust_env=True, connector=_make_ssl_connector(),
        ) as http_sess:
            qr_resp = await _api_get(
                http_sess,
                base_url=ILINK_BASE_URL,
                endpoint=f"{EP_GET_BOT_QR}?bot_type=3",
                timeout_ms=QR_TIMEOUT_MS,
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("P30 wechat qr start: ilink get_bot_qrcode 失败: %s", e)
        return web.json_response(
            {"error": f"ilink 获取二维码失败: {e}"}, status=502,
        )

    qrcode_value = str(qr_resp.get("qrcode") or "")
    qrcode_url = str(qr_resp.get("qrcode_img_content") or "")
    if not qrcode_value:
        return web.json_response(
            {"error": "ilink 返回未含 qrcode 字段"}, status=502,
        )

    _wechat_qr_sessions[qrcode_value] = {
        "base_url": ILINK_BASE_URL,
        "created_at": _t.time(),
    }
    scan_data = qrcode_url if qrcode_url else qrcode_value
    return web.json_response({
        "qrcode": qrcode_value,
        "qrcode_url": qrcode_url,
        "scan_data": scan_data,
    })


async def _handle_wechat_qr_poll(self, request):
    """GET /api/platforms/wechat/qr_login/poll?qrcode=<hex>

    Response:
      {status: "wait" | "scaned" | "confirmed" | "expired",
       account_id?, user_id?, _warning?}
      404 (session not found — hermes 重启; UI 会自己重跑 start)

    scaned_but_redirect 内部升 base_url 后返给前端 "scaned" (前端语义只有
    wait/scaned/confirmed/expired 4 档).
    """
    auth_err = self._check_auth(request)
    if auth_err:
        return auth_err
    qrcode_value = request.query.get("qrcode", "")
    if not qrcode_value:
        return web.json_response(
            {"error": "缺 qrcode query 参数"}, status=400,
        )
    sess_state = _wechat_qr_sessions.get(qrcode_value)
    if sess_state is None:
        return web.json_response(
            {"error": "session not found (可能 hermes 重启, 请重新扫码)"},
            status=404,
        )

    try:
        import aiohttp  # noqa: PLC0415
        from gateway.platforms.weixin import (  # noqa: PLC0415
            EP_GET_QR_STATUS, QR_TIMEOUT_MS,
            _api_get, _make_ssl_connector,
            save_weixin_account,
        )
        from hermes_constants import get_hermes_home  # noqa: PLC0415
    except ImportError as e:
        return web.json_response(
            {"error": f"hermes weixin 模块 import 失败: {e}"}, status=500,
        )

    base_url = sess_state.get("base_url") or ""
    try:
        async with aiohttp.ClientSession(
            trust_env=True, connector=_make_ssl_connector(),
        ) as http_sess:
            status_resp = await _api_get(
                http_sess,
                base_url=base_url,
                endpoint=f"{EP_GET_QR_STATUS}?qrcode={qrcode_value}",
                timeout_ms=QR_TIMEOUT_MS,
            )
    except Exception as e:  # noqa: BLE001
        # 前端 wechat_qr.ts:22 契约: poll 502/_warning 字段 → 临时网络抖动
        # UI 不动让它下次再 poll (等到 wait/expired). 这里 200 + _warning.
        logger.info("P30 wechat qr poll: ilink get_qrcode_status 抖动: %s", e)
        return web.json_response({"status": "wait", "_warning": str(e)})

    status = str(status_resp.get("status") or "wait")
    # P3.5.198.d (7/8 鸿波): 加 raw status_resp key 日志便于 audit 已绑用户扫码卡
    # scaned 场景 (ilink 侧不给 confirm). 记 keys 不记 value 防 token 泄漏.
    logger.info(
        "P30 wechat qr poll: qrcode=%s...%s status=%r keys=%s",
        qrcode_value[:6], qrcode_value[-4:],
        status,
        sorted(status_resp.keys()) if isinstance(status_resp, dict) else "not-dict",
    )

    if status == "wait":
        return web.json_response({"status": "wait"})

    if status == "scaned":
        return web.json_response({"status": "scaned"})

    if status == "scaned_but_redirect":
        redirect_host = str(status_resp.get("redirect_host") or "")
        if redirect_host:
            sess_state["base_url"] = f"https://{redirect_host}"
        # 前端语义把它归到 scaned (等下次 poll 用新 base_url 拿最终 confirmed).
        return web.json_response({"status": "scaned"})

    if status == "expired":
        _wechat_qr_sessions.pop(qrcode_value, None)
        return web.json_response({"status": "expired"})

    if status == "confirmed":
        account_id = str(status_resp.get("ilink_bot_id") or "")
        token = str(status_resp.get("bot_token") or "")
        acct_base_url = str(status_resp.get("baseurl") or base_url)
        user_id = str(status_resp.get("ilink_user_id") or "")
        if not account_id or not token:
            logger.warning(
                "P30 wechat qr poll: confirmed 但 payload 缺 ilink_bot_id/bot_token "
                "(account_id=%r len_token=%d)", account_id, len(token),
            )
            return web.json_response({
                "status": "wait",
                "_warning": "confirmed but credential payload incomplete",
            })
        try:
            save_weixin_account(
                str(get_hermes_home()),
                account_id=account_id,
                token=token,
                base_url=acct_base_url,
                user_id=user_id,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("P30 wechat qr poll: save_weixin_account 失败")
            return web.json_response(
                {"error": f"保存 credential 失败: {e}"}, status=500,
            )
        # P31 (P3.5.198.f 7/8 鸿波): save 完 credential 立刻同步 ~/.hermes/.env
        # 的 WEIXIN_* 变量. 老流程要员工扫完码开 terminal 手动 vim .env, 荒唐;
        # P31 后员工只需 restart hermes (Companion 服务重启按钮或 launchd) 即通.
        # 失败不阻塞主流程 — credential 已经 save 到 accounts/, 员工大不了手动
        # 编 env (回退到荒唐路径). fail-safe.
        try:
            _sync_hermes_env_weixin(
                account_id=account_id, bot_token=token, user_id=user_id,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("P31 sync .env WEIXIN_* 失败 (不阻塞): %s", e)
        _wechat_qr_sessions.pop(qrcode_value, None)
        logger.info(
            "P30 wechat qr confirmed: account_id=%s user_id=%s → 已 save_weixin_account",
            account_id, user_id,
        )
        # P38 (P3.5.201 7/8 鸿波军规审判 — 撤 P32-P37 中间层):
        #
        # audit 出 hermes 里原生已有 SIGUSR1 graceful restart:
        #   - gateway/run.py:19362-19364 gateway 注册 SIGUSR1 handler
        #   - gateway/run.py:5973 request_restart(via_service=True) drain in-flight
        #   - launchd `<KeepAlive>true</>` (plutil verify) 自动拉起新 process
        #   - hermes_cli/gateway.py:239 _graceful_restart_via_sigusr1 官方 recipe
        #
        # 实测 (7/8 21:xx 鸿波本机): kill -USR1 hermes → 3 秒 launchd 起新 PID.
        #
        # 老路径 (P32-P37) 是我们在 Companion 侧搭 "kill -9 + sh -lc start + poll
        # /health 45s" 中间层 — 复杂 + 无 drain 断 in-flight chat/SSE + 装机依
        # 赖 sh -lc PATH / launchd 501 recovery / hermes_kill Tauri command
        # 权限. 全部推翻用 hermes 原生 SIGUSR1 收敛到一行 plugin 代码.
        #
        # # 边界
        #
        # 1. asyncio.get_event_loop().call_later(3s, ...) 延迟 3 秒 — 给当前
        #    response (return web.json_response 下面那句) 时间 flush 到网络,
        #    Modal 收到 confirmed 后再 hermes 才 drain+exit.
        # 2. 用 signal.SIGUSR1 (POSIX 通用, mac+linux 都有). Windows 走
        #    hasattr(signal, "SIGUSR1") = False 分支跳过 — 员工用 Companion
        #    在 mac/linux, 忽略 Windows.
        # 3. 失败不阻塞主流程 — credential 已 save 到 accounts/, env 已 sync,
        #    员工手动 hermes gateway restart 也能起来 (fallback 路径).
        try:
            import signal as _signal, asyncio as _asyncio
            if hasattr(_signal, "SIGUSR1"):
                _loop = _asyncio.get_event_loop()
                _hermes_pid = os.getpid()
                def _fire_sigusr1() -> None:
                    try:
                        _signal.raise_signal(_signal.SIGUSR1)  # type: ignore[attr-defined]
                    except AttributeError:
                        # Python < 3.8 or Windows fallback (should not fire here)
                        os.kill(_hermes_pid, _signal.SIGUSR1)
                    except Exception as _e:  # noqa: BLE001
                        logger.warning("P38 SIGUSR1 raise 失败: %s", _e)
                _loop.call_later(3.0, _fire_sigusr1)
                logger.info(
                    "P38 已排 SIGUSR1 3s 后自 restart (pid=%s) — hermes 原生 "
                    "graceful drain + launchd KeepAlive 拉起, WeixinAdapter "
                    "读新 .env credential. Modal 侧看 /health 通就 auto-close.",
                    _hermes_pid,
                )
            else:
                logger.info(
                    "P38: signal.SIGUSR1 不存在 (Windows?), 员工需手动 "
                    "'hermes gateway restart' 让新 credential 生效.",
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("P38 SIGUSR1 排程失败 (不阻塞, 员工需手动 restart): %s", e)
        return web.json_response({
            "status": "confirmed",
            "account_id": account_id,
            "user_id": user_id,
        })

    # 未知 status — 归到 wait + warning, 别让前端崩
    logger.info("P30 wechat qr poll: 未知 status=%r, 归 wait", status)
    return web.json_response({
        "status": "wait",
        "_warning": f"ilink 未知 status: {status}",
    })


# ── P31 (P3.5.198.f 7/8 鸿波): .env WEIXIN_* 自动同步 ─────────────────
#
# # 真因 (7/8 05:xx 员工反馈"手动命令行同步，很荒唐")
#
# hermes WeixinAdapter (weixin.py:1162) init 时 self._account_id 从 config
# extra.account_id 或 env WEIXIN_ACCOUNT_ID 读死一个, 启动后不 hot-reload.
# 员工扫码 P30 只把新 credential 落到 ~/.hermes/weixin/accounts/{id}.json,
# 但 WeixinAdapter 手里的 account_id 还是老的. 结合 ilink 后端"一个 wechat
# user 一个 active bot" policy (员工每次扫码, ilink 侧生成新 bot_id → 老 bot
# 立刻废), 结果 WeixinAdapter 手里那个直接 errcode=-14 session timeout.
#
# 老流程要员工 open terminal 编 ~/.hermes/.env 换 WEIXIN_ACCOUNT_ID + WEIXIN_TOKEN,
# restart hermes. 这跟 "扫码即绑定" 承诺矛盾 — 员工反馈"荒唐".
#
# # P31 修法
#
# P30 confirmed 分支 save_weixin_account 之后立刻 call _sync_hermes_env_weixin
# 更新 .env 里的:
#   - WEIXIN_ACCOUNT_ID: 换新 account_id (格式 xxx@im.bot)
#   - WEIXIN_TOKEN: 换新 bot_token — hermes qr_login helper 拿的 ilink bot_token
#     就是完整 "{account_id}:{hex}" 格式 (JSON 里也是), 直接放入不再拼 prefix.
#   - WEIXIN_HOME_CHANNEL: 如果原来为空且新 user_id 有值就补上; 有值就保留
#     (员工可能显式 pin 某个 home channel, 不覆盖)
#
# 员工扫码后剩下的动作只有 "restart hermes" 一步 (Companion Dashboard 上的
# 服务重启按钮 / 或者 launchd kickstart / 或者敲一次 hermes gateway stop+start),
# 完全不用 vim .env.
#
# # 边界
#
# - 只改这 3 个 key, 其他 WEIXIN_* / 别的 env 完全不动
# - 原子写 (tmp + rename) 防 partial write 撞 half-updated .env 让 hermes 起不来
# - chmod 0600 保持权限 (env 里含 bot_token 是敏感)
# - .env 里从没这些 key 就 append (少数首次绑定场景)
# - 失败不阻塞 P30 主流程 — credential 已经落到 accounts/, 员工最坏回退到
#   老"手动 vim .env" 路径 (荒唐但至少可用)

def _sync_hermes_env_weixin(
    *, account_id: str, bot_token: str, user_id: str = "",
) -> None:
    """P31: 把新 WEIXIN_* 同步到 ~/.hermes/.env.

    bot_token 是 ilink 返回的 "bot_token" 原始值, 格式已经是
    "{account_id}:{token_hex}" — 直接写入 WEIXIN_TOKEN, 不再拼前缀.

    fail-safe: 抛错让 caller 记 warning 不阻塞主流程.
    """
    import os as _os
    from pathlib import Path as _Path

    env_path = _Path.home() / ".hermes" / ".env"
    if not env_path.exists():
        # 无 .env 直接创建 — 员工首次装 catfish, hermes 端可能还没 init env
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.touch(mode=0o600)

    try:
        content = env_path.read_text(encoding="utf-8")
    except Exception as e:
        raise RuntimeError(f"读 .env 失败: {e}") from e

    seen = {"WEIXIN_ACCOUNT_ID": False, "WEIXIN_TOKEN": False, "WEIXIN_HOME_CHANNEL": False}
    new_lines: list[str] = []
    for line in content.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("WEIXIN_ACCOUNT_ID="):
            new_lines.append(f"WEIXIN_ACCOUNT_ID={account_id}\n")
            seen["WEIXIN_ACCOUNT_ID"] = True
        elif stripped.startswith("WEIXIN_TOKEN="):
            new_lines.append(f"WEIXIN_TOKEN={bot_token}\n")
            seen["WEIXIN_TOKEN"] = True
        elif stripped.startswith("WEIXIN_HOME_CHANNEL="):
            existing_val = stripped.split("=", 1)[1].strip() if "=" in stripped else ""
            # 已有值就保留 (员工可能显式 pin 别的 channel); 空才补 user_id
            if not existing_val and user_id:
                new_lines.append(f"WEIXIN_HOME_CHANNEL={user_id}\n")
            else:
                new_lines.append(line)
            seen["WEIXIN_HOME_CHANNEL"] = True
        else:
            new_lines.append(line)

    # 缺哪 key 追加 (首次绑定 .env 里可能没这几行)
    # 确保结尾有换行防拼进上一行
    if new_lines and not new_lines[-1].endswith("\n"):
        new_lines[-1] = new_lines[-1] + "\n"
    if not seen["WEIXIN_ACCOUNT_ID"]:
        new_lines.append(f"WEIXIN_ACCOUNT_ID={account_id}\n")
    if not seen["WEIXIN_TOKEN"]:
        new_lines.append(f"WEIXIN_TOKEN={bot_token}\n")
    if not seen["WEIXIN_HOME_CHANNEL"] and user_id:
        new_lines.append(f"WEIXIN_HOME_CHANNEL={user_id}\n")

    # atomic write + chmod 600
    tmp = env_path.with_name(f".env.p31.tmp.{_os.getpid()}")
    try:
        tmp.write_text("".join(new_lines), encoding="utf-8")
        try:
            tmp.chmod(0o600)
        except OSError:
            pass  # Windows / 特殊 FS 没 chmod 概念, 不致命
        tmp.replace(env_path)
    except Exception as e:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise RuntimeError(f"原子写 .env 失败: {e}") from e

    logger.info(
        "P31 .env WEIXIN_* 已同步: WEIXIN_ACCOUNT_ID=%s (bot_token 长度=%d) "
        "user_id=%s. Restart hermes gateway 让 WeixinAdapter 拿新 credential.",
        account_id, len(bot_token), user_id or "(未变)",
    )


def _patch_p30_wechat_qr_endpoints() -> None:
    """P30 (P3.5.198 7/8 鸿波): wechat qr_login start/poll RESTful endpoint.

    跟 P26 同模式: attach handler 到 APIServerAdapter class, route 由
    _patched_app_init 在 Application 创建时真注册 (router 未 freeze 时机).

    fail-safe: import 失败 / attach 失败 → silent skip 不阻塞 hermes 启动.
    """
    try:
        from gateway.platforms.api_server import APIServerAdapter  # noqa: PLC0415
    except ImportError as e:
        logger.warning(
            "P30: api_server 没导, skip wechat qr endpoint patch (%s)", e,
        )
        return
    APIServerAdapter._handle_wechat_qr_start = _handle_wechat_qr_start
    APIServerAdapter._handle_wechat_qr_poll = _handle_wechat_qr_poll
    logger.info(
        "P30 APIServerAdapter._handle_wechat_qr_start/poll 已挂 ✓ "
        "(route 由 Application.__init__ patch 真注册, 跟 P26 同时机)"
    )


# ── P36 (P3.5.199 7/8 鸿波): execute_code / terminal / file_tools 默认 cwd ──
#
# # 真因 (7/8 chat sandbox 找不到 .catfish/uploads 深审 8 项)
#
# hermes 由 launchd 起 gateway daemon (~/Library/LaunchAgents/ai.hermes.gateway.plist),
# ProgramArguments 里没设 WorkingDirectory → process cwd = "/". LLM 里
# execute_code 用相对路径 (`.catfish/uploads/x.csv`, `notes.md`) 就从 `/` 找
# → FileNotFoundError.
#
# hermes source grep 出 5 处 os.getcwd() 会返 "/":
#   - tools/code_execution_tool.py:_resolve_child_cwd → execute_code subprocess cwd
#   - tools/terminal_tool.py:_safe_getcwd → _get_env_config 用
#   - tools/file_tools.py:_resolve_base_dir 兜底
#   - tools/file_operations.py 链式 fallback 兜底
#   - tools/environments/local.py 兜底
#
# 前 3 处**都优先读 TERMINAL_CWD env** (hermes upstream 公开 API, 稳定, 有
# unit test 覆盖). setdefault 兜底一次覆盖三条路径, 不 monkey-patch.
#
# # 边界
#
# 1. 员工/装机脚本已 export TERMINAL_CWD → 尊重, 不覆盖 (员工主权)
# 2. $HOME 不是有效目录 → skip (edge case)
# 3. 顶层 try/except 挂了不阻塞 hermes 启动 (跟 P29-P35 同 pattern)
#
# # 不 fail-loud 的原因
#
# 不列入 _PATCH_TARGETS. P36 不依赖任何 hermes 内部 attr / func, 只 setenv 一
# 个公开 env var. hermes 未来重构 _resolve_child_cwd / _get_env_config 内部
# 实现, TERMINAL_CWD env 语义都不会变 (hermes 自己 docstring 里明说 "session's
# TERMINAL_CWD (same as the terminal tool)").
#
# # 影响面 (审 P36 audit 8 项后严格分类)
#
# 全部正向:
#   ✓ execute_code 里 open(".catfish/uploads/x") 找到 $HOME/.catfish/uploads/x
#   ✓ terminal 里 `ls .catfish` 找到 $HOME/.catfish
#   ✓ file_tools read_file("notes.md") 找到 $HOME/notes.md
#   ✓ cron scheduled 里 execute_code 也从 $HOME 起 (员工 cron script 更需要)
#   ✓ LLM 用 terminal `cd /somewhere` 后 overrides.cwd 机制照旧生效, 不 touch
#   ✓ SSH / Docker / Modal / Daytona backend 完全不 touch (它们本身有合理 default)
