#!/usr/bin/env python3
"""生成 identity 的 users.yaml / clients.yaml —— setup.sh 和 setup.ps1 共用。

跟 envgen.py 同一个理由: 这两个文件的生成规则有几条容易漏的不变量, 写两遍
必然跑偏一遍。

# users.yaml

交付 tar 里只带 users.yaml.example (真 hash 不进 tar, 是军规)。装机时用这个
镜像里的 bcrypt 算出真 hash 再写。identity 首次启动会 seed_pg_from_yaml_if_empty()
把 admin 灌进 PG; 之后一律从 PG 读, yaml 被忽略。

⚠ 已存在就**不动**。重跑装机把 users.yaml 盖回默认密码, 而 identity 这时已经
从 PG 读了, 于是文件里写的和实际能登的对不上 —— 排查时会一直盯着这个文件。

# clients.yaml

Companion 启动时用 client_credentials (client_id=hermes-cli) 向 identity 换
30 天 service token 给本机 hermes。identity 从 clients.yaml 认这个 client。

这个文件之前**整个被交付漏了** —— 打包排除真 clients.yaml 是对的, 但既没有
example 也没有生成步骤, 于是任何一台机器上这条链路都是 401 invalid_client。

# 怎么调

    docker run --rm -v <装机目录>:/work -v <tools>:/tools:ro \\
        catfish-identity:<tag> python3 /tools/seedgen.py --dir /work --admin-password <pw>

必须在 identity 镜像里跑 —— bcrypt 和 catfish_identity 都在那里面。

退出码: 0 = 成功 / 1 = 失败
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

DEFAULT_ADMIN_EMAIL = "admin@catfish.com"


def hash_password(pw: str) -> str:
    """用 identity 自己的 hash_password —— 必须跟它登录时的校验同一个实现。

    直接调 bcrypt 库也能算出"一个 hash", 但如果 identity 那边加了 pepper、
    换了 cost、或者改成别的算法, 自己算的就登不进去, 而错误是"密码不对",
    根本指不到这里。所以走它自己的函数。
    """
    try:
        from catfish_identity.users import hash_password as h  # type: ignore
    except ImportError:
        sys.stderr.write(
            "❌ 这个镜像里没有 catfish_identity。\n"
            "   seedgen.py 必须在 catfish-identity 镜像里跑 —— admin 的密码 hash\n"
            "   要用 identity 自己的实现算, 不能用别的 bcrypt 凑。\n"
        )
        raise SystemExit(1) from None
    return h(pw)


def write_users(cfg: Path, email: str, password: str) -> bool:
    """返回是否真的写了。"""
    target = cfg / "users.yaml"
    if target.exists():
        print("→ users.yaml 已存在 · skip (跨装机保留)")
        return False

    pw_hash = hash_password(password)
    body = (
        "# 装机时 seedgen.py 自动生成\n"
        "# admin 首次登进后立即改密 (Companion 内建改密 UI · 或 sysadmin 面板)\n"
        "users:\n"
        f"  - email: {email}\n"
        f"    password_hash: {pw_hash}\n"
        "    name: 系统管理员\n"
        "    department: IT\n"
        "    role: sysadmin\n"
    )
    # 密码 hash 不该是 644。用 os.open 带 mode 创建, 从第一个字节起就是 600 ——
    # 先写再 chmod 会留一个短暂的可读窗口。
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(body)
    print(f"→ users.yaml 生成 · sysadmin: {email} / {password}")
    print("  ⚠ 首次登进立即改密")
    return True


def write_clients(cfg: Path) -> None:
    target, example = cfg / "clients.yaml", cfg / "clients.yaml.example"
    if target.exists():
        print("→ clients.yaml 已存在 · skip (跨装机保留)")
        return
    if not example.exists():
        print("❌ 缺 clients.yaml.example · 交付包不完整")
        print("   (Companion 的 hermes service token 链路会全挂 invalid_client)")
        print("   重新解包, 或联系交付方。")
        raise SystemExit(1)
    shutil.copy2(example, target)
    print("→ clients.yaml 生成 (hermes-cli · Companion service token 用)")


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 identity 的 users.yaml / clients.yaml")
    ap.add_argument("--dir", required=True, type=Path, help="装机目录")
    ap.add_argument("--admin-email", default=DEFAULT_ADMIN_EMAIL)
    ap.add_argument("--admin-password", default="catfish_2026")
    args = ap.parse_args()

    cfg = args.dir / "identity-server" / "config"
    cfg.mkdir(parents=True, exist_ok=True)

    wrote = write_users(cfg, args.admin_email, args.admin_password)
    write_clients(cfg)

    # 回读核对 —— 生成了却没落到盘上, 后果是 identity 起来后一个用户都没有,
    # 而装机脚本会一路绿灯装完。
    if wrote:
        back = (cfg / "users.yaml").read_text()
        if args.admin_email not in back or "password_hash:" not in back:
            print("❌ 回读核对失败: users.yaml 里没有 admin 或没有 password_hash")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
