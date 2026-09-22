#!/usr/bin/env python3
"""生成 / 升级装机目录里的 .env —— setup.sh 和 setup.ps1 共用这一份实现。

# 为什么要有这个文件

Windows 需要一个原生装机脚本 (客户包里没有源码, deploy.ps1 去 build 必然
失败; 而 setup.sh 依赖 openssl / sed / grep 这些 Windows 上没有的东西)。

直觉做法是把 setup.sh 翻译一份 PowerShell。**不这么干**, 因为下面这些不变量
一旦两份实现之一漏掉, 症状都是"装完看起来正常, 过一阵才发现完了":

  · 重跑装机换掉 PG_PASSWORD  → 连不上已有的库, 四个服务全挂, 报错只在
                                容器日志里, 装机脚本一路绿
  · 重跑装机换掉 CATFISH_SECRET_KEY → 服务照常起, 但库里所有供应商 API key
                                全部解不开, 且**旧密文无法恢复**
  · 覆盖在先、保护在后 → 那句"已有值·保持不变"永远走不到 (8/1 和 8/4
                         各踩过一次, 同一个形状)

这些判断只写一遍。两个平台的脚本都调这里, 没有第二份可以跑偏。

# 怎么调

优先在容器里跑 (identity 镜像自带 python3, 宿主机不用装任何东西):

    docker run --rm -v <装机目录>:/work -v <tools 目录>:/tools:ro \\
        catfish-identity:<tag> python3 /tools/envgen.py --dir /work ...

宿主机有 python3 时也能直接跑 —— test_setup_env.sh 走的就是这条, 这样
CI 里不需要 docker 也能验这份逻辑。

退出码: 0 = 成功 / 1 = 失败 (且已经把原因和修法打出来)
"""
from __future__ import annotations

import argparse
import base64
import re
import secrets
import shutil
import sys
import time
from pathlib import Path

# 跨装机必须原样保留的值。换掉任何一个都会出事, 见文件顶部。
PERSISTENT = ("PG_PASSWORD", "JWT_SIGNING_KEY", "CATFISH_SECRET_KEY")


def read_value(text: str, key: str) -> str:
    """取 .env 里某个 key 的值。取不到返回空串。

    只认行首的 `KEY=`, 注释掉的 (`# KEY=`) 不算 —— 这正是我们想要的:
    被注释的字段应该走"没有值"的分支。
    """
    m = re.search(rf"^{re.escape(key)}=(.*)$", text, flags=re.MULTILINE)
    return m.group(1).strip() if m else ""


def set_value(text: str, key: str, value: str) -> str:
    """替换或追加 `KEY=value`。

    用正则替换而不是 sed —— 顺带绕开了 sed 的两个老问题: GNU/BSD 的 -i
    参数不兼容, 以及值里含 `|` `&` `\\` 时要转义 (转漏了就是 `unterminated
    's' command`, 现场看到的只有这一句, 完全指不到是哪个字段)。
    """
    pattern = rf"^{re.escape(key)}=.*$"
    if re.search(pattern, text, flags=re.MULTILINE):
        return re.sub(pattern, f"{key}={value}", text, count=1, flags=re.MULTILINE)
    sep = "" if text.endswith("\n") else "\n"
    return f"{text}{sep}{key}={value}\n"


def rand_hex(n: int) -> str:
    return secrets.token_hex(n)


def rand_fernet() -> str:
    """32 字节随机的 url-safe base64 —— Fernet key 的格式。"""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


def recover_from_backups(work: Path) -> dict[str, str]:
    """.env 没了但 .env.bak.* 还在时, 把持久化密钥捞回来。

    场景: 装机目录被清过 / 重新解包到别处 / 误删 .env, 而 pgdata 命名卷还在。
    postgres 的密码只在**首次建库**时写入, 新生成的随机密码连不上已有的库。

    ⚠ 只在备份里 PG_PASSWORD **只有一个不同值**时才自动沿用。有多个说明这个
    目录的历史复杂, 猜错会静默连错库 —— 那种情况必须人来判断, 所以只列出
    候选让人选, 不替人决定。
    """
    baks = sorted(work.glob(".env.bak.*"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not baks:
        return {}

    candidates = set()
    for b in baks:
        v = read_value(b.read_text(errors="replace"), "PG_PASSWORD")
        if v:
            candidates.add(v)

    if len(candidates) > 1:
        print(f"→ .env 不存在 · 备份里有 {len(candidates)} 个不同的 PG_PASSWORD · 不自动猜")
        print("  (猜错会连错库, 而且报错只在容器日志里。确认后手工写进 .env:)")
        for c in sorted(candidates):
            print(f"       PG_PASSWORD={c}")
        return {}
    if not candidates:
        return {}

    latest = baks[0].read_text(errors="replace")
    found = {k: read_value(latest, k) for k in PERSISTENT}
    found["PG_PASSWORD"] = candidates.pop()
    found = {k: v for k, v in found.items() if v}
    print(f"→ .env 不存在, 但从 {baks[0].name} 找回了: {', '.join(sorted(found))}")
    print("  (装机目录被清过? 这些值必须跟 pgdata 卷里的库一致, 否则服务全连不上)")
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description="生成/升级 .env")
    ap.add_argument("--dir", required=True, type=Path, help="装机目录 (含 .env.example)")
    ap.add_argument("--server-ip", required=True)
    ap.add_argument("--https", required=True, choices=["0", "1"])
    ap.add_argument("--https-port", default="443")
    ap.add_argument("--upgrade", default="0", choices=["0", "1"])
    ap.add_argument("--gateway-workers", default="")
    ap.add_argument("--identity-workers", default="")
    ap.add_argument("--identity-url", default="", help="显式指定的 CATFISH_IDENTITY_URL")
    ap.add_argument(
        "--pg-volume", default="",
        help="宿主机上已存在的 pgdata 卷名 (由 wrapper 查 docker volume ls 得到, "
             "没有就留空)。有卷但捞不到旧密码时会拒绝继续 —— 见 main 里那段。",
    )
    args = ap.parse_args()

    work: Path = args.dir
    env_path, example = work / ".env", work / ".env.example"

    # ── 1. 先把旧值捞出来, 再动 .env ─────────────────────────
    #
    # 顺序是这段代码的全部要害。8/1 和 8/4 那两次事故都是"覆盖在先、保护在
    # 后": 保护逻辑读的是 cp 之后的 .env, 而那时候值已经被 .env.example 的
    # 空值盖掉了, 于是每次重跑都判空、都重新生成, 那句"已有值·保持不变"
    # 永远走不到。
    old: dict[str, str] = {}
    old_identity_url = ""
    if env_path.exists():
        stamp = int(time.time())
        shutil.copy2(env_path, work / f".env.bak.{stamp}")
        print(f"→ .env 已存在 · 备份到 .env.bak.{stamp}")
        cur = env_path.read_text(errors="replace")
        old = {k: read_value(cur, k) for k in PERSISTENT}
        old = {k: v for k, v in old.items() if v}
        old_identity_url = read_value(cur, "CATFISH_IDENTITY_URL")
    else:
        old = recover_from_backups(work)

    # ── 2. 底稿 ──────────────────────────────────────────────
    if args.upgrade == "1":
        if not env_path.exists():
            print("❌ UPGRADE=1 但目录里没有 .env · 拒绝按新装流程生成配置")
            print("   请回到原安装目录, 或确认旧 .env 已备份后再升级")
            return 1
        print("→ UPGRADE=1 · 保留现有 .env, 不从 .env.example 覆盖")
        text = env_path.read_text(errors="replace")
    else:
        if not example.exists():
            print(f"❌ 找不到 {example} · 请确认在装机目录里跑")
            return 1
        text = example.read_text(errors="replace")

    # ── 3. 把旧密钥写回去, 然后只补"确实还空着"的 ──────────────
    for k, v in old.items():
        text = set_value(text, k, v)

    generated: list[str] = []
    if not read_value(text, "PG_PASSWORD"):
        # fail-loud: 库卷还在却没有可沿用的密码 → 生成新的必然连不上。
        #
        # postgres 的 POSTGRES_PASSWORD **只在数据目录为空(首次 initdb)时生效**,
        # 已有的库照旧认旧密码。不拦的话脚本一路绿灯装完, 而 identity /
        # gateway / skills-hub / wiki-hub 四个服务全部连库认证失败, 报错只在
        # 容器日志里 —— IT 从装机输出上完全看不出是密码被换了。
        if args.pg_volume:
            print("")
            print(f"❌ 检测到既有数据库卷: {args.pg_volume}")
            print("   但 .env 和 .env.bak.* 里都没有可沿用的 PG_PASSWORD。")
            print("")
            print("   现在生成新密码连不上已有的库, 所以这里停下, 不往下装。")
            print("")
            print("   二选一:")
            print("     A. 保数据 — 从备份找回旧密码, 写进 .env 后重跑装机:")
            print("          grep -h '^PG_PASSWORD=' .env.bak.* | sort -u")
            print("        (Windows PowerShell:")
            print("          Select-String '^PG_PASSWORD=' .env.bak.* | ForEach-Object { $_.Line } | Sort-Object -Unique)")
            print("     B. 清库重来 — 删卷后重跑 (⚠ 库内数据全丢):")
            print("          docker compose down -v")
            print("")
            return 1
        text = set_value(text, "PG_PASSWORD", rand_hex(16))
        generated.append("PG_PASSWORD")
    if not read_value(text, "JWT_SIGNING_KEY"):
        text = set_value(text, "JWT_SIGNING_KEY", rand_hex(32))
        generated.append("JWT_SIGNING_KEY")
    if not read_value(text, "CATFISH_SECRET_KEY"):
        text = set_value(text, "CATFISH_SECRET_KEY", rand_fernet())
        generated.append("CATFISH_SECRET_KEY")

    # ── 4. 部署派生配置 —— 新装和升级都要写 ────────────────────
    #
    # 这些跟着 IP / HTTPS 模式走, 不是秘密, 每次都按当前参数刷新。
    # 升级时现场可能换了 IP 或从 HTTP 切到 HTTPS, 不刷新的话 OIDC 的 issuer
    # 会对不上, 表现为登录验签失败。
    # ⚠ HTTP 和 HTTPS 两种模式下, issuer 和 CORS origin **不是同一个地址**。
    #
    #   HTTPS: 443 由 web 容器自己扛, 前端和 OIDC 都走同一个入口
    #          → issuer = cors = https://IP  (非 443 端口时带上端口)
    #   HTTP:  没有统一入口, identity 和 web 各自暴露自己的端口
    #          → issuer = http://IP:8998   (identity)
    #            cors   = http://IP:5173   (web 开发端口)
    #
    # 9/22 重构时这里一度写成两者都等于 http://IP —— HTTPS 路径看不出问题
    # (那时两者本来就相同), 但 HTTP 模式下 issuer 指向一个没有 OIDC 的端口,
    # 登录会直接挂。而当时的 20 条回归测试**全是 HTTPS**, 没兜住。
    # 现在两种模式都有测试 (test_setup_env.sh 里的 HTTP 段)。
    port = (args.https_port or "443").strip() or "443"
    if args.https == "1":
        base = f"https://{args.server_ip}" + ("" if port == "443" else f":{port}")
        issuer_url, web_url = base, base
    else:
        issuer_url = f"http://{args.server_ip}:8998"
        web_url = f"http://{args.server_ip}:5173"

    text = set_value(text, "CATFISH_OIDC_ISSUER", issuer_url)
    text = set_value(text, "CATFISH_IDENTITY_ISSUER", issuer_url)
    text = set_value(text, "CATFISH_IDENTITY_CORS_ORIGINS", web_url)
    text = set_value(text, "CATFISH_ENABLE_HTTPS", args.https)
    text = set_value(text, "CATFISH_HTTPS_PORT", port)
    if args.gateway_workers:
        text = set_value(text, "GATEWAY_WORKERS", args.gateway_workers)
    if args.identity_workers:
        text = set_value(text, "IDENTITY_WORKERS", args.identity_workers)

    # CATFISH_IDENTITY_URL: 显式传入 > 现场原值 > 容器内默认。
    # 现场可能把它改成了别的 (比如走反代), 升级不该把那种改动冲掉。
    if args.identity_url:
        text = set_value(text, "CATFISH_IDENTITY_URL", args.identity_url)
    elif old_identity_url:
        text = set_value(text, "CATFISH_IDENTITY_URL", old_identity_url)
    else:
        text = set_value(text, "CATFISH_IDENTITY_URL", "http://identity:8998")

    # 占位符必须全部replace掉 —— 留一个 <server-ip> 在里面, 服务起得来但
    # 登录跳转会指到一个不存在的地址, 而且报错是浏览器给的, 不在我们日志里。
    text = text.replace("<server-ip>", args.server_ip)

    env_path.write_text(text)

    # ── 5. 回读核对 ──────────────────────────────────────────
    #
    # 不信刚才 write_text 的返回。这一步几毫秒, 换的是"装完才发现 .env 少了
    # 一个字段"不会发生 —— 那种问题在现场要靠翻容器日志才查得出来。
    back = env_path.read_text()
    missing = [k for k in PERSISTENT if not read_value(back, k)]
    if missing:
        print(f"❌ 回读核对失败: 写完之后这些字段还是空的: {', '.join(missing)}")
        return 1
    if "<server-ip>" in back:
        print("❌ 回读核对失败: .env 里还留着 <server-ip> 占位符")
        return 1
    for k, v in old.items():
        if read_value(back, k) != v:
            print(f"❌ 回读核对失败: {k} 跟重跑之前不一样了 —— 这会导致服务连不上已有数据")
            return 1

    if generated:
        print(f"→ 新生成: {', '.join(generated)}")
    kept = sorted(set(old) - set(generated))
    if kept:
        print(f"→ 保持不变 (改了会出事): {', '.join(kept)}")
    print(f"→ .env 已写入 · issuer {issuer_url} · 前端 {web_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
