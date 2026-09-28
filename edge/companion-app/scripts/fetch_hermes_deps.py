#!/usr/bin/env python3
"""把 hermes venv 的额外依赖取成离线 wheel —— mac / Windows 三处打包共用 (9/28)。

    python fetch_hermes_deps.py <输出目录> [pip download 的平台参数...]

    # mac (build-mac-resources.sh): 用包里嵌的 3.11 解释器跑, 按最低系统版本取
    "$EMBED_PY" fetch_hermes_deps.py "$DEPS_STAGE" --platform macosx_11_0_arm64
    # Windows (CI / build-msi-local.ps1): 构建机 python 跑, 指定目标解释器
    python fetch_hermes_deps.py $depsStage --platform win_amd64 --python-version 3.11 \\
        --implementation cp --abi cp311

清单是 src-tauri/hermes-extra-packages.txt —— Companion 装机和启动自检读的是
同一份 (include_str!)。原来包名在 mac 打包脚本、Windows CI、Windows 本地打包里
各写一遍, 加一个包要改四处, 漏一处就是「装机时静默缺功能」。

# 为什么 mac 要 --platform

9/28 加 pypdfium2 时实测: 不限平台的话, 在新系统的构建机上 pip 取到的是
pypdfium2 5.13.0 的 macosx_13_0_arm64 轮子 —— macOS 12 及以下的员工机上 uv 装
不上, 而装机是**一次** uv pip install, 它装不上, jieba / playwright 也一起没了。
限定 macosx_11_0_arm64 后 pip 自己退到 5.9.0 (最后一个支持 macOS 11 的版本)。

# 做完检查

清单里每个包都得有 .whl, 目录里不许有源码包 —— 员工机上 uv 带 --no-index 装,
源码包要现场构建, 离线环境下必挂 (而且是到现场才挂)。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

LIST = Path(__file__).resolve().parents[1] / "src-tauri" / "hermes-extra-packages.txt"


def load(path: Path = LIST) -> list[tuple[str, bool]]:
    """[(pip 需求, 是否只有源码包)] —— 格式见清单文件开头。"""
    out: list[tuple[str, bool]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        cols = line.split()
        if len(cols) not in (2, 3) or (len(cols) == 3 and cols[2] != "sdist"):
            raise SystemExit(f"❌ {path.name} 格式不对: {raw!r}")
        out.append((cols[0], len(cols) == 3))
    return out


def project(requirement: str) -> str:
    """`xlrd<2` → `xlrd`; 按 wheel 文件名的规范化方式 (小写, -/. 换成 _)。"""
    name = re.split(r"[<>=!~;\[\s]", requirement, maxsplit=1)[0]
    return re.sub(r"[-_.]+", "_", name).lower()


def problems(dest: Path, requirements: list[str]) -> list[str]:
    wheels = {project(p.name.split("-", 1)[0]) for p in dest.glob("*.whl")}
    found = [f"缺 {req} 的 wheel" for req in requirements if project(req) not in wheels]
    found += [f"混进了源码包 {p.name}" for p in dest.iterdir() if p.is_file() and p.suffix != ".whl"]
    return found


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows runner 默认 cp1252, 中文会崩
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if len(argv) < 2:
        print(__doc__)
        return 2
    dest = Path(argv[1])
    platform_args = argv[2:]
    dest.mkdir(parents=True, exist_ok=True)
    packages = load()
    binary = [req for req, sdist in packages if not sdist]
    source_only = [req for req, sdist in packages if sdist]

    pip = [sys.executable, "-m", "pip"]
    if subprocess.run([*pip, "download", "--only-binary=:all:", "--dest", str(dest),
                       *platform_args, *binary]).returncode != 0:
        print(f"❌ 取 wheel 失败: {' '.join(binary)} (上面是 pip 的原始报错)", file=sys.stderr)
        return 1
    # 只有源码包的 (jieba) 现打成 py3-none-any 轮子; 纯 Python, 不需要平台参数
    if source_only and subprocess.run([*pip, "wheel", "--no-deps", "--wheel-dir", str(dest),
                                       *source_only]).returncode != 0:
        print(f"❌ 打 wheel 失败: {' '.join(source_only)}", file=sys.stderr)
        return 1

    found = problems(dest, [req for req, _ in packages])
    if found:
        print("❌ hermes-deps 不完整, 装机时会静默缺功能:\n  " + "\n  ".join(found), file=sys.stderr)
        return 1
    count = len(list(dest.glob("*.whl")))
    print(f"  OK hermes-deps · {len(packages)} 个包 (连依赖共 {count} 个 wheel)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
