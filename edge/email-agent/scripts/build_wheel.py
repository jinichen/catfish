#!/usr/bin/env python3
"""只用标准库打 catfish-email 的 wheel —— 不需要 pip / setuptools / 联网 (9/28)。

# 为什么

打包原来用 `pip wheel`, 它会先建一个隔离环境, 再**联网**下载 setuptools>=68
当构建后端。9/28 鸿波的 Mac 上终端设着代理、代理没开, 于是:

    ProxyError('Cannot connect to proxy.') … No matching distribution found for setuptools>=68

一个零依赖、全是 .py 的包, 打包却要联网拉构建工具 —— 内网机器、代理没开、
离线的时候都打不出来。隔壁 wechat-reader 早就是这么做的 (scripts/build_wheel.py),
这里照同一个办法: wheel 就是一个带 dist-info 的 zip。

产物跟 setuptools 打的等价: 同样的包内容、console script、Requires-Python 和
windows extra 的依赖声明。只打 src/catfish_email 下的 .py (这个包没有别的数据文件)。
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = ROOT / "src" / "catfish_email"
PYPROJECT = ROOT / "pyproject.toml"
FIXED_ZIP_TIME = (2026, 1, 1, 0, 0, 0)  # 固定时间戳: 同样的源码打出同样的包 (指纹才稳定)


def _project_field(name: str) -> str:
    """从 pyproject.toml 取 [project] 里的单行字段 (不用 tomllib: 构建机可能是 3.10)。"""
    text = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(rf'^{name}\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise RuntimeError(f"pyproject.toml 里没有 {name}")
    return match.group(1)


def _zip_info(path: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(path, FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (0o644 & 0xFFFF) << 16
    return info


def _record_line(path: str, payload: bytes) -> tuple[str, str, str]:
    digest = hashlib.sha256(payload).digest()
    encoded = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return path, f"sha256={encoded}", str(len(payload))


def build_wheel(output_dir: Path) -> Path:
    name = _project_field("name")
    version = _project_field("version")
    dist = name.replace("-", "_")
    dist_info = f"{dist}-{version}.dist-info"
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f"{dist}-{version}-py3-none-any.whl"

    files: dict[str, bytes] = {}
    for source in sorted(PACKAGE_DIR.rglob("*.py")):
        if "__pycache__" in source.parts:
            continue
        files[source.relative_to(PACKAGE_DIR.parent).as_posix()] = source.read_bytes()
    files[f"{dist_info}/METADATA"] = (
        "Metadata-Version: 2.1\n"
        f"Name: {name}\n"
        f"Version: {version}\n"
        f"Summary: {_project_field('description')}\n"
        "Requires-Python: >=3.10\n"
        "Provides-Extra: windows\n"
        'Requires-Dist: pywin32>=306; sys_platform == "win32" and extra == "windows"\n\n'
    ).encode("utf-8")
    files[f"{dist_info}/WHEEL"] = (
        "Wheel-Version: 1.0\n"
        "Generator: catfish-email stdlib builder\n"
        "Root-Is-Purelib: true\n"
        "Tag: py3-none-any\n\n"
    ).encode("utf-8")
    files[f"{dist_info}/entry_points.txt"] = (
        "[console_scripts]\n"
        "catfish-email = catfish_email.__main__:main\n"
    ).encode("utf-8")
    files[f"{dist_info}/top_level.txt"] = b"catfish_email\n"

    record_path = f"{dist_info}/RECORD"
    records = [_record_line(path, payload) for path, payload in sorted(files.items())]
    records.append((record_path, "", ""))
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\n").writerows(records)
    files[record_path] = output.getvalue().encode("utf-8")

    with zipfile.ZipFile(destination, "w") as archive:
        for path, payload in sorted(files.items()):
            archive.writestr(_zip_info(path), payload)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    print(build_wheel(args.output_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
