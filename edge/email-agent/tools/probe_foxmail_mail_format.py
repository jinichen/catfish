"""探测 Foxmail 7.x 邮件文件到底是什么编码 —— 一次性诊断工具, 不进产品路径。

# 为什么需要这个 (9/18)

真机 `Mails/0/0/6144` 前 32 字节::

    CD 50 4F 22 FB 95 37 4D 2B 62 5E E2 9D 99 09 67
    EF 24 F1 22 92 68 05 B1 02 16 CA 08 9A 1C 7C 34

高熵, 无可读 ASCII, 不是已知压缩魔数。也就是说 7.x 的邮件文件不是明文
RFC822 —— 目录/索引/文件夹那几个 bug 修完之后, 还是读不出内容。

与其再猜一次 (今天已经因为猜布局踩了五个同族 bug), 不如把可能性逐个证伪。
这个脚本只读文件、只打印统计和判断, 不写任何东西。

# 用法

    python probe_foxmail_mail_format.py <账号目录>

例如::

    python probe_foxmail_mail_format.py "E:\\nextcloud\\mailstore\\ffchenhb@chinatelecom.cn"

# 隐私

脚本只输出统计量、十六进制片段和"是否命中"结论。命中明文时最多打印 200 字节
预览 —— 那是判断成败的唯一依据。输出会包含邮件片段, 贴给别人前自己看一眼。
"""
from __future__ import annotations

import bz2
import lzma
import math
import sys
import zlib
from collections import Counter
from pathlib import Path

#: 每封邮件都该有的明文片段, 用来做 crib drag
CRIBS = [
    b"Content-Type: ",
    b"Received: ",
    b"Message-ID: ",
    b"MIME-Version: 1.0",
    b"Content-Transfer-Encoding: ",
]
#: 判定"解出来了"的特征词
MARKERS = [b"Content-Type", b"Received:", b"Subject:", b"From:", b"charset="]
SAMPLE_BYTES = 8192


def entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = Counter(data)
    total = len(data)
    return -sum((c / total) * math.log2(c / total) for c in counts.values())


def looks_like_mail(data: bytes) -> bool:
    return sum(1 for m in MARKERS if m in data) >= 2


def report(title: str) -> None:
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


# ── 1. 基本面 ────────────────────────────────────────────────


def probe_entropy(data: bytes) -> None:
    report("1. 熵与字节分布")
    ent = entropy(data[:SAMPLE_BYTES])
    print(f"前 {min(len(data), SAMPLE_BYTES)} 字节熵 = {ent:.3f} bits/byte")
    if ent > 7.5:
        print("  → 接近 8: 压缩或加密过的数据")
    elif ent > 6.0:
        print("  → 偏高: 可能压缩, 也可能是 base64/二进制附件占主体")
    else:
        print("  → 偏低: 像是明文或简单变换过的明文")
    top = Counter(data[:SAMPLE_BYTES]).most_common(5)
    print("最常见字节:", ", ".join(f"0x{b:02X}×{n}" for b, n in top))
    print("  (明文英文里 0x20 空格 应该遥遥领先; 异或固定密钥会把它挪到别处)")


# ── 2. 压缩 ─────────────────────────────────────────────────


def probe_compression(data: bytes) -> bool:
    report("2. 是不是压缩")
    magics = {
        b"\x1f\x8b": "gzip", b"\x78\x01": "zlib(1)", b"\x78\x9c": "zlib(9c)",
        b"\x78\xda": "zlib(da)", b"BZh": "bzip2", b"\xfd7zXZ": "xz", b"PK\x03\x04": "zip",
    }
    for off in range(0, min(256, len(data))):
        for magic, name in magics.items():
            if data[off:off + len(magic)] == magic:
                print(f"  偏移 {off} 发现 {name} 魔数")
    for off in range(0, min(128, len(data))):
        for wbits, label in ((15, "zlib"), (-15, "raw deflate"), (31, "gzip")):
            try:
                out = zlib.decompressobj(wbits).decompress(data[off:])
            except Exception:  # noqa: BLE001, S112
                continue
            if len(out) > 64:
                print(f"  ✓ 偏移 {off} 用 {label} 解出 {len(out)} 字节")
                if looks_like_mail(out):
                    print("  ★ 解出来的是邮件明文:")
                    print(out[:200].decode("utf-8", "replace"))
                    return True
    for name, fn in (("bz2", bz2.decompress), ("lzma", lzma.decompress)):
        try:
            out = fn(data)
        except Exception:  # noqa: BLE001
            continue
        if looks_like_mail(out):
            print(f"  ★ {name} 解出邮件明文")
            print(out[:200].decode("utf-8", "replace"))
            return True
    print("  ✗ 常见压缩格式都不是")
    return False


# ── 3. 单字节异或 ───────────────────────────────────────────


def probe_single_xor(data: bytes) -> bool:
    report("3. 是不是单字节异或")
    head = data[:SAMPLE_BYTES]
    for key in range(1, 256):
        out = bytes(b ^ key for b in head)
        if looks_like_mail(out):
            print(f"  ★ 密钥 0x{key:02X} 解出邮件明文:")
            print(out[:200].decode("utf-8", "replace"))
            return True
    print("  ✗ 256 个单字节密钥都不是")
    return False


# ── 4. 重复密钥异或 (crib drag) ─────────────────────────────


def probe_repeating_xor(data: bytes) -> bool:
    """用已知明文片段反推密钥。

    重复密钥异或的弱点: 密文 XOR 已知明文 = 该位置的密钥流。如果密钥是周期性
    重复的, 反推出来的片段会自我重复 —— 那个重复周期就是密钥长度。
    """
    report("4. 是不是重复密钥异或 (用已知明文反推)")
    head = data[:SAMPLE_BYTES]
    for crib in CRIBS:
        for pos in range(0, len(head) - len(crib)):
            stream = bytes(head[pos + i] ^ crib[i] for i in range(len(crib)))
            # 密钥流自我重复 → 找到周期
            for period in range(1, len(crib) // 2 + 1):
                if all(stream[i] == stream[i % period] for i in range(len(stream))):
                    key = stream[:period]
                    if len(set(key)) == 1 and key[0] == 0:
                        continue  # 全零 = 明文本来就等于 crib, 前面已验过
                    out = bytes(
                        head[i] ^ key[(i - pos) % period] for i in range(len(head))
                    )
                    if looks_like_mail(out):
                        print(f"  ★ 密钥长度 {period}, 密钥 {key.hex()}, 命中位置 {pos}")
                        print(out[:200].decode("utf-8", "replace"))
                        return True
    print("  ✗ 反推不出周期性密钥 (不是简单的重复密钥异或)")
    return False


# ── 5. 两封互异或 ───────────────────────────────────────────


def probe_cross_xor(a: bytes, b: bytes) -> None:
    """两封邮件互相异或。

    如果两封用的是同一段密钥流 (固定密钥 / 复用 IV), 异或之后密钥流抵消,
    剩下的是两份明文的异或 —— 那东西的熵会明显低于随机, 而且
    "明文 XOR 明文" 在两边同为空格/同为字母时会露出规律。
    """
    report("5. 两封邮件互相异或 (看密钥流是否复用)")
    n = min(len(a), len(b), SAMPLE_BYTES)
    if n < 256:
        print("  样本太短, 跳过")
        return
    x = bytes(a[i] ^ b[i] for i in range(n))
    ent = entropy(x)
    zeros = x.count(0)
    print(f"  异或结果熵 = {ent:.3f}, 零字节 {zeros}/{n} ({zeros / n:.1%})")
    if ent < 7.0 or zeros > n * 0.02:
        print("  → 熵掉下来了 / 零字节偏多: 密钥流很可能被复用, 这条线值得深挖")
    else:
        print("  → 仍然接近随机: 每封的密钥流不同 (或者根本不是异或)")


# ── 6. Mime/Decode.rec0 ─────────────────────────────────────


def probe_decode_cache(account: Path) -> None:
    """Foxmail 的解码缓存里有没有明文。

    账号目录下有 ``Mime/Decode.map`` + ``Decode.rec0`` (实测 8 MB)。名字叫
    Decode, 有可能存的是**解码后**的内容 —— 真是那样的话, 我们根本不用去碰
    邮件文件的加密。
    """
    report("6. Mime/Decode.rec0 里有没有明文")
    rec = account / "Mime" / "Decode.rec0"
    if not rec.is_file():
        print(f"  没有 {rec}")
        return
    with rec.open("rb") as handle:
        chunk = handle.read(1 << 20)
    print(f"  大小 {rec.stat().st_size} 字节, 前 1MB 熵 = {entropy(chunk):.3f}")
    hits = [m for m in MARKERS if m in chunk]
    if hits:
        print(f"  ★ 前 1MB 里出现明文特征: {[h.decode() for h in hits]}")
        idx = chunk.find(hits[0])
        print(chunk[max(0, idx - 100):idx + 200].decode("utf-8", "replace"))
    else:
        print("  ✗ 前 1MB 没有明文邮件特征")


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    account = Path(sys.argv[1])
    mails = sorted(
        (p for p in (account / "Mails").rglob("*") if p.is_file() and p.name.isdigit()),
        key=lambda p: p.stat().st_size,
    )
    if not mails:
        print(f"{account / 'Mails'} 下没找到邮件文件")
        return 1

    print(f"找到 {len(mails)} 封邮件文件, 取最小的两封做样本")
    first = mails[0]
    data = first.read_bytes()
    print(f"样本: {first}  ({len(data)} 字节)")
    print("前 32 字节:", data[:32].hex(" ").upper())

    report("0. 会不会本来就是明文")
    if looks_like_mail(data[:SAMPLE_BYTES]):
        print("  ★ 直接就是 RFC822 明文, 不需要解码")
        print(data[:200].decode("utf-8", "replace"))
        return 0
    print("  ✗ 开头没有邮件头特征")

    probe_entropy(data)
    if probe_compression(data):
        return 0
    if probe_single_xor(data):
        return 0
    if probe_repeating_xor(data):
        return 0
    if len(mails) > 1:
        probe_cross_xor(data, mails[1].read_bytes())
    probe_decode_cache(account)

    report("结论")
    print("以上都没命中 = 不是压缩、不是简单异或。剩下的可能:")
    print("  · Foxmail 自己的分组密码 (公开逆向项目里有过, 需要照着实现)")
    print("  · 绑定账号口令的真加密 —— 那样本地解不开, 只能走 IMAP")
    print("把这份输出贴回来, 我据此判断下一步。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
