#!/usr/bin/env python3
"""把 x86_64 Mach-O 的字符串表挪到 8 字节对齐 (10/14, Intel Mac 的 cryptography 现编轮子用)。

cryptography 49 起不再发 Intel Mac 的轮子, hermes v0.22 锁的 50.0.1 只能在打包机上现编。当前
打包机的 Xcode 27 链接器 (ld-27037) 交叉链接 x86_64 时, 把 LC_SYMTAB 的字符串表放在只按
4 字节对齐的位置, dyld 拒绝加载: ``mis-aligned LINKEDIT string pool``。-ld_classic /
-no_fixup_chains 不改变布局, strip 重写后照样不对齐, rust-lld 读不了 SDK 27 的 .tbd。

修法只覆盖一种布局, 不满足就拒绝改 (退出码 2):
  · 字符串表是文件 (也是 __LINKEDIT 段) 的最后一块, 后面没有任何数据
  · 没有 LC_CODE_SIGNATURE (x86_64 的 .so 不强制签名, 有签名的话补字节会让签名失效)
满足时: 在字符串表前插 4 个 0, stroff += 4, __LINKEDIT 的 filesize += 4 (vmsize 本来就按页向上
取整, 不够才加一页)。别的 load command 指的偏移都在字符串表之前, 不受影响。

    python3 macho_align_strtab.py <file.so>        # 原地改, 已对齐就什么都不做
"""
from __future__ import annotations

import struct
import sys

MH_MAGIC_64 = 0xFEEDFACF
LC_SEGMENT_64 = 0x19
LC_SYMTAB = 0x2
LC_CODE_SIGNATURE = 0x1D
PAGE = 0x1000


def fix(path: str) -> int:
    data = bytearray(open(path, "rb").read())
    if len(data) < 32:
        print(f"{path}: 太短, 不是 Mach-O", file=sys.stderr)
        return 2
    magic, _cpu, _sub, _ftype, ncmds, _size, _flags, _res = struct.unpack_from("<IiiIIIII", data, 0)
    if magic != MH_MAGIC_64:
        print(f"{path}: 不是 64 位 Mach-O (fat 包要先拆)", file=sys.stderr)
        return 2
    off = 32
    linkedit = symtab = None
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from("<II", data, off)
        if cmd == LC_CODE_SIGNATURE:
            print(f"{path}: 有代码签名, 不改", file=sys.stderr)
            return 2
        if cmd == LC_SEGMENT_64 and data[off + 8:off + 24].rstrip(b"\0") == b"__LINKEDIT":
            linkedit = off
        if cmd == LC_SYMTAB:
            symtab = off
        off += cmdsize
    if linkedit is None or symtab is None:
        print(f"{path}: 找不到 __LINKEDIT / LC_SYMTAB", file=sys.stderr)
        return 2
    _symoff, _nsyms, stroff, strsize = struct.unpack_from("<IIII", data, symtab + 8)
    if stroff % 8 == 0:
        print(f"{path}: 字符串表已对齐 (stroff={stroff:#x}), 不用改")
        return 0
    pad = 8 - stroff % 8
    seg_vmsize, seg_fileoff, seg_filesize = struct.unpack_from("<QQQ", data, linkedit + 32)
    if stroff + strsize != len(data) or seg_fileoff + seg_filesize != len(data):
        print(f"{path}: 字符串表不是文件最后一块 (stroff+strsize={stroff + strsize:#x}, 文件 {len(data):#x}), 不改",
              file=sys.stderr)
        return 2
    data[stroff:stroff] = b"\0" * pad
    struct.pack_into("<I", data, symtab + 16, stroff + pad)
    new_filesize = seg_filesize + pad
    new_vmsize = max(seg_vmsize, (new_filesize + PAGE - 1) // PAGE * PAGE)
    struct.pack_into("<QQQ", data, linkedit + 32, new_vmsize, seg_fileoff, new_filesize)
    open(path, "wb").write(data)
    print(f"{path}: 字符串表 {stroff:#x} → {stroff + pad:#x} (补 {pad} 字节)")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(fix(sys.argv[1]))
