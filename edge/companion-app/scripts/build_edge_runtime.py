#!/usr/bin/env python3
"""打 catfish-edge-runtime.tar.gz —— tool-bridge / local-search 的 Python 源码 (9/23).

Companion 在客户机器上把它解到 ~/.catfish/edge-runtime/ (services/edge_runtime.rs)。
在这之前两个平台的安装包都没带这两个组件, Companion 按源码树去找, 客户机器
上永远找不到 —— 见 edge_runtime.rs 文件头。

mac (build-mac-resources.sh) 和 Windows (build-msi-local.ps1 / CI workflow) 调的
是**同一个脚本**, 所以两边包里的内容一字不差。

    python3 build_edge_runtime.py <输出 .tar.gz 路径>

只收 src/ 下的 .py 和数据文件; 排掉 __pycache__ / *.egg-info / tests。
tar 里的条目按路径排序、mtime 归零 —— 同一份源码打两次 sha256 相同, Companion
据此判断"要不要重新解" (内容没变就不动员工机器上的文件)。
"""
from __future__ import annotations

import gzip
import io
import sys
import tarfile
from pathlib import Path

EDGE = Path(__file__).resolve().parents[2]  # edge/
COMPONENTS = {
    "tool-bridge": EDGE / "tool-bridge" / "src",
    "local-search": EDGE / "local-search" / "src",
}
# Companion 解完会检查这两个路径在不在 —— 两边的清单要对得上
MUST_EXIST = ["tool-bridge/src/catfish_tool_bridge/__main__.py", "local-search/src/catfish_search/cli.py"]
SKIP_PARTS = {"__pycache__", "tests", ".pytest_cache"}


def collect() -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    for name, src in COMPONENTS.items():
        if not src.is_dir():
            sys.exit(f"❌ 找不到 {src}")
        for p in sorted(src.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(src)
            if any(part in SKIP_PARTS or part.endswith(".egg-info") for part in rel.parts):
                continue
            if p.suffix in {".pyc", ".pyo"}:
                continue
            out.append((f"{name}/src/{rel.as_posix()}", p))
    return out


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    dest = Path(sys.argv[1])
    files = collect()
    names = {n for n, _ in files}
    missing = [m for m in MUST_EXIST if m not in names]
    if missing:
        sys.exit(f"❌ 打包清单缺关键文件: {missing}")

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for arcname, path in files:
            data = path.read_bytes()
            info = tarfile.TarInfo(arcname)
            info.size = len(data)
            info.mtime = 0
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as f, gzip.GzipFile(filename="", fileobj=f, mode="wb", mtime=0) as gz:
        gz.write(buf.getvalue())
    print(f"  OK {dest.name} · {len(files)} 个文件 · {dest.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
