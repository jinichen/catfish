#!/usr/bin/env python3
"""自签证书体系 —— 在**容器里**跑, 不依赖宿主机装了什么。

9/22 从 setup.sh 里的一串 openssl 命令改过来。

# 为什么不继续用 openssl

原来的注释写着 "openssl 装了 docker 的机器上都有" —— 在 Linux / macOS 上
成立, 在 **Windows 上不成立**。Windows 既没有 openssl 也没有 /dev/urandom,
这是中央端一直没法在 Windows 上照着装的原因之一。

`cryptography` 是 identity-server 的**直接依赖** (pyproject 里声明的
cryptography>=50.0.0), 所以它一定在镜像里 —— 不用赌 slim 镜像带不带
openssl 命令行。调用方式三个平台完全一样:

    docker run --rm -v <宿主 certs 目录>:/certs -v <宿主 tools 目录>:/tools:ro \\
        catfish-identity:<tag> python3 /tools/certgen.py --ip <IP> --out /certs

顺带修掉一处老毛病: openssl < 1.1.1 不支持 `-addext`, 现场撞到过, 原脚本
只能靠注释提醒人去查 `openssl version`。这里没有这个变量。

# 证书体系 (跟原来完全一致, 不是重新设计)

    ca.pem / ca-key.pem   内部 CA, 10 年   ← 发给员工机器装信任库, 只装一次
    key.pem               服务器私钥
    server.pem            服务器证书, 397 天, 由 CA 签发
    cert.pem              fullchain = server.pem + ca.pem, nginx 用

397 天不是随便取的: macOS / iOS 的 Security.framework 拒绝有效期超过 398 天
的 TLS 服务器证书, 而且**跟受不受信任无关** —— 装进信任库照样拒。老做法那张
10 年自签证书浏览器点"继续前往"能用, 但 Companion 桌面端直接连不上。

# 什么时候重签

CA: **只在不存在时生成**。重新生成 CA 会让所有已经发到员工机器上的 ca.pem
    立刻失效, 而那些机器不会自动更新 —— 等于要挨台重装一遍。

服务器证书: 缺失 / SAN 里没有当前 IP / 30 天内到期, 三者之一才重签。
    CA 不动, 所以员工机器上的 ca.pem 不用跟着换。

退出码: 0 = 成功 (不管有没有重签) / 1 = 失败
"""
from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import os
import sys
from pathlib import Path

try:
    from cryptography import x509
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
except ImportError:  # pragma: no cover - 只会在跑错镜像时发生
    sys.stderr.write(
        "❌ 这个镜像里没有 cryptography。\n"
        "   certgen.py 必须在 catfish-identity 镜像里跑 —— 它把 cryptography\n"
        "   列为直接依赖。如果你是在宿主机上直接执行这个文件, 那就是用法错了,\n"
        "   正确调用见本文件顶部的注释。\n"
    )
    sys.exit(1)

CA_DAYS = 3650
# 397 < 398, 留一天余量。见文件顶部那段。
CERT_DAYS = 397
RENEW_WITHIN_DAYS = 30


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _write(path: Path, data: bytes, *, secret: bool) -> None:
    """写文件, 私钥收权限。

    先写再 chmod 会有一个短暂的窗口私钥是 644 的。窗口很短但没必要留 ——
    用 os.open 带 mode 创建, 从第一个字节起就是 600。
    """
    if secret:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    else:
        path.write_bytes(data)


def _new_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _key_bytes(key: rsa.RSAPrivateKey) -> bytes:
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _san_entries(ip: str) -> list[x509.GeneralName]:
    """SAN 列表。

    ⚠ 现代浏览器**只看 SAN, 完全忽略 CN**。早年只填 CN 的证书在今天的
    Chrome / Safari 上一律报 ERR_CERT_COMMON_NAME_INVALID —— 这就是原脚本
    里那句 `-addext subjectAltName=...` 存在的理由, 不是可选项。
    """
    names: list[x509.GeneralName] = [
        x509.IPAddress(ipaddress.ip_address(ip)),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        x509.DNSName("localhost"),
    ]
    return names


def ensure_ca(out: Path) -> tuple[x509.Certificate, rsa.RSAPrivateKey, bool]:
    ca_pem, ca_key_pem = out / "ca.pem", out / "ca-key.pem"
    if ca_pem.exists() and ca_key_pem.exists():
        cert = x509.load_pem_x509_certificate(ca_pem.read_bytes())
        key = serialization.load_pem_private_key(ca_key_pem.read_bytes(), password=None)
        return cert, key, False  # type: ignore[return-value]

    key = _new_key()
    subject = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "CN"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Catfish"),
        x509.NameAttribute(NameOID.COMMON_NAME, "Catfish Internal CA"),
    ])
    now = _utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))  # 容忍机器间时钟偏差
        .not_valid_after(now + dt.timedelta(days=CA_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, key_cert_sign=True, crl_sign=True,
                content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    _write(ca_pem, cert.public_bytes(serialization.Encoding.PEM), secret=False)
    _write(ca_key_pem, _key_bytes(key), secret=True)
    return cert, key, True


def server_cert_is_fine(out: Path, ip: str) -> tuple[bool, str]:
    """现有服务器证书还能不能用。返回 (能用, 原因)。"""
    server_pem, key_pem = out / "server.pem", out / "key.pem"
    if not server_pem.exists() or not key_pem.exists():
        return False, "还没有服务器证书"
    try:
        cert = x509.load_pem_x509_certificate(server_pem.read_bytes())
    except ValueError:
        return False, "现有 server.pem 解不开 (文件坏了)"

    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        ips = {str(v) for v in san.get_values_for_type(x509.IPAddress)}
    except x509.ExtensionNotFound:
        return False, "现有证书没有 SAN (浏览器只认 SAN, 不看 CN)"
    if ip not in ips:
        return False, f"现有证书的 SAN 里没有 {ip} (SAN: {', '.join(sorted(ips)) or '空'})"

    not_after = cert.not_valid_after_utc
    left = (not_after - _utcnow()).days
    if left < RENEW_WITHIN_DAYS:
        return False, f"现有证书 {left} 天后到期 (阈值 {RENEW_WITHIN_DAYS} 天)"
    return True, f"现有证书可用, 还有 {left} 天"


def issue_server_cert(out: Path, ip: str, ca_cert: x509.Certificate, ca_key: rsa.RSAPrivateKey) -> None:
    key = _new_key()
    now = _utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Catfish"),
            x509.NameAttribute(NameOID.COMMON_NAME, ip),
        ]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=CERT_DAYS))
        .add_extension(x509.SubjectAlternativeName(_san_entries(ip)), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, key_encipherment=True,
                content_commitment=False, data_encipherment=False,
                key_agreement=False, key_cert_sign=False, crl_sign=False,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    server_pem = cert.public_bytes(serialization.Encoding.PEM)
    _write(out / "server.pem", server_pem, secret=False)
    _write(out / "key.pem", _key_bytes(key), secret=True)
    # nginx 要 fullchain: 服务器证书在前, CA 在后。顺序反了部分客户端会验不过。
    _write(
        out / "cert.pem",
        server_pem + ca_cert.public_bytes(serialization.Encoding.PEM),
        secret=False,
    )


def verify(out: Path, ip: str) -> None:
    """独立回读核对 —— 不信刚才写入时的返回值, 重新从磁盘读一遍。

    写成功不等于写对了。这一步多花几毫秒, 换的是"装完才发现证书是废的"
    不会发生: 现场重来一次的代价是几十分钟。
    """
    ca = x509.load_pem_x509_certificate((out / "ca.pem").read_bytes())
    srv = x509.load_pem_x509_certificate((out / "server.pem").read_bytes())

    if srv.issuer != ca.subject:
        raise SystemExit("❌ 回读核对失败: server.pem 的签发者不是 ca.pem")

    # 用 CA 的公钥验服务器证书的签名 —— 等价于 openssl verify -CAfile
    #
    # 裸的 InvalidSignature traceback 对现场没有任何用处 (实测过), 所以接住它
    # 说人话。这种情况最常见的来由是 certs/ 目录被两次装机混了: ca.pem 是新的,
    # server.pem 还是上一套 CA 签的。
    try:
        ca.public_key().verify(
            srv.signature,
            srv.tbs_certificate_bytes,
            padding.PKCS1v15(),
            srv.signature_hash_algorithm,
        )
    except InvalidSignature:
        raise SystemExit(
            "❌ 回读核对失败: server.pem 的签名用 ca.pem 验不过。\n"
            "   多半是 certs/ 目录里混了两套证书 (ca.pem 换过, 服务器证书还是旧 CA 签的)。\n"
            "   修法: 确认没有员工机器已经装了当前这张 ca.pem, 然后删掉整个 certs/ 重跑装机;\n"
            "        若已经发出去过, 改为只删 server.pem / key.pem / cert.pem, 保住 CA。"
        ) from None

    san = srv.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    ips = {str(v) for v in san.get_values_for_type(x509.IPAddress)}
    if ip not in ips:
        raise SystemExit(f"❌ 回读核对失败: SAN 里没有 {ip}")

    fullchain = (out / "cert.pem").read_bytes()
    if not fullchain.startswith(srv.public_bytes(serialization.Encoding.PEM)):
        raise SystemExit("❌ 回读核对失败: cert.pem 开头不是服务器证书 (fullchain 顺序错了)")

    left = (srv.not_valid_after_utc - _utcnow()).days
    print(f"  ✓ 回读核对通过 · SAN 含 {ip} · 由内部 CA 签发 · {left} 天后到期")


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 Catfish 自签证书体系")
    ap.add_argument("--ip", required=True, help="服务器 IP, 会写进 SAN")
    ap.add_argument("--out", required=True, type=Path, help="输出目录 (容器内路径)")
    args = ap.parse_args()

    try:
        ipaddress.ip_address(args.ip)
    except ValueError:
        sys.stderr.write(f"❌ --ip 不是合法 IP: {args.ip!r}\n")
        return 1

    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)

    ca_cert, ca_key, ca_created = ensure_ca(out)
    if ca_created:
        print(f"  ✓ 新建内部 CA · ca.pem / ca-key.pem · {CA_DAYS} 天")
    else:
        print("  · 内部 CA 已存在, 不动 (重建会让员工机器上已装的 ca.pem 全部失效)")

    fine, why = server_cert_is_fine(out, args.ip)
    if fine:
        print(f"  · 服务器证书不用换: {why}")
    else:
        print(f"  → 重签服务器证书: {why}")
        issue_server_cert(out, args.ip, ca_cert, ca_key)
        print(f"  ✓ cert.pem (fullchain) / key.pem / server.pem · {CERT_DAYS} 天")

    verify(out, args.ip)
    return 0


if __name__ == "__main__":
    sys.exit(main())
