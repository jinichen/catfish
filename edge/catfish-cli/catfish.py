#!/usr/bin/env python3
"""catfish — 鲶鱼员工自助登录 CLI (BL-CATFISH-LOGIN, 5/14 凌晨 ship).

# 目的

新机部署时不再需要 IT 手粘 token. 员工首次开 catfish 自己浏览器登录公司
SSO (catfish-identity), token 自动存 + 自动同步到 hermes config.

学 hermes 的 "Qwen OAuth (reuses local Qwen CLI login)" 模式 — 我们写一个
独立 CLI 管登录, hermes 通过 Custom endpoint 复用我们维护的 token.

# 用法

  catfish login                  # 浏览器登录, 拿 token, 写盘
  catfish status                 # 看登录状态 (是谁 / 还有多久过期)
  catfish logout                 # 清 token
  catfish token                  # 输出当前 access_token (供 shell 用, e.g. curl Bearer)
  catfish refresh                # 续 token (现在重做 login, 5/16+ 加 refresh_token 后真 refresh)

# 跑法 (无包装, 单文件直接跑)

  python3 ~/person_task/catfish/edge/catfish-cli/catfish.py login

  推荐加 alias 到 ~/.zshrc:
    alias catfish='python3 ~/person_task/catfish/edge/catfish-cli/catfish.py'

# 文件布局

  ~/.catfish/auth/token.json      catfish 自己的 token store (本 CLI 写)
  ~/.hermes/config.yaml           hermes 配置 (本 CLI 同步 api_key 进去)

# 设计决策

- 用 OAuth 2.0 authorization_code flow (RFC 6749 §4.1) + 浏览器 (RFC 8252 §7.3 native app)
- redirect_uri = http://localhost:RANDOM_PORT/callback (loopback 防中间人)
- state CSRF 防御 (随机 32 字节)
- **MVP 不上 PKCE** — catfish-identity 还没支持. 5/16 加上 PKCE + refresh_token 一起.
- **不存 client_secret** — public client (RFC 6749 §2.1). 装机包不应该含任何 secret.
- token 存盘格式: JSON, chmod 600. 含 expires_at 让 status / token 命令快判.

# 配套要做 (跟其他 task 协同)

- BL-IDENTITY-REFRESH-TOKEN (#79): catfish-identity 加 refresh_token grant
- 5/16 加 PKCE 支持后这文件加 PKCE 实现 (~30 行 hashlib + secrets)
- 5/22+ install.sh (#80) 把这文件加到装机流程
"""
from __future__ import annotations

import argparse
import http.server
import json
import logging
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# ─── 配置 / 路径 (8/15 搬去 catfish_config.py) ─────────────
#
# 这里 re-export 是为了**不破老 caller**: test_catfish.py 和别的脚本一直写
# `catfish.TokenStore` / `catfish._hermes_env_path()`, 拆分不该让它们改。
#
# ⚠ 这些全是**不可变常量和无状态函数**, 所以 from-import 的快照语义在这里
#   是安全的。换成可变全局就不安全了 —— `from X import name` 建的是新绑定
#   不是别名, 后来的 `global` 赋值这边永远看不见 (8/15 在 plugin_cors /
#   plugin_approval 上刚栽过, 见 test_cors_approval_wiring.py)。
from catfish_config import (  # noqa: F401
    CALLBACK_TIMEOUT_SECS,
    DEFAULT_CLIENT_ID,
    DEFAULT_GATEWAY_URL,
    DEFAULT_HERMES_CLI_CLIENT_ID,
    DEFAULT_HERMES_CLI_DEV_SECRET,
    DEFAULT_IDENTITY_URL,
    DEFAULT_SCOPES,
    EXPIRY_BUFFER_SECS,
    HERMES_PROVIDER_NAME,
    HERMES_SERVICE_SCOPES,
    HERMES_TOKEN_MIN_REMAINING_DAYS,
    _auth_dir,
    _client_id,
    _gateway_url,
    _hermes_config_path,
    _hermes_env_path,
    _identity_url,
    _scopes,
    _token_path,
    logger,
)



# ─── token 模型 / 存盘 / JWT / refresh (8/15 搬去 catfish_token.py) ──
#
# 整组一起走的理由见 catfish_token.py 的 docstring。这里 re-export 是为了不破
# test_catfish.py 里那 30 多处 `catfish.TokenStore` / `catfish.save_token`。
#
# ⚠ `_do_refresh` 被测试用 monkeypatch 换掉过, 打的是 catfish 上这个绑定。
#   现在成立, 因为调它的 cmd_token / cmd_refresh 还在本文件里。哪天它们也搬走,
#   那条 patch 就会静默失效 —— 同一天 catfish_proxy.py 上刚栽过这一下。
from catfish_token import (  # noqa: F401
    TokenStore,
    _decode_jwt_payload,
    _do_refresh,
    _fmt_expires,
    delete_token,
    load_token,
    save_token,
)


# ─── OAuth flow (8/15 搬去 catfish_oauth.py) ────────────────
#
# `_do_oauth_login` 只被 cmd_login 调, 但 `_CallbackHandler` 和它是一体的
# (handler 通过闭包往 flow 里回写 code/state), 所以整组走。
from catfish_oauth import (  # noqa: F401
    _CallbackHandler,
    _do_oauth_login,
    _find_free_port,
)


# ─── 死代理检测 (8/15 整组搬去 catfish_proxy.py) ──────────────
#
# 整组一起搬的理由见 catfish_proxy.py 的模块 docstring: 被 monkeypatch 的
# 名字必须跟它的调用者同模块, 拆开会让那 4 条测试静默失效。
#
# 这里 re-export 的都是**函数和不可变常量**, 快照语义安全。但注意:
# 在这边 `monkeypatch.setattr(catfish, "_check_proxy_alive", ...)` 是**无效**的,
# 要打就打 catfish_proxy —— tests/test_split_layering.py 钉住了这一点。
from catfish_proxy import (  # noqa: F401
    _PROXY_ENV_VARS,
    _build_clean_env,
    _check_proxy_alive,
    _detect_dead_proxy_vars,
    _handle_proxy_cleanup,
    _restart_hermes_with_clean_env,
)


# ─── hermes 同步 (8/15 整组搬去 catfish_hermes.py) ──────────────
#
# 原来分成"hermes config 同步"和"BL-EDGE-TOOL-KEY 中央派发"两段, 现在合成一个
# 模块 —— 它们互相调用, 而且 _patch_hermes_config / _fetch_edge_tool_list /
# _fetch_edge_tool_config 三个名字被 test_catfish.py monkeypatch。拆开的话
# patch 打空, 单测会去真写 ~/.hermes/config.yaml 和 ~/.hermes/.env。
# 详见 catfish_hermes.py 的模块 docstring。
#
# ⚠ 要 patch 这几个名字请打 catfish_hermes, 打 catfish 无效。
from catfish_hermes import (  # noqa: F401
    _fetch_edge_tool_config,
    _fetch_edge_tool_list,
    _hermes_cli_client_secret,
    _mint_hermes_service_token,
    _patch_hermes_config,
    _patch_hermes_config_yaml_blocks,
    _patch_hermes_env_file,
    _sync_hermes_edge_tool_configs,
    _sync_hermes_with_service_token,
)


# ─── 命令实现 ───────────────────────────────────────────────


def cmd_login(args) -> int:
    identity_url = args.identity_url or _identity_url()
    client_id = args.client_id or _client_id()
    scopes = args.scopes or _scopes()

    print(f"📡 catfish-identity:  {identity_url}")
    print(f"🆔 client_id:         {client_id}")
    print(f"🎫 scopes:            {scopes}")
    print()

    try:
        store = _do_oauth_login(
            identity_url=identity_url,
            client_id=client_id,
            scopes=scopes,
        )
    except Exception as e:
        print(f"\n❌ 登录失败: {e}", file=sys.stderr)
        return 1

    save_token(store)
    print(f"\n✓ 登录成功!")
    if store.user_email:
        print(f"  员工:     {store.user_email}")
    elif store.user_sub:
        print(f"  sub:      {store.user_sub}")
    print(f"  scope:    {store.scope}")
    print(f"  到期:     {store.expires_in_secs()} 秒后 ({_fmt_expires(store.expires_at)})")
    print(f"  已存:     {_token_path()}")

    # 同步 hermes config
    # BL-HERMES-SERVICE-TOKEN (5/24): 不再写 store.access_token (user OAuth, 1h TTL)
    # 改 mint hermes-cli service token (sub=client:hermes-cli, 30 天 TTL) 写进去.
    # 这样 hermes 30 天不用动 token, 不再撞 "缓存 user token 1h 后过期" 那个 bug.
    # user OAuth token 仍然存在 ~/.catfish/auth/token.json (catfish 自己用 +
    # Companion 通过 `catfish token` 拿) — 两条 token 互不影响.
    patched = _sync_hermes_with_service_token(
        identity_url=identity_url,
        user_fallback_token=store.access_token,
    )
    if patched:
        print(f"  hermes:   ✓ 已更新 ~/.hermes/config.yaml provider '{patched}'")
        print(f"            (备份: {_hermes_config_path().with_suffix('.yaml.bak')})")
    else:
        print(f"  hermes:   ⚠ 未同步 (跑过 'hermes model' setup 后再 catfish login 一次会自动同步)")

    return 0


def cmd_status(args) -> int:
    store = load_token()
    if not store:
        print("❌ 未登录. 跑: catfish login")
        return 1

    expired = store.is_expired(buffer=0)
    print(f"{'❌ 已过期' if expired else '✓ 已登录'}")
    if store.user_email:
        print(f"  员工:     {store.user_email}")
    elif store.user_sub:
        print(f"  sub:      {store.user_sub}")
    print(f"  client:   {store.client_id}")
    print(f"  issuer:   {store.issuer}")
    print(f"  scope:    {store.scope}")
    print(f"  到期:     {_fmt_expires(store.expires_at)} ({store.expires_in_secs()} 秒后)")
    if store.refresh_token:
        print(f"  refresh:  ✓ 有 (catfish refresh 续)")
    else:
        print(f"  refresh:  ✗ 没 (5/16+ 加 refresh_token 后会有, 现在过期要重 catfish login)")
    print(f"  文件:     {_token_path()}")
    return 0 if not expired else 1


def cmd_logout(args) -> int:
    deleted = delete_token()
    if deleted:
        print(f"✓ 已登出 (删了 {_token_path()})")
    else:
        print("(本来就没登录)")
    return 0


def cmd_token(args) -> int:
    """输出当前 access_token. 给 shell 用 (e.g. curl -H "Authorization: Bearer $(catfish token)").

    BL-IDENTITY-REFRESH-TOKEN (5/15): 如果 access_token 快过期 (60s 内) 且有 refresh_token,
    自动透明 refresh. 员工 / hermes 不感知.
    """
    store = load_token()
    if not store:
        print("ERROR: 未登录, 跑: catfish login", file=sys.stderr)
        return 1
    if store.is_expired():
        # 自动 refresh
        if store.refresh_token:
            try:
                store = _do_refresh(store)
                save_token(store)
                # BL-HERMES-SERVICE-TOKEN (5/24): user OAuth refresh 顺便 opportunistic
                # refresh hermes service token. 不影响主 cmd_token 返 user token 的语义,
                # 只是让 hermes 那条独立链也跟上节奏. mint 失败不阻塞 cmd_token (caller
                # 调 catfish token 是为了 user token, hermes 同步是副作用).
                try:
                    _sync_hermes_with_service_token(
                        identity_url=store.issuer,
                        user_fallback_token=store.access_token,
                    )
                except Exception as e:
                    logger.warning("hermes service token 同步失败 (non-fatal): %s", e)
            except Exception as e:
                print(f"ERROR: refresh 失败 ({e}), 跑: catfish login", file=sys.stderr)
                return 1
        else:
            print(
                f"ERROR: token 已过期 ({_fmt_expires(store.expires_at)}) 且无 refresh_token, "
                f"跑: catfish login",
                file=sys.stderr,
            )
            return 1
    print(store.access_token)
    return 0


def cmd_refresh(args) -> int:
    """续 token. 有 refresh_token 走 refresh grant 无感续, 没 refresh_token 退化到重 login.

    BL-HERMES-SERVICE-TOKEN (5/24): 顺手 refresh hermes service token (独立 client_credentials
    grant, 跟 user OAuth refresh 完全解耦). 想纯 refresh hermes 不动 user 用 `catfish refresh-hermes`.
    """
    store = load_token()
    if store and store.refresh_token:
        try:
            new_store = _do_refresh(store)
            save_token(new_store)
            # BL-HERMES-SERVICE-TOKEN (5/24): mint 新 service token 写 hermes config
            _sync_hermes_with_service_token(
                identity_url=new_store.issuer,
                user_fallback_token=new_store.access_token,
            )
            print(f"✓ token 已续 ({new_store.expires_in_secs()} 秒后过期, "
                  f"{_fmt_expires(new_store.expires_at)})")
            if store.user_email:
                print(f"  员工: {store.user_email}")
            return 0
        except Exception as e:
            print(f"❌ refresh 失败: {e}", file=sys.stderr)
            print("退化到重做 login...", file=sys.stderr)
    else:
        print("(没 refresh_token, 重做 login — 浏览器会再开一次)")
    return cmd_login(args)


def cmd_refresh_hermes(args) -> int:
    """BL-HERMES-SERVICE-TOKEN (5/24): 独立 refresh hermes service token, 不动 user OAuth.

    使用场景:
      - hermes service token 快到 30 天上限, 手动 rotate
      - launchd / cron 每月跑一次防 token 过期
      - identity-server 配置改了 (client_secret 轮换), 强制重新 mint

    不需要先 catfish login — 完全 server-to-server, 不依赖任何 user 状态.
    """
    identity_url = (args.identity_url if hasattr(args, 'identity_url') else None) \
        or _identity_url()
    print(f"📡 catfish-identity:  {identity_url}")
    print(f"🆔 client_id:         {DEFAULT_HERMES_CLI_CLIENT_ID}")
    print(f"🎫 scopes:            {HERMES_SERVICE_SCOPES}")
    print()

    try:
        patched = _sync_hermes_with_service_token(identity_url=identity_url)
    except Exception as e:
        print(f"\n❌ refresh-hermes 失败: {e}", file=sys.stderr)
        return 1

    if patched:
        print(f"\n✓ hermes config 已更新 provider '{patched}'")
        print(f"  接下来: hermes gateway restart  # 让 hermes 读新 token")
    else:
        print(f"\n⚠ hermes config 没更新 (~/.hermes/config.yaml 不存在 / 没匹配 provider).")
        print(f"  先跑 hermes model 配 Custom endpoint (localhost:8999) 再来一次.")

    # BL-EDGE-TOOL-PROXY (5/25): 死代理检测 + 可选 auto restart hermes.
    # 解决 5/24 凌晨发现的 web_search 走 browser 后路问题 — hermes 进程继承
    # 死 HTTPS_PROXY → Tavily HTTP 撞墙 → 模型退化用浏览器抓页面.
    _handle_proxy_cleanup(auto_restart=getattr(args, "restart_hermes", False))

    return 0 if patched else 1


# ─── 隐私自查 (8/15 整组搬去 catfish_privacy.py) ──────────────
#
# `cmd_privacy_audit` 跟它的 4 个 helper 和那张 _PRIVACY_SCAN_TARGETS 表一起走。
# 拆表和用表的人是这次拆分里最没意义的一种分家。
from catfish_privacy import (  # noqa: F401
    _PRIVACY_SCAN_TARGETS,
    _fetch_audit_me,
    _fmt_bytes,
    _scan_local_audit_jsonl,
    _stat_path,
    cmd_privacy_audit,
)



def _delegate_to_bash_catfish(subcommand: str, extra_args: list[str]) -> int:
    """P33 (6/5 鸿波) — Python CLI 装的员工 → bash CLI 的 doctor / lint / brand-*
    走 subprocess 透传, 不 Python 重写一遍. 实时 stdout (capture_output=False).

    bash CLI 位置: <repo>/edge/branding/catfish (从 catfish.py 相对推).
    """
    script_dir = Path(__file__).resolve().parent  # edge/catfish-cli/
    bash_cli = script_dir.parent / "branding" / "catfish"  # edge/branding/catfish
    if not bash_cli.exists():
        print(
            f"✗ bash catfish CLI 找不到: {bash_cli}\n"
            f"  设 CATFISH_REPO_ROOT=<repo> 或 cd 到 catfish 仓库后重跑",
            file=sys.stderr,
        )
        return 1
    cmd = ["bash", str(bash_cli), subcommand, *extra_args]
    try:
        proc = subprocess.run(cmd, check=False)
        return proc.returncode
    except FileNotFoundError:
        print(f"✗ bash 不在 PATH", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


def cmd_doctor(args) -> int:
    """完整自检 — Gateway / Chrome / Plugins / Wiki / Memory / Network / Brand patch.
    透传到 bash catfish doctor (含 P15 [Network] off-site check).
    """
    return _delegate_to_bash_catfish("doctor", getattr(args, "passthrough", []) or [])


def cmd_lint(args) -> int:
    """扫 ~/.catfish/wiki/ broken-link / orphan concept / dead-end.
    透传到 bash catfish lint (跑 scripts/lint_wiki.py).
    """
    return _delegate_to_bash_catfish("lint", getattr(args, "passthrough", []) or [])


def cmd_brand_check(args) -> int:
    """校 hermes brand patch 完整性 (升 hermes 后跑). 透传 bash."""
    return _delegate_to_bash_catfish("brand-check", getattr(args, "passthrough", []) or [])


def cmd_brand_fix(args) -> int:
    """re-apply hermes brand patch (升 hermes 后第一次跑). 透传 bash."""
    return _delegate_to_bash_catfish("brand-fix", getattr(args, "passthrough", []) or [])


def cmd_init(args) -> int:
    """P25 (6/5 鸿波): 商用部署 onboarding.

    cp ~/person_task/catfish/edge/catfish-cli/templates/{companion,memory_plugin}.yaml.example
       到 ~/.catfish/{companion,memory_plugin}.yaml (chmod 600 后者).
    已存 skip (不覆盖, 保护客户已改的 config).

    退码: 0 (always — 已存也算成功).
    """
    GREEN = "\033[1;32m"
    YELLOW = "\033[1;33m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

    # 定位 templates dir (从本脚本相对位置)
    script_dir = Path(__file__).resolve().parent  # edge/catfish-cli/
    templates_dir = script_dir / "templates"

    target_dir = Path.home() / ".catfish"
    target_dir.mkdir(parents=True, exist_ok=True)

    print(f"{BOLD}=== catfish init: 配置文件初始化 ==={RESET}\n")

    for name, chmod_secret in (
        ("companion.yaml", False),
        ("memory_plugin.yaml", True),  # 含 token, chmod 600
    ):
        example = templates_dir / f"{name}.example"
        target = target_dir / name

        if target.exists():
            print(f"    {YELLOW}已存{RESET} {target} (skip, 不覆盖)")
            continue
        if not example.exists():
            print(f"    {YELLOW}缺{RESET} template {example} 不存在")
            continue

        try:
            target.write_bytes(example.read_bytes())
            if chmod_secret:
                target.chmod(0o600)  # secret 防泄露
                print(f"    {GREEN}OK{RESET} cp → {target} (chmod 600)")
            else:
                print(f"    {GREEN}OK{RESET} cp → {target}")
        except OSError as e:
            print(f"    {YELLOW}失败{RESET} {target}: {e}")

    print(f"\n{BOLD}下一步{RESET}:")
    print(f"  1. vim {target_dir / 'companion.yaml'}")
    print(f"     改 endpoints.gateway_url (中央部署 IP)")
    print(f"  2. vim {target_dir / 'memory_plugin.yaml'}")
    print(f"     改 gateway.url + gateway.token")
    print(f"  3. catfish status         自检")
    print(f"  4. hermes gateway stop && hermes gateway start    reload plugin")
    return 0





# ─── argparse 入口 ─────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="catfish",
        description="鲶鱼员工自助登录 CLI — 浏览器 OAuth + 自动同步 hermes",
    )
    sub = p.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--identity-url", help=f"catfish-identity URL (默认 {DEFAULT_IDENTITY_URL})")
    common.add_argument("--client-id", help=f"OAuth client_id (默认 {DEFAULT_CLIENT_ID})")
    common.add_argument("--scopes", help="OAuth scopes (空格分隔)")

    login_p = sub.add_parser("login", parents=[common], help="浏览器 OAuth 登录")
    login_p.set_defaults(func=cmd_login)

    status_p = sub.add_parser("status", help="查登录状态")
    status_p.set_defaults(func=cmd_status)

    logout_p = sub.add_parser("logout", help="登出 (删 token)")
    logout_p.set_defaults(func=cmd_logout)

    token_p = sub.add_parser("token", help="输出当前 access_token (供 shell)")
    token_p.set_defaults(func=cmd_token)

    refresh_p = sub.add_parser("refresh", parents=[common], help="续 token (现在重做 login)")
    refresh_p.set_defaults(func=cmd_refresh)

    # BL-HERMES-SERVICE-TOKEN (5/24): 独立 refresh hermes service token, 不动 user OAuth.
    # 适合 cron / 30 天前手动跑. 跟 `refresh` 命令的区别: refresh 续 user OAuth (顺手
    # 也续 hermes); refresh-hermes 只续 hermes, 不需要 user 登录态.
    refresh_hermes_p = sub.add_parser(
        "refresh-hermes",
        parents=[common],
        help="Mint 新 hermes service token + 写 hermes config (30 天 TTL, 跟 user OAuth 解耦)",
    )
    # BL-EDGE-TOOL-PROXY (5/25): 默认只检测+警告死代理. 加 flag 才真自动 restart hermes
    # 跟 clean env (unset 死的 HTTPS_PROXY/HTTP_PROXY/ALL_PROXY). 推荐 demo / cron 用.
    refresh_hermes_p.add_argument(
        "--restart-hermes",
        action="store_true",
        help="检测到死代理时, 用 clean env (unset HTTPS_PROXY 等) 自动 hermes gateway restart",
    )
    refresh_hermes_p.set_defaults(func=cmd_refresh_hermes)

    # BL-EMPLOYEE-PRIVACY-VERIFICATION (#76, 5/25): 员工自查 "本机存了啥 + 中央存了我啥"
    privacy_p = sub.add_parser(
        "privacy-audit",
        help="员工自查: 本机存了啥 + 中央存了我啥 (透明性卖点兑现)",
    )
    privacy_p.add_argument(
        "--json",
        action="store_true",
        help="输出 JSON (供 CI / 软著合规脚本机器读)",
    )
    privacy_p.set_defaults(func=cmd_privacy_audit)

    # P25 (6/5 鸿波): 商用部署 onboarding — cp companion.yaml + memory_plugin.yaml
    # example 到 ~/.catfish/, 客户开箱即用. 已存 skip (不覆盖)真.
    init_p = sub.add_parser(
        "init",
        aliases=["setup"],
        help="初始化 ~/.catfish/{companion,memory_plugin}.yaml (商用首次部署)",
    )
    init_p.set_defaults(func=cmd_init)

    # P33 (6/5 鸿波): bash CLI 真 doctor / lint / brand-* 透传 Python CLI,
    # 鸿波装 Python entry-point 也能跑全套自检 + brand patch 管理.
    doctor_p = sub.add_parser(
        "doctor",
        aliases=["self-check", "check"],
        help="完整自检 — Gateway / Chrome / Plugins / Wiki / Memory / Network / Brand patch",
    )
    doctor_p.add_argument("passthrough", nargs="*", help="透传给 bash catfish doctor")
    doctor_p.set_defaults(func=cmd_doctor)

    lint_p = sub.add_parser(
        "lint",
        help="扫 ~/.catfish/wiki/ broken-link / orphan / dead-end (--json 机器读)",
    )
    lint_p.add_argument("passthrough", nargs="*", help="透传给 bash catfish lint")
    lint_p.set_defaults(func=cmd_lint)

    brand_check_p = sub.add_parser(
        "brand-check",
        help="校 hermes brand patch 完整性 (升 hermes 后跑)",
    )
    brand_check_p.add_argument("passthrough", nargs="*", help="透传给 bash catfish brand-check")
    brand_check_p.set_defaults(func=cmd_brand_check)

    brand_fix_p = sub.add_parser(
        "brand-fix",
        help="re-apply hermes brand patch (升 hermes 后第一次跑)",
    )
    brand_fix_p.add_argument("passthrough", nargs="*", help="透传给 bash catfish brand-fix")
    brand_fix_p.set_defaults(func=cmd_brand_fix)

    return p


_PASSTHROUGH_COMMANDS = {"doctor", "self-check", "check", "lint", "brand-check", "brand-fix"}


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = build_parser()
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    # P33 (6/5 鸿波): doctor/lint/brand-* `bash CLI 透传` 真 — args 可能带
    # --json 之类`不在 Python parser 注册` `flag`, 用 parse_known_args
    # 收 unknown args 全塞 passthrough.
    if raw_argv and raw_argv[0] in _PASSTHROUGH_COMMANDS:
        args, extra = parser.parse_known_args(raw_argv)
        # 跟 nargs="*" 收的合并
        prev = getattr(args, "passthrough", None) or []
        args.passthrough = list(prev) + list(extra)
    else:
        args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
