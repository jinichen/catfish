#!/usr/bin/env python3
"""打 catfish-edge-runtime.tar.gz —— tool-bridge / local-search / 文件解析的 Python 源码.

Companion 在客户机器上把它解到 ~/.catfish/edge-runtime/ (services/edge_runtime.rs)。
在这之前两个平台的安装包都没带这两个组件, Companion 按源码树去找, 客户机器
上永远找不到 —— 见 edge_runtime.rs 文件头。

9/28 加 file-parse/: 聊天里上传文件用的 parse_file*.py / attachment_bm25.py
(原来在 src-tauri/scripts, 同样只有源码树里有)。Windows 上传文件报
「Python 解释器找不到」, 修掉解释器之后下一步就会是「parse_file.py 脚本找不到」
—— 装好的 mac 也一样, 只是开发机上有源码树, 一直没暴露。

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
# 文件解析脚本: 平铺在 file-parse/ 下 (parse_file.py 按同目录 import parse_file_*)。
# 只挑这几类 —— 同目录里还有测试和打包脚本, 不该进员工机器。
FILE_PARSE_SRC = EDGE / "companion-app" / "src-tauri" / "scripts"
# 10/1: meeting_asr.py (会议转写) 也平铺在这里 —— 跟解析脚本一样由 file_parse_env::find_script
# 找, 开发机读源码树、客户机读 edge-runtime, 不用另开一条查找路径。它跑在会议组件包的
# venv 里, 不是 hermes venv。
FILE_PARSE_PATTERNS = ("parse_file*.py", "attachment_bm25.py", "meeting_asr.py")
# Companion 解完会检查 (services/edge_runtime.rs 的 must 列表) —— 两边的清单要对得上
MUST_EXIST = [
    "tool-bridge/src/catfish_tool_bridge/__main__.py",
    "local-search/src/catfish_search/cli.py",
    "file-parse/parse_file.py",
    "file-parse/attachment_bm25.py",
    "file-parse/meeting_asr.py",
]
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
    parse_files = sorted({p for pattern in FILE_PARSE_PATTERNS for p in FILE_PARSE_SRC.glob(pattern)})
    out.extend((f"file-parse/{p.name}", p) for p in parse_files if p.is_file())
    return out


def main() -> int:
    # Windows runner 上 stdout 是 cp1252, 中文输出直接 UnicodeEncodeError → exit 1
    # (9/23 MSI 构建就是这么挂的: 包已经打好, 死在最后一行 print)。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
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
