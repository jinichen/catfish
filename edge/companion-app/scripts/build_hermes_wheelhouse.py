#!/usr/bin/env python3
"""给 hermes 源码树配一份离线安装用的 wheelhouse (10/14, hermes v0.22.0 起)。

    python build_hermes_wheelhouse.py --src <hermes 源码树> --uv <uv 0.12.3> \\
        --target aarch64-apple-darwin|x86_64-apple-darwin|x86_64-pc-windows-msvc

产出 (都写进源码树, 跟着 hermes-agent-bundle 一起打包):
    <src>/catfish-requirements.txt   目标平台的全部依赖, 版本钉死 + sha256
    <src>/wheelhouse/*.whl           对应的 wheel, 外加 editable 安装要的 setuptools/wheel

员工机上装机就是 (Companion hermes_install_native.rs):
    uv venv venv --python <内嵌 CPython 3.14>
    uv pip install --offline --no-index --find-links wheelhouse --require-hashes -r catfish-requirements.txt
    uv pip install --offline --no-index --find-links wheelhouse --no-deps -e .

# 为什么不再调上游 install.sh / install.ps1

v0.22 起上游安装器换成了自带的包管理器 (`hermes pm`), 只认 Python 3.14、要联网按
uv.lock 装, 原来打在 install.sh / install.ps1 上的离线补丁锚点全部对不上
(docs/HERMES-022-UPGRADE-ASSESSMENT.md)。这里直接从上游的 uv.lock 出发:

1. ``uv export --locked --extra all`` —— 上游锁定的版本 (跟 Tier-0 ``uv sync --locked`` 同一份);
2. 加上 catfish 自己要的包 (src-tauri/hermes-extra-packages.txt, 跟 Companion 装机自检同一份清单),
   用 ``uv pip compile --python-platform <目标>`` 一起解析 —— 额外包跟 hermes 依赖不会版本打架,
   环境标记 (sys_platform 等) 按**目标平台**求值, 不是按构建机;
3. ``--generate-hashes``: 员工机 ``--require-hashes`` 装, 传递依赖被换包也装不进去 (lock 原来的保护不丢);
4. ``pip download --only-binary=:all:`` 按目标平台取 wheel; 只有源码包的 (jieba) 现打 py3-none-any。

macOS 按安装包支持的最低系统取 (aarch64: 11.0, x64: 10.15) —— 9/28 实测不限的话新系统构建机会取到
只支持 macOS 13+ 的 pypdfium2, 老系统员工机上一个装不上就全部装不上。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXTRA_LIST = HERE.parent / "src-tauri" / "hermes-extra-packages.txt"
PYTHON_VERSION = "3.14"
# editable 安装 hermes 自己要的构建后端 —— 版本跟上游 pyproject [build-system] 一致, 下面会核对
BUILD_BACKEND = ["setuptools==83.0.0", "wheel"]

PIP_PLATFORMS = {
    "aarch64-apple-darwin": ["macosx_11_0_arm64", "macosx_11_0_universal2"],
    "x86_64-apple-darwin": [f"macosx_10_{v}_x86_64" for v in range(9, 16)]
    + [f"macosx_10_{v}_universal2" for v in range(9, 16)],
    "x86_64-pc-windows-msvc": ["win_amd64"],
}
# uv pip compile 的 --python-platform 要带最低系统版本, 不然按 uv 的默认 (更高) 挑轮子
UV_PLATFORMS = {
    "aarch64-apple-darwin": "aarch64-apple-darwin",
    "x86_64-apple-darwin": "x86_64-apple-darwin",
    "x86_64-pc-windows-msvc": "x86_64-pc-windows-msvc",
}
MACOS_FLOOR = {"aarch64-apple-darwin": "11.0", "x86_64-apple-darwin": "10.15"}
# 目标平台上没有现成 3.14 wheel、要在构建机上用 --python (内嵌解释器, 目标架构) 现编的包。
# watchdog 6.0.0 只发到 cp313 的 macOS 轮子 (Windows 是 py3-none-win_amd64, 不用编)。
SOURCE_BUILD = {
    "aarch64-apple-darwin": {"watchdog"},
    "x86_64-apple-darwin": {"watchdog"},
    "x86_64-pc-windows-msvc": set(),
}


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print("  $", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run(cmd, check=True, **kw)


def extras() -> tuple[list[str], list[str]]:
    """(有 wheel 的需求, 只有源码包的需求) —— 格式见清单文件开头。"""
    binary: list[str] = []
    source_only: list[str] = []
    for raw in EXTRA_LIST.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        cols = line.split()
        (source_only if len(cols) == 3 and cols[2] == "sdist" else binary).append(cols[0])
    return binary, source_only


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "_", name).lower()


def pinned(requirements: Path) -> dict[str, str]:
    """catfish-requirements.txt 里钉死的 {规范化包名: 版本}。"""
    out: dict[str, str] = {}
    for line in requirements.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)", line)
        if m:
            out[norm(m.group(1))] = m.group(2)
    return out


def check_backend(src: Path) -> None:
    text = (src / "pyproject.toml").read_text(encoding="utf-8")
    if 'requires = ["setuptools==83.0.0", "wheel"]' not in text:
        sys.exit("❌ 上游 pyproject [build-system] 变了, 改 BUILD_BACKEND 再打包 (editable 安装离线要用)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, required=True)
    ap.add_argument("--uv", required=True)
    ap.add_argument("--target", choices=sorted(PIP_PLATFORMS), required=True)
    ap.add_argument("--python", default=sys.executable,
                    help="跑 pip download 的解释器 (要能联网; 取的是目标平台的 wheel, 版本不必跟目标一致)")
    args = ap.parse_args()
    src: Path = args.src.resolve()
    check_backend(src)

    wheelhouse = src / "wheelhouse"
    req_out = src / "catfish-requirements.txt"
    shutil.rmtree(wheelhouse, ignore_errors=True)
    wheelhouse.mkdir(parents=True)
    binary, source_only = extras()
    # 本平台要现编的 (C 扩展) 也走"单独解析 + 现打 wheel"那条路
    build_here = SOURCE_BUILD[args.target]
    source_only += [r for r in binary if norm(re.split(r"[<>=!~;\[\s]", r, maxsplit=1)[0]) in build_here]
    binary = [r for r in binary if r not in source_only]

    env = dict(os.environ)
    if args.target in MACOS_FLOOR:
        # uv 默认按较新的 macOS 挑轮子 (实测 pypdfium2 选到只支持 13+ 的 5.14.0), 压到安装包支持的最低系统
        env["MACOSX_DEPLOYMENT_TARGET"] = MACOS_FLOOR[args.target]
    with tempfile.TemporaryDirectory() as tmp:
        core = Path(tmp) / "core.txt"
        # ① 上游锁定的依赖 (不含 hermes 自己, 不含 dev)
        with core.open("w", encoding="utf-8") as fh:
            run([args.uv, "export", "--locked", "--no-dev", "--extra", "all", "--format", "requirements-txt",
                 "--no-hashes", "--no-emit-project", "--no-header", "-p", PYTHON_VERSION, "-q"],
                cwd=src, stdout=fh)
        # ② 按目标平台一起解析, 只要 wheel (--only-binary 才会按 MACOSX_DEPLOYMENT_TARGET 挑), 带 sha256。
        #    sys_platform == 'android' 那行 git 源 (psutil) 按目标平台被丢掉。
        wanted = Path(tmp) / "wanted.txt"
        wanted.write_text(core.read_text(encoding="utf-8") + "\n" + "\n".join(binary) + "\n", encoding="utf-8")
        run([args.uv, "pip", "compile", str(wanted), "--python-platform", UV_PLATFORMS[args.target],
             "-p", PYTHON_VERSION, "--only-binary", ":all:", "--generate-hashes", "--no-header", "--no-annotate",
             "-o", str(req_out), "-q"], env=env)
        # 只有源码包的 (jieba) / 本平台要现编的 (watchdog@mac) 单独解析出版本 —— 它们都没有依赖
        src_pins: dict[str, str] = {}
        for req in source_only:
            one = Path(tmp) / "one.txt"
            one.write_text(req + "\n", encoding="utf-8")
            out = subprocess.run([args.uv, "pip", "compile", str(one), "-p", PYTHON_VERSION, "--no-header",
                                  "--no-annotate", "-q"], check=True, capture_output=True, text=True).stdout
            for line in out.splitlines():
                m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==(\S+)", line)
                if m:
                    src_pins[norm(m.group(1))] = m.group(2)

    pins = pinned(req_out)
    # ③ 取 wheel。--require-hashes 用 compile 出来的 sha256 逐个校验
    plat: list[str] = []
    for p in PIP_PLATFORMS[args.target]:
        plat += ["--platform", p]
    pip = [args.python, "-m", "pip", "--disable-pip-version-check"]
    target_tags = [*plat, "--python-version", PYTHON_VERSION, "--implementation", "cp", "--abi", "cp314",
                   "--abi", "abi3", "--abi", "none"]
    run([*pip, "download", "-q", "--only-binary=:all:", "--no-deps", "--require-hashes", *target_tags,
         "-d", str(wheelhouse), "-r", str(req_out)])
    run([*pip, "download", "-q", "--only-binary=:all:", "--no-deps", *target_tags, "-d", str(wheelhouse),
         *BUILD_BACKEND])
    # ④ 只有源码包的现打成 wheel (员工机 --no-index 装不了源码包; C 扩展用 --python 这个目标架构的解释器编),
    #    用打出来的 wheel 的 sha256 记账,
    #    这样员工机 --require-hashes 照样一个不漏
    extra_lines: list[str] = []
    for name, version in sorted(src_pins.items()):
        before = set(wheelhouse.glob("*.whl"))
        run([*pip, "wheel", "-q", "--no-deps", "--no-binary", ":all:", "--wheel-dir", str(wheelhouse),
             f"{name}=={version}"])
        built = sorted(set(wheelhouse.glob("*.whl")) - before)
        if len(built) != 1:
            sys.exit(f"❌ {name} 打 wheel 结果不对: {built}")
        digest = hashlib.sha256(built[0].read_bytes()).hexdigest()
        extra_lines.append(f"{name}=={version} \\\n    --hash=sha256:{digest}")
        pins[name] = version
    if extra_lines:
        with req_out.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(extra_lines) + "\n")

    # ⑤ 核对: 每个钉死的包都有 wheel, 没有混进源码包
    have = {norm(p.name.split("-", 1)[0]) for p in wheelhouse.glob("*.whl")}
    missing = sorted(set(pins) - have)
    stray = [p.name for p in wheelhouse.iterdir() if p.suffix != ".whl"]
    if missing or stray:
        print(f"❌ wheelhouse 不完整: 缺 {missing} · 非 wheel {stray}", file=sys.stderr)
        return 1
    size = sum(p.stat().st_size for p in wheelhouse.glob("*.whl")) / 1024 / 1024
    print(f"  OK wheelhouse · {args.target} · {len(pins)} 个包 · {len(have)} 个 wheel · {size:.0f}MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
