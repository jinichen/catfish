#!/usr/bin/env python3
"""客户给的正式证书 (域名用) —— 装机时校验, 合格才拷进 certs/official/。

11/08 (IP + 域名同时用)。跟 certgen.py 一样**在 identity 镜像里跑** (要 cryptography,
Windows 宿主机没有 openssl):

    docker run --rm -v <证书>:/in/fullchain.pem:ro -v <私钥>:/in/privkey.pem:ro \\
        -v <装机目录>/certs:/certs -v <tools>:/tools:ro catfish-identity:<tag> \\
        python3 /tools/certcheck.py --cert /in/fullchain.pem --key /in/privkey.pem \\
        --public-hosts "<CATFISH_PUBLIC_HOSTS>" --out /certs

不带 --out = 只检查 (升级时核对 certs/official/ 里已经在用的那份, 加 --existing)。

# 查什么 (任何一条不过 → 退出码 1, 不拷, 说清楚怎么办)

  · 证书 / 私钥读得开 (私钥不能带密码 —— nginx 启动时没人输密码)
  · 私钥跟证书是一对
  · 证书覆盖 PUBLIC_HOST 里的**每一个域名** (SAN, 支持 *.example.com 通配)
  · 没过期、已生效
  · PUBLIC_HOST 里至少有一个域名 (公网 CA 不给 IP 签证书; IP 项照旧走内部证书)

# 只提醒、不拦

  · 只有服务器证书没有中间证书 (部分手机 / Java 客户端会验不过 —— 要 fullchain)
  · 30 天内到期 (正式证书**不会**自动续)
  · 中间证书顺序不对 / 看起来是自签的
  · --existing (升级时核对旧证书) 时, 过期也只提醒: 那份证书已经在用了, 停下装机修不好它

renew-cert 从不碰 certs/official/ —— 正式证书由客户自己的 CA 续, 换证书就是带
TLS_CERT / TLS_KEY 重跑一次装机。
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
except ImportError:  # pragma: no cover
    sys.stderr.write("❌ 这个镜像里没有 cryptography —— certcheck.py 要在 catfish-identity 镜像里跑\n")
    sys.exit(1)

from publichosts import PublicHostError, parse_list
from publichosts import domains as public_domains
from publichosts import ips as public_ips

WARN_DAYS = 30


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def name_matches(pattern: str, host: str) -> bool:
    """证书 SAN 里的名字是否覆盖 host。通配符只管一层 (*.a.com 覆盖 x.a.com, 不覆盖 a.com / y.x.a.com)。"""
    pattern, host = pattern.lower().rstrip("."), host.lower().rstrip(".")
    if pattern == host:
        return True
    if pattern.startswith("*."):
        suffix = pattern[1:]
        return host.endswith(suffix) and "." not in host[: -len(suffix)] and len(host) > len(suffix)
    return False


def _pub_der(key) -> bytes:
    return key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)


def check(cert_pem: bytes, key_pem: bytes, domains: list[str], *, existing: bool = False) -> tuple[list[str], list[str], x509.Certificate | None]:
    """返回 (错误, 提醒, 服务器证书)。错误非空 = 不合格。"""
    errors: list[str] = []
    warns: list[str] = []
    try:
        chain = x509.load_pem_x509_certificates(cert_pem)
    except ValueError:
        chain = []
    if not chain:
        return ["证书文件里没有 PEM 格式的证书 (应以 -----BEGIN CERTIFICATE----- 开头; .pfx / .der 要先转成 PEM)"], [], None
    leaf = chain[0]

    try:
        key = serialization.load_pem_private_key(key_pem, password=None)
    except TypeError:
        errors.append("私钥带了密码。nginx 启动时没人输密码, 要无密码的私钥: openssl rsa -in 带密码.key -out privkey.pem")
        key = None
    except ValueError:
        errors.append("私钥文件读不开 (不是 PEM 私钥? 是不是把证书当成私钥传了)")
        key = None
    if key is not None and _pub_der(key.public_key()) != _pub_der(leaf.public_key()):
        errors.append("私钥跟证书不是一对 (证书文件第一张是服务器证书, 私钥要用申请这张证书时生成的那把)")

    try:
        san = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        names = [n.lower() for n in san.get_values_for_type(x509.DNSName)]
    except x509.ExtensionNotFound:
        names = []
    if not domains:
        errors.append("PUBLIC_HOST 里没有域名 —— 正式证书只给域名用 (公网 CA 不给 IP 签证书), IP 走内部证书")
    for d in domains:
        if not any(name_matches(n, d) for n in names):
            errors.append(f"证书不包含域名 {d} (证书上的名字: {', '.join(names) or '无'})")

    now = _now()
    left = (leaf.not_valid_after_utc - now).days
    if leaf.not_valid_after_utc <= now:
        msg = f"证书已于 {leaf.not_valid_after_utc:%Y-%m-%d} 过期"
        (warns if existing else errors).append(msg + (" —— 用域名打开会报证书错误, 尽快换新证书" if existing else ""))
    elif leaf.not_valid_before_utc > now:
        errors.append(f"证书 {leaf.not_valid_before_utc:%Y-%m-%d} 才生效 (服务器时间对吗?)")
    elif left < WARN_DAYS:
        warns.append(f"证书 {left} 天后 ({leaf.not_valid_after_utc:%Y-%m-%d}) 到期 —— 正式证书不会自动续, 请尽快向证书供应商续期")

    if len(chain) == 1:
        if leaf.issuer == leaf.subject:
            warns.append("这是一张自签证书, 不是公网 CA 签的 —— 浏览器照样会报不受信任 (想用内部证书就不用传 TLS_CERT)")
        else:
            warns.append("证书文件里只有服务器证书, 没有中间证书。电脑浏览器多半没事, 部分手机 / 老客户端会验不过 —— 用证书供应商给的 fullchain (完整链) 文件")
    else:
        for a, b in zip(chain, chain[1:]):
            if a.issuer != b.subject:
                warns.append("证书链顺序看起来不对 (应是 服务器证书 → 中间证书 → …), 部分客户端会验不过")
                break
    return errors, warns, leaf


def _write(path: Path, data: bytes, *, secret: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if secret else 0o644)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def main() -> int:
    ap = argparse.ArgumentParser(description="校验正式证书 (域名用), 合格才拷进 certs/official/")
    ap.add_argument("--cert", required=True, type=Path, help="fullchain (服务器证书 + 中间证书), PEM")
    ap.add_argument("--key", required=True, type=Path, help="私钥, PEM, 不带密码")
    ap.add_argument("--public-hosts", required=True, help=".env 的 CATFISH_PUBLIC_HOSTS 原样")
    ap.add_argument("--out", type=Path, help="certs 目录; 给了就拷到 <out>/official/, 不给只检查")
    ap.add_argument("--existing", action="store_true", help="核对已经在用的那份 (过期只提醒)")
    args = ap.parse_args()

    try:
        public = parse_list(args.public_hosts)
    except PublicHostError as e:
        print(f"❌ {e}")
        return 1
    domains = public_domains(public)
    try:
        cert_pem, key_pem = args.cert.read_bytes(), args.key.read_bytes()
    except OSError as e:
        print(f"❌ 证书 / 私钥文件读不了: {e}")
        print("   路径对吗? 私钥一般只有 root 能读 —— 用 sudo 跑装机, 或者先把两个文件拷到装机目录里")
        return 1

    errors, warns, leaf = check(cert_pem, key_pem, domains, existing=args.existing)
    what = "certs/official/ 里的正式证书" if args.existing else "TLS_CERT / TLS_KEY 给的正式证书"
    if errors:
        print(f"❌ {what}不能用:")
        for e in errors:
            print(f"   · {e}")
        if not args.existing:
            print("   什么都没改 (certs/ 原样)。修好证书后重跑; 不用正式证书就去掉 TLS_CERT / TLS_KEY (域名会走内部证书)。")
        else:
            print("   二选一: 带新证书重跑 (UPGRADE=1 TLS_CERT=… TLS_KEY=… bash setup.sh),")
            print("           或者删掉 certs/official/ 改用内部证书 (员工要装内部 CA, 用接入码)。")
        return 1
    for w in warns:
        print(f"  ⚠ {w}")
    left = (leaf.not_valid_after_utc - _now()).days
    print(f"  ✓ 正式证书可用 · 覆盖 {', '.join(domains)} · {leaf.not_valid_after_utc:%Y-%m-%d} 到期 (还有 {left} 天)")
    if public_ips(public):
        print(f"  ⓘ {', '.join(public_ips(public))} 是 IP, 公网 CA 不给 IP 签证书 —— 这些地址仍用内部证书 (员工用接入码装内部 CA)")
    if args.out:
        _write(args.out / "official" / "fullchain.pem", cert_pem, secret=False)
        _write(args.out / "official" / "privkey.pem", key_pem, secret=True)
        print("  ✓ 已拷到 certs/official/fullchain.pem + privkey.pem (自动续期不会碰它们)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
