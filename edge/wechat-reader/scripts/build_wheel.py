#!/usr/bin/env python3
"""Build the pure-Python reader wheel without pip, wheel, or setuptools."""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
from pathlib import Path
import re
import zipfile


PROJECT_NAME = "catfish-wechat-reader"
WHEEL_NAME = PROJECT_NAME.replace("-", "_")
ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = ROOT / "src" / "catfish_wechat_reader"
FIXED_ZIP_TIME = (2026, 1, 1, 0, 0, 0)


def _version() -> str:
    text = (PACKAGE_DIR / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    if not match:
        raise RuntimeError("catfish_wechat_reader.__version__ is missing")
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
    version = _version()
    dist_info = f"{WHEEL_NAME}-{version}.dist-info"
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f"{WHEEL_NAME}-{version}-py3-none-any.whl"

    files: dict[str, bytes] = {}
    for source in sorted(PACKAGE_DIR.glob("*.py")):
        files[f"catfish_wechat_reader/{source.name}"] = source.read_bytes()
    files[f"{dist_info}/METADATA"] = (
        "Metadata-Version: 2.1\n"
        f"Name: {PROJECT_NAME}\n"
        f"Version: {version}\n"
        "Summary: Read employee-selected chat export files through the Catfish local helper protocol\n"
        "Requires-Python: >=3.10\n\n"
    ).encode("utf-8")
    files[f"{dist_info}/WHEEL"] = (
        "Wheel-Version: 1.0\n"
        "Generator: catfish-wechat-reader stdlib builder\n"
        "Root-Is-Purelib: true\n"
        "Tag: py3-none-any\n\n"
    ).encode("utf-8")
    files[f"{dist_info}/entry_points.txt"] = (
        "[console_scripts]\n"
        "catfish-wechat-reader = catfish_wechat_reader.__main__:main\n"
    ).encode("utf-8")

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
