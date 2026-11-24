#!/usr/bin/env python3
"""把一个目录打成「同样内容 → 逐字节一样」的 .tar.gz (11/25)。

为什么: catfish-email / catfish-wechat-reader 的安装包装到员工机上时, 按归档的指纹判断「装的是不是这一份」
(src-tauri hermes_install_steps::addon_current)。原来用 `tar czf` 打 —— 文件修改时间、遍历顺序、gzip 头里的打包时刻
每次都不一样, 代码一个字没改指纹也变: 每出一版都重装一次; 开发版和安装版共用 ~/.hermes, 两边来回切每次都重装。

做法: 文件按路径排序, 时间 / 属主 / 权限统一 (目录 755, 文件 644, 可执行的 755), gzip 头的时间写 0。

用法: repro_tar.py <源目录> <输出.tar.gz>    (源目录里的内容放在归档根下, 跟 `tar czf OUT -C SRC .` 一样)
"""
import gzip
import io
import os
import sys
import tarfile


def _norm(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    if info.isdir():
        info.mode = 0o755
    else:
        info.mode = 0o755 if info.mode & 0o111 else 0o644
    return info


def build(src: str, out: str) -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        entries = []
        for root, dirs, files in os.walk(src):
            dirs.sort()
            for name in dirs + sorted(files):
                full = os.path.join(root, name)
                entries.append((os.path.relpath(full, src), full))
        for rel, full in sorted(entries):
            tar.add(full, arcname="./" + rel, recursive=False, filter=_norm)
    tmp = out + ".tmp"
    with open(tmp, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=9) as gz:
        gz.write(buf.getvalue())
    os.replace(tmp, out)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    build(sys.argv[1], sys.argv[2])
