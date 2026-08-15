"""死代理检测 + 清理 hermes 进程 env (BL-EDGE-TOOL-PROXY, 5/25)。

8/15 从 catfish.py 搬出来 —— 整组一起搬, 一个不留。

# ⚠ 为什么必须整组一起搬

test_catfish.py 里有 4 条测试是这么写的:

    monkeypatch.setattr(catfish, "_check_proxy_alive", lambda url, **k: False)
    catfish._handle_proxy_cleanup(auto_restart=False)

如果只搬 `_check_proxy_alive` 而把 `_handle_proxy_cleanup` 留在 catfish.py,
或者反过来, 那条 monkeypatch 就**打不到实际被调用的那个名字**上了 ——
函数体里的自由变量是在**定义它的那个模块**的 globals 里查的, 不是调用方的。
catfish.py 那边的 re-export 只是另一个绑定, 改它影响不到这边。

那种情况下测试要么假绿 (测了个寂寞), 要么直接去连真实的代理端口 / 真的去
`hermes gateway restart`。所以判据是: **被 monkeypatch 的名字, 必须和它的
调用者待在同一个模块里**。这一组 6 个名字对外只暴露一个入口
(`_handle_proxy_cleanup` ← `cmd_refresh_hermes`), 内部闭环, 整组搬最干净。

对应地, test_catfish.py 里那几条的 patch 目标从 `catfish` 改成了
`catfish_proxy`。tests/test_split_layering.py 有一条守卫钉住这件事, 免得
以后有人"顺手"改回去。

# 依赖方向

只依赖标准库 + catfish_config 都不需要 —— 这一组零仓内依赖。
"""
from __future__ import annotations

import os

#
# 背景 (5/24 凌晨鸿波 web_search demo 暴露):
#   员工 shell 经常有 HTTPS_PROXY=http://127.0.0.1:7890 指向 Clash / Mihomo /
#   v2ray, 但代理常常没开. hermes gateway restart 起的 hermes 进程**继承**这条
#   死代理, 之后所有外网调用 (Tavily web_search / Firecrawl 等) 全撞墙 timeout,
#   模型学会"web_search 不能用", 退化用 catfish_browser_* 抓页面 (慢 30 倍).
#
# Gateway 自己启动时跑过这个检测 (network.py:precheck_and_setup), 清掉了自己
# 进程的死代理. 但 hermes 是单独进程, 没人帮它清. 现在 catfish refresh-hermes
# 顺手帮 hermes 的 restart 把 env 清干净.
#
# 复用 gateway/network.py 的检测思路, 但不依赖那个模块 (catfish-cli 是单文件,
# 不想加 import). 50 行抄过来 + 个性化 logging.


_PROXY_ENV_VARS = (
    "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
    "https_proxy", "http_proxy", "all_proxy",
)


def _check_proxy_alive(proxy_url: str, timeout: float = 2.0) -> bool:
    """TCP probe 代理端口是否能连上.

    抄 gateway network.py:_check_tcp_port 同款. 不实际跑 HTTP, 只看 TCP 通不通
    (端口接受连接 = 代理至少在跑, 哪怕配错也算"活着", 不在我们 scope).
    """
    if not proxy_url:
        return False
    from urllib.parse import urlparse  # noqa: PLC0415
    if not proxy_url.startswith(("http://", "https://")):
        proxy_url = "http://" + proxy_url
    try:
        parsed = urlparse(proxy_url)
    except ValueError:
        return False
    host = parsed.hostname
    if not host:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    import socket  # noqa: PLC0415
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (TimeoutError, OSError):
        return False


def _detect_dead_proxy_vars() -> list[tuple[str, str]]:
    """检测当前 shell env 里哪些 proxy var 指向死端口.

    返 [(var_name, value), ...]. 全活或全没设返 []. 多个 var 指同一个 URL 算多条.

    幂等 — 只读 env, 不修改.
    """
    seen_urls: dict[str, bool] = {}  # url → alive cache, 不重复 TCP probe
    dead: list[tuple[str, str]] = []
    for var in _PROXY_ENV_VARS:
        val = os.environ.get(var, "").strip()
        if not val:
            continue
        if val not in seen_urls:
            seen_urls[val] = _check_proxy_alive(val)
        if not seen_urls[val]:
            dead.append((var, val))
    return dead


def _build_clean_env(unset_vars: list[str]) -> dict[str, str]:
    """copy os.environ 然后删指定 var, 给 subprocess 用. 不动当前进程 env."""
    env = os.environ.copy()
    for v in unset_vars:
        env.pop(v, None)
    return env


def _restart_hermes_with_clean_env(unset_vars: list[str]) -> int:
    """spawn `hermes gateway restart` with proxy env vars removed.

    返 hermes 命令的 exit code. 找不到 hermes 命令返 127.
    不挂任何异常 — caller 应当吞掉 (这是 best-effort 帮员工省事).
    """
    import shutil  # noqa: PLC0415
    import subprocess  # noqa: PLC0415

    hermes_bin = shutil.which("hermes")
    if not hermes_bin:
        print("  proxy-clean: ⚠ 找不到 hermes 命令 (PATH 没设?), 跳过自动 restart")
        return 127

    env = _build_clean_env(unset_vars)
    try:
        result = subprocess.run(
            [hermes_bin, "gateway", "restart"],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        print("  proxy-clean: ⚠ hermes gateway restart 60s 没返, 放弃")
        return 124
    except Exception as e:  # pragma: no cover
        print(f"  proxy-clean: ⚠ hermes gateway restart 出错: {e}")
        return 1

    # hermes "✓ Service restarted" 这种行打到 stdout
    if result.stdout:
        for line in result.stdout.rstrip().split("\n"):
            print(f"  hermes: {line}")
    if result.returncode != 0 and result.stderr:
        for line in result.stderr.rstrip().split("\n")[:5]:
            print(f"  hermes(err): {line}")
    return result.returncode


def _handle_proxy_cleanup(auto_restart: bool) -> None:
    """打 banner + 可选 auto restart hermes. 永远 best-effort, 不挂主流程.

    auto_restart=True (传 --restart-hermes flag): 死代理 → unset + 重启 hermes.
    auto_restart=False: 只警告, 给员工具体命令, 不动 hermes.

    无死代理 → 静默不打字 (避免 banner 噪音).
    """
    dead = _detect_dead_proxy_vars()
    if not dead:
        return

    # 打 banner
    print()
    print("⚠ 检测到死代理 (TCP 端口连不上):")
    for var, val in dead:
        print(f"    {var}={val}")
    print("  hermes 进程继承这条会让 web_search / Firecrawl 等外网工具撞墙 timeout.")

    var_names = sorted({v for v, _ in dead})
    if auto_restart:
        print(f"  → 自动用 clean env 重启 hermes (unset: {' '.join(var_names)})")
        rc = _restart_hermes_with_clean_env(var_names)
        if rc == 0:
            print("  ✓ hermes 已用 clean env 重启, web_search 等工具应可正常调")
        else:
            print(f"  ✗ hermes restart 失败 (rc={rc}), 手动跑下面命令:")
            print(f"    unset {' '.join(var_names)}")
            print(f"    hermes gateway restart")
    else:
        print("  → 推荐用这两条手动重启 (跳过死代理):")
        print(f"    unset {' '.join(var_names)}")
        print(f"    hermes gateway restart")
        print("  → 或下次 `catfish refresh-hermes --restart-hermes` 自动帮你做.")
