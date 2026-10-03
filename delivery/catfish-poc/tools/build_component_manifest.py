#!/usr/bin/env python3
"""生成组件分发目录的 manifest.json (10/1, docs/MEETING-MINUTES-PLAN.md §4).

中央 catfish-web 的 nginx 把宿主机组件目录挂到 /components/ (catfish-locations.conf),
Companion 读 /components/manifest.json 决定有哪些组件可下、下哪个文件、下完怎么校验。

用法 (在中央服务器上, 或打组件包的机器上):

    python3 tools/build_component_manifest.py ./components    (在交付目录 delivery/catfish-poc/ 下)

组件包文件名约定 (版本号进文件名, nginx 可以放心缓存):

    <name>-<major>.<minor>.<patch>-<platform>.tar.gz
    例: meeting-asr-1.0.0-mac-arm64.tar.gz

同一 name + platform 有多个版本时, manifest 只列最高版本 —— 旧包留在目录里不碍事,
正在下载旧版的客户端不会被半路换掉文件。不符合约定的文件直接报错退出, 不静默跳过:
放错名字的包在 manifest 里"不存在", 客户端那边只会看到"没有这个组件", 查不出原因。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = 1
# any = 跟平台无关的包 (纯模型文件之类); Companion 找不到本平台的包时退而用它 (10/3)。
# 中央门户上传接口 (llm-gateway components_admin.py) 用同一套规则, 改这里要一起改 ——
# central/llm-gateway/tests/test_components_admin.py 会对两边的结果。
PLATFORMS = ("mac-arm64", "mac-x64", "windows-x64", "any")
_NAME_RE = re.compile(
    r"^(?P<name>[a-z0-9]+(?:-[a-z0-9]+)*?)"
    r"-(?P<version>\d+\.\d+\.\d+)"
    r"-(?P<platform>" + "|".join(re.escape(p) for p in PLATFORMS) + r")"
    r"\.tar\.gz$"
)


def parse_filename(filename: str) -> dict | None:
    m = _NAME_RE.match(filename)
    if not m:
        return None
    return m.groupdict()


def _version_key(v: str) -> tuple[int, int, int]:
    a, b, c = v.split(".")
    return int(a), int(b), int(c)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def build_manifest(directory: Path) -> dict:
    bad: list[str] = []
    newest: dict[tuple[str, str], dict] = {}
    for p in sorted(directory.iterdir()):
        if not p.is_file() or p.name == "manifest.json" or p.name.startswith("."):
            continue
        meta = parse_filename(p.name)
        if meta is None:
            bad.append(p.name)
            continue
        key = (meta["name"], meta["platform"])
        cur = newest.get(key)
        if cur is None or _version_key(meta["version"]) > _version_key(cur["version"]):
            newest[key] = {**meta, "path": p}
    if bad:
        raise ValueError(
            "这些文件不符合 <name>-<x.y.z>-<platform>.tar.gz 约定 "
            f"(platform ∈ {', '.join(PLATFORMS)}): " + ", ".join(bad)
        )
    components = []
    for (name, platform), meta in sorted(newest.items()):
        path: Path = meta["path"]
        components.append({
            "name": name,
            "version": meta["version"],
            "platform": platform,
            "file": path.name,
            "size": path.stat().st_size,
            "sha256": sha256_of(path),
        })
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "components": components,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("directory", type=Path)
    args = ap.parse_args(argv)
    d: Path = args.directory
    if not d.is_dir():
        print(f"❌ 不是目录: {d}", file=sys.stderr)
        return 1
    try:
        manifest = build_manifest(d)
    except ValueError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1
    out = d / "manifest.json"
    tmp = d / ".manifest.json.tmp"
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(out)  # 原子替换: 客户端不会读到写了一半的 manifest
    for c in manifest["components"]:
        print(f"  ✓ {c['name']} {c['version']} {c['platform']}  {c['size'] / 1e6:.0f} MB")
    print(f"✓ 写入 {out} · {len(manifest['components'])} 个组件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
