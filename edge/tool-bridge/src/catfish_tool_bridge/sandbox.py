"""
sandbox.py — BL-S29.2 macOS sandbox-exec 包装.

LLM 通过 hermes/tool-bridge 调 execute_code/python/bash/shell_exec 时,
adapter._do_dispatch 走完字符串规则 (_check_execute_code_security) 后, 如果
CATFISH_SANDBOX_EXEC=1 启用沙箱模式, 就**不**转给 hermes dispatch, 改本模块
直接用 sandbox-exec 在员工 mac 本机起隔离子进程跑代码.

设计:
- stateless: 每次调 = 新 python/bash 进程, 新 TASK_DIR (LLM 不能跨 step 共享变量).
  Stateful session 是 P2/P3 事, 不阻塞 5/14 demo.
- 默认 allow + 5 类关键 deny (网络/写持久化/读凭证/iokit/sysctl-write), 看 catfish_execute.sb
- 5/19 BL-S29.5 加 nsjail (Linux) + Docker fallback.

单测 / e2e 测在 tests/sandbox/test_sandbox_exec.sh.
"""

from __future__ import annotations

import logging
import os
import platform as _platform
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Tuple

logger = logging.getLogger(__name__)


def _resolve_python_executable() -> str:
    """BL-SANDBOX-PPTX (5/15): 选 sandbox 用的 python 解释器路径.

    优先级:
      1. env CATFISH_SANDBOX_PYTHON 显式覆盖 (IT 部署时可指定特定 venv)
      2. sys.executable — 跑这模块的 python (= tool-bridge 自己的 venv).
         tool-bridge venv 装的 lib (python-pptx / pandas / matplotlib /
         openpyxl 等) 全部可用. 之前写死 /usr/bin/python3 系统 python
         什么都没装, 导致 LLM execute_code 想 import 任何非 stdlib 库都挂.
      3. 最后兜底 /usr/bin/python3 (sys.executable 不可用的极端场景)

    这条改是 5/15 鸿波撞 PPT 生成 import pptx 失败的真修.
    """
    custom = os.environ.get("CATFISH_SANDBOX_PYTHON", "").strip()
    if custom:
        p = Path(custom).expanduser()
        if p.exists():
            return str(p)
        logger.warning(
            "CATFISH_SANDBOX_PYTHON=%s 不存在, fallback sys.executable",
            custom,
        )
    if sys.executable and Path(sys.executable).exists():
        return sys.executable
    return "/usr/bin/python3"

# 沙箱 profile 路径: 优先看 env, 否则用 catfish 仓库默认路径.
# __file__ = .../edge/tool-bridge/src/catfish_tool_bridge/sandbox.py
# parents[2] = .../edge/tool-bridge/   ← 这里有 sandbox-profiles/ 子目录
_PROFILE_DIR = Path(__file__).resolve().parents[2] / "sandbox-profiles"
_DEFAULT_PROFILE_MACOS = _PROFILE_DIR / "catfish_execute.sb"
_DEFAULT_PROFILE_LINUX = _PROFILE_DIR / "catfish_execute.cfg"


def _resolve_profile_path(*, kind: str | None = None) -> Path:
    """找到沙箱 profile.

    Args:
        kind: "sandbox-exec" | "nsjail" | None (按 platform 自选)

    优先级:
        1. env CATFISH_SANDBOX_PROFILE 显式指定
        2. macOS → catfish_execute.sb
        3. Linux → catfish_execute.cfg
    """
    env_path = os.environ.get("CATFISH_SANDBOX_PROFILE")
    if env_path:
        p = Path(env_path).expanduser().resolve()
        if not p.is_file():
            raise FileNotFoundError(f"CATFISH_SANDBOX_PROFILE 指向的文件不存在: {p}")
        return p

    if kind is None:
        kind = "sandbox-exec" if _platform.system() == "Darwin" else "nsjail"

    if kind == "sandbox-exec":
        candidate = _DEFAULT_PROFILE_MACOS
    elif kind == "nsjail":
        candidate = _DEFAULT_PROFILE_LINUX
    else:
        raise ValueError(f"未知 sandbox kind: {kind!r}")

    if not candidate.is_file():
        raise FileNotFoundError(
            f"沙箱 profile 不存在: {candidate}. "
            f"用 env CATFISH_SANDBOX_PROFILE 指定路径."
        )
    return candidate


def is_sandbox_enabled() -> bool:
    """env CATFISH_SANDBOX_EXEC=1 启用 (5/14 demo 期间默认开)."""
    return os.environ.get("CATFISH_SANDBOX_EXEC", "0") == "1"


def detect_sandbox_kind() -> str | None:
    """按 platform 选沙箱后端. 三层 fallback 顺序:

    macOS: sandbox-exec → docker → None
    Linux: nsjail → docker → None
    其他:  docker → None

    docker 是兜底层, 只在 sandbox-exec / nsjail 都不在时启用 (不期望生产用,
    主要给"客户 Linux 真机临时没 nsjail" 等极端场景兜底). 真生产部署应该装
    nsjail / 用 macOS sandbox-exec.

    None = 三层都不可用, run_in_sandbox 拒绝跑.
    """
    sys_name = _platform.system()
    if sys_name == "Darwin":
        if shutil.which("sandbox-exec"):
            return "sandbox-exec"
    elif sys_name == "Linux":
        if shutil.which("nsjail"):
            return "nsjail"
    # 兜底层: docker (如果 daemon 在跑)
    if _is_docker_available():
        return "docker"
    return None


def _is_docker_available() -> bool:
    """检测 docker 是否可用 (装了 + daemon 在跑).
    探测方式: docker ps 返 0 = daemon 在; 非 0 = 装了但 daemon 没起.
    """
    if not shutil.which("docker"):
        return False
    try:
        # 短超时探测, 不阻塞 dispatch (鲁棒性)
        r = subprocess.run(
            ["docker", "ps", "-q"],
            capture_output=True, timeout=2,
        )
        return r.returncode == 0
    except Exception:
        return False


def is_sandbox_supported() -> bool:
    """macOS 有 sandbox-exec, Linux 有 nsjail, 兜底 docker, 任意一个就算支持."""
    return detect_sandbox_kind() is not None


# 6/7 BL-SANDBOX-MACOS-RLIMIT: macOS sandbox-exec 本身不拦 fork/mem/CPU bomb
# (SBPL 1 没这能力). Linux nsjail cfg 里 rlimit_cpu/as/nproc/fsize 全有, macOS
# 这块裸跑. 修法: subprocess.Popen 加 preexec_fn 在 sandbox-exec 子进程级设
# setrlimit, kernel 层硬拦.
#
# macOS 上能真 enforce 的 rlimit (其它系统 quirk):
#   RLIMIT_CPU    — CPU 秒, 触发 SIGXCPU 信号 (per-process safe)
#   RLIMIT_FSIZE  — 单文件最大字节, 触发 SIGXFSZ (per-process safe)
#   RLIMIT_NOFILE — 文件描述符数, 触发 EMFILE
#   RLIMIT_STACK  — stack 字节, macOS enforce
#
# 不设的 (macOS quirk):
#   RLIMIT_NPROC  — macOS 是 per-user 不是 per-process, 设了**会影响员工整个
#                   用户的进程数**, 直接让员工电脑卡死. 不安全, 不设.
#                   → fork bomb 在 macOS 这边目前**仍拦不住** (known limit,
#                     真要拦只能上 nsjail / docker / cgroups, macOS 没原生)
#   RLIMIT_AS     — macOS 64-bit 系统不 enforce (10.x 之后基本 no-op)
#   RLIMIT_DATA   — macOS 时灵时不灵, 不可靠
#
# Linux nsjail 路径**不**走这函数 (nsjail cfg 自己 rlimit 全配了, kernel 级更强).
_MACOS_SANDBOX_RLIMITS = {
    "RLIMIT_CPU":    30,                  # CPU 累计 30 秒 (跟 nsjail rlimit_cpu 一致)
    "RLIMIT_FSIZE":  50 * 1024 * 1024,    # 单文件最大 50 MB (跟 nsjail 一致)
    "RLIMIT_NOFILE": 256,                 # fd 上限 256 (跟 nsjail 一致)
    "RLIMIT_STACK":  8 * 1024 * 1024,     # stack 8 MB (跟 nsjail 一致)
}


def _apply_macos_rlimits() -> None:
    """preexec_fn — 在 sandbox-exec 子进程 fork 后 exec 前调用.

    设 setrlimit 在子进程, 由 kernel enforce (sandbox-exec 自己拦不住). rlimit
    会 inherit 给 sandbox-exec → Python/bash 子子进程, 整链都受限.

    各 rlimit 失败时静默 — macOS 某些版本 / 某些字段时灵时不灵, 单个失败不影响
    其它字段生效. CPU / FSIZE 这俩最重要的实测在 macOS 12+ 一直稳.
    """
    for name, limit in _MACOS_SANDBOX_RLIMITS.items():
        try:
            res = getattr(resource, name, None)
            if res is None:
                continue
            resource.setrlimit(res, (limit, limit))
        except (ValueError, OSError):
            # 单个 rlimit 失败不抛, 静默. macOS quirk 概率小但有.
            pass


def _build_macos_sandbox_args(profile: Path, task_dir: Path) -> list[str]:
    """组 macOS sandbox-exec 命令前缀."""
    home = os.path.expanduser("~")
    return [
        "sandbox-exec",
        "-f", str(profile),
        "-D", f"TASK_DIR={task_dir}",
        "-D", f"HOME_SSH={home}/.ssh",
        "-D", f"HOME_AWS={home}/.aws",
        "-D", f"HOME_GNUPG={home}/.gnupg",
        "-D", f"HOME_CONFIG={home}/.config",
        "-D", f"HOME_KEYCHAINS={home}/Library/Keychains",
        "-D", f"HOME_DOCUMENTS={home}/Documents",
        "-D", f"HOME_DESKTOP={home}/Desktop",
        "-D", f"HOME_DOWNLOADS={home}/Downloads",
        "-D", f"HOME_CATFISH={home}/.catfish",
    ]


def _build_nsjail_args(profile: Path, task_dir: Path) -> list[str]:
    """组 Linux nsjail 命令前缀.

    nsjail 用 --config 装 cfg 模板, 调用方按 task 注入 TASK_DIR 的 bindmount.
    架构 (arm64 vs x86_64) 不同, mount 列表加额外 --bindmount_ro 指定 lib64 等.
    """
    args = [
        "nsjail",
        "--config", str(profile),
        # TASK_DIR 注入: 沙箱内 /tmp/task 是员工的 task_dir
        "--bindmount", f"{task_dir}:/tmp/task",
        # 沙箱内当前目录 = TASK_DIR (跟 macOS cwd 对齐)
        "--cwd", "/tmp/task",
    ]
    # x86_64 上 /lib64 必需; arm64 上没有这个目录, mandatory:false 已经容错了
    return args


def _build_docker_args(task_dir: Path, timeout_s: int) -> list[str]:
    """组 docker fallback 命令前缀.

    第三层兜底: 沙箱后端 (sandbox-exec/nsjail) 都不在时, 用 docker 隔离.
    比 nsjail 更重 (启动 ~1s, vs nsjail ~50ms), 但接近的隔离强度.

    参数:
        --rm                自动清理容器
        --network=none      网络全断 (= nsjail clone_newnet)
        --read-only         rootfs 只读 (= nsjail mount tmpfs)
        --tmpfs /tmp        /tmp 可写 (装 task_dir mount)
        --memory=512m       内存上限 (= nsjail rlimit_as)
        --cpus=1            CPU 限 1 核 (= nsjail rlimit_cpu)
        --pids-limit=20     进程数上限 (fork bomb 防护)
        --user=65534:65534  以 nobody 身份跑 (no setuid)
        --cap-drop=ALL      丢所有 Linux capabilities
        --security-opt=no-new-privileges  防 setuid 提权
        --workdir /tmp/task 沙箱内 cwd
    """
    return [
        "docker", "run", "--rm",
        "--network=none",
        "--read-only",
        "--tmpfs", "/tmp:rw,size=128m,exec",
        "-v", f"{task_dir}:/tmp/task:rw",
        "--memory=512m",
        "--memory-swap=512m",
        "--cpus=1",
        "--pids-limit=20",
        "--user=65534:65534",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        "--workdir", "/tmp/task",
        "-e", "HOME=/tmp/task",
        "-e", "PATH=/usr/bin:/bin",
        # 镜像: 极简 python (~50MB), 客户内网部署时改成内网 registry 镜像
        "python:3.11-slim",
    ]


def run_in_sandbox(
    code: str,
    *,
    lang: str = "python",
    timeout_s: int = 30,
    max_output_bytes: int = 200_000,
) -> Dict[str, Any]:
    """在 macOS sandbox-exec 隔离的子进程里跑代码.

    Args:
        code: 要跑的代码 (python/bash 文本)
        lang: "python" | "bash" | "sh"
        timeout_s: 超时秒, 超时 SIGKILL (没用 perl alarm, 子进程超时由 subprocess.run 的 timeout 处理)
        max_output_bytes: stdout/stderr 各自截断阈值, 防 LLM 拿到天量输出

    Returns:
        dict: {
            ok: bool,                      # rc == 0 算成功
            sandbox_used: True,            # 标志位, audit 字段
            sandbox_kind: "sandbox-exec",  # macOS 是 sandbox-exec, Linux 5/19 后 nsjail
            stdout: str (可能截断),
            stderr: str (可能截断),
            rc: int,
            elapsed_ms: float,
            timed_out: bool,
        }
    """
    sandbox_kind = detect_sandbox_kind()
    if sandbox_kind is None:
        return {
            "ok": False,
            "sandbox_used": False,
            "sandbox_kind": None,
            "stdout": "",
            "stderr": (
                "沙箱不可用: macOS 需要 sandbox-exec / Linux 需要 nsjail. "
                "5/19 BL-S29.5 后会加 Docker fallback."
            ),
            "rc": -1,
            "elapsed_ms": 0.0,
            "timed_out": False,
        }

    # docker fallback 不需要 profile 文件 (用 docker run flags 直接配)
    if sandbox_kind != "docker":
        profile = _resolve_profile_path(kind=sandbox_kind)
    else:
        profile = None

    # 选解释器
    # BL-SANDBOX-PPTX (5/15): python 改用 _resolve_python_executable()
    # (= tool-bridge venv 自己的 python), 让 venv 装的 python-pptx / pandas /
    # matplotlib 等库 LLM execute_code 直接可用. 之前写死 /usr/bin/python3
    # 系统 python 啥都没装. 见函数 docstring.
    py_path = _resolve_python_executable()
    interpreter_map = {
        "python": py_path,
        "py": py_path,
        "bash": "/bin/bash",
        "sh": "/bin/sh",
    }
    interpreter = interpreter_map.get(lang.lower())
    if interpreter is None:
        return {
            "ok": False,
            "sandbox_used": False,
            "sandbox_kind": None,
            "stdout": "",
            "stderr": f"不支持的 lang: {lang!r} (支持: python/bash/sh)",
            "rc": -1,
            "elapsed_ms": 0.0,
            "timed_out": False,
        }

    # 创 TASK_DIR (沙箱内唯一可写的工作目录)
    task_dir = Path(tempfile.mkdtemp(prefix="catfish-sandbox-"))

    try:
        # 选不同沙箱后端的命令前缀
        if sandbox_kind == "sandbox-exec":
            sandbox_argv = _build_macos_sandbox_args(profile, task_dir)
            argv = sandbox_argv + [interpreter, "-c", code]
        elif sandbox_kind == "nsjail":
            sandbox_argv = _build_nsjail_args(profile, task_dir)
            # nsjail 用 -- 分隔沙箱参数和命令
            argv = sandbox_argv + ["--", interpreter, "-c", code]
        elif sandbox_kind == "docker":
            # docker fallback: python:3.11-slim 镜像内的 /usr/local/bin/python3
            # 不用 caller 传的 /usr/bin/python3 (容器内可能没有), 让 docker 镜像决定
            container_interpreter = {
                "python": "python3",
                "py": "python3",
                "bash": "bash",     # python:3.11-slim 镜像不带 bash, 用 sh
                "sh": "sh",
            }.get(lang.lower(), "python3")
            sandbox_argv = _build_docker_args(task_dir, timeout_s)
            argv = sandbox_argv + [container_interpreter, "-c", code]
        else:
            raise RuntimeError(f"未知 sandbox kind: {sandbox_kind!r}")

        # 6/7 BL-SANDBOX-MACOS-RLIMIT: macOS sandbox-exec 路径加 preexec_fn 设
        # rlimit (CPU / FSIZE / NOFILE / STACK). 详 _apply_macos_rlimits docstring.
        # nsjail 路径**不**加 (cfg 已经 rlimit 全设了, 不重复). docker 也不加
        # (docker run 自己 --memory / --cpus 限制).
        preexec_fn = None
        if sandbox_kind == "sandbox-exec":
            preexec_fn = _apply_macos_rlimits

        start = time.time()
        timed_out = False
        try:
            # cwd=TASK_DIR 让 LLM 写相对路径文件直接落在沙箱目录
            # (nsjail 已经在 cfg 里设了 --cwd /tmp/task, sandbox-exec 靠 cwd 参数)
            proc = subprocess.run(
                argv,
                capture_output=True,
                timeout=timeout_s,
                cwd=str(task_dir),
                preexec_fn=preexec_fn,  # noqa: PLW1509 (sandbox 限制是核心特性, 不是杂项)
                env={
                    # 给沙箱内进程一个干净 env, 不漏员工 mac 上的 secret 环境变量
                    # (例如 GITHUB_TOKEN / OPENAI_API_KEY 这些 LLM 不该见的)
                    # 注: nsjail 在 cfg 里也设了 envar, 但 keep_env:false 让 nsjail
                    # 不继承 caller env. 这里我们给一个最小集兜底.
                    "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                    "HOME": str(task_dir),  # 沙箱内 HOME 指向 TASK_DIR, 防 LLM 用 ~/ 偷文件
                    "TMPDIR": "/tmp",
                    "LANG": os.environ.get("LANG", "en_US.UTF-8"),
                },
            )
            rc = proc.returncode
            stdout = proc.stdout.decode("utf-8", errors="replace")
            stderr = proc.stderr.decode("utf-8", errors="replace")
        except subprocess.TimeoutExpired as e:
            timed_out = True
            rc = -signal.SIGKILL.value  # 约定: 超时返 -SIGKILL
            stdout = (e.stdout or b"").decode("utf-8", errors="replace")
            stderr = (e.stderr or b"").decode("utf-8", errors="replace") + \
                     f"\n[catfish-sandbox] 超时 {timeout_s}s, 进程被 SIGKILL"

        elapsed_ms = (time.time() - start) * 1000.0

        # 截断防止 LLM 拿到 100MB 输出 (恶意代码会塞)
        if len(stdout) > max_output_bytes:
            stdout = stdout[:max_output_bytes] + f"\n... [truncated {len(stdout) - max_output_bytes} bytes]"
        if len(stderr) > max_output_bytes:
            stderr = stderr[:max_output_bytes] + f"\n... [truncated {len(stderr) - max_output_bytes} bytes]"

        return {
            "ok": rc == 0 and not timed_out,
            "sandbox_used": True,
            "sandbox_kind": sandbox_kind,   # "sandbox-exec" (macOS) | "nsjail" (Linux)
            "stdout": stdout,
            "stderr": stderr,
            "rc": rc,
            "elapsed_ms": round(elapsed_ms, 2),
            "timed_out": timed_out,
        }
    finally:
        # 清理 TASK_DIR (即使 LLM 在沙箱里写了文件)
        try:
            shutil.rmtree(task_dir, ignore_errors=True)
        except Exception:
            logger.warning("清理沙箱 TASK_DIR 失败: %s", task_dir, exc_info=True)


def run_in_sandbox_streaming(
    code: str,
    *,
    lang: str = "python",
    timeout_s: int = 30,
    max_output_bytes: int = 200_000,
    on_chunk: "Callable[[str, str], None] | None" = None,
) -> Dict[str, Any]:
    """P3.5.39 (6/18 鸿波 audit daytona 后催): run_in_sandbox 流式版本.

    跟 run_in_sandbox 同 sandbox 隔离逻辑 + 同返回结构, 区别:
      - subprocess.Popen 替 subprocess.run (非阻塞 spawn)
      - 两个 daemon thread 分别读 stdout/stderr 行式
      - 每收到一行调 on_chunk(stream, text), 让 caller 流式拿到进度
      - 全量累积仍走 stdout/stderr 字符串, 跟现有路径兼容

    Args:
        on_chunk(stream, text): stream ∈ {"stdout", "stderr"}, text = 新读到的一行
            (含 \\n). caller 用它实时更新 task.latest_output / Tauri event / etc.
            None = 不流式, 等价 run_in_sandbox (但仍 Popen 内部走).

    Returns: 跟 run_in_sandbox 同结构.

    audit (鸿波 6/18 daytona PTY 借鉴, 不抄 cloud stack, 只抄 streaming 体感):
      catfish_run_task long task LLM 调 catfish_task_status 现状只见 elapsed_s,
      看不到 stdout 中间. 这条路径让 task.latest_output 实时滚动 ~4KB tail buffer.
    """
    import threading
    from typing import Callable  # noqa: F401, PLC0415 — type-only at top would force module-level

    sandbox_kind = detect_sandbox_kind()
    if sandbox_kind is None:
        return {
            "ok": False,
            "sandbox_used": False,
            "sandbox_kind": None,
            "stdout": "",
            "stderr": (
                "沙箱不可用: macOS 需要 sandbox-exec / Linux 需要 nsjail. "
                "5/19 BL-S29.5 后会加 Docker fallback."
            ),
            "rc": -1,
            "elapsed_ms": 0.0,
            "timed_out": False,
        }

    if sandbox_kind != "docker":
        profile = _resolve_profile_path(kind=sandbox_kind)
    else:
        profile = None

    py_path = _resolve_python_executable()
    interpreter_map = {
        "python": py_path,
        "py": py_path,
        "bash": "/bin/bash",
        "sh": "/bin/sh",
    }
    interpreter = interpreter_map.get(lang.lower())
    if interpreter is None:
        return {
            "ok": False,
            "sandbox_used": False,
            "sandbox_kind": None,
            "stdout": "",
            "stderr": f"不支持的 lang: {lang!r} (支持: python/bash/sh)",
            "rc": -1,
            "elapsed_ms": 0.0,
            "timed_out": False,
        }

    task_dir = Path(tempfile.mkdtemp(prefix="catfish-sandbox-"))

    try:
        if sandbox_kind == "sandbox-exec":
            sandbox_argv = _build_macos_sandbox_args(profile, task_dir)
            argv = sandbox_argv + [interpreter, "-c", code]
        elif sandbox_kind == "nsjail":
            sandbox_argv = _build_nsjail_args(profile, task_dir)
            argv = sandbox_argv + ["--", interpreter, "-c", code]
        elif sandbox_kind == "docker":
            container_interpreter = {
                "python": "python3",
                "py": "python3",
                "bash": "bash",
                "sh": "sh",
            }.get(lang.lower(), "python3")
            sandbox_argv = _build_docker_args(task_dir, timeout_s)
            argv = sandbox_argv + [container_interpreter, "-c", code]
        else:
            raise RuntimeError(f"未知 sandbox kind: {sandbox_kind!r}")

        preexec_fn = None
        if sandbox_kind == "sandbox-exec":
            preexec_fn = _apply_macos_rlimits

        start = time.time()
        timed_out = False

        proc = subprocess.Popen(  # noqa: S603
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(task_dir),
            preexec_fn=preexec_fn,  # noqa: PLW1509
            env={
                "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                "HOME": str(task_dir),
                "TMPDIR": "/tmp",
                "LANG": os.environ.get("LANG", "en_US.UTF-8"),
            },
            text=True,                # 行式读, 默认 locale 编码 (errors=strict)
            encoding="utf-8",
            errors="replace",         # 防 LLM 跑非 utf-8 输出炸 decode
            bufsize=1,                # line-buffered, 行到位就给 reader
        )

        # 两个 thread 分别读 stdout/stderr 累积 + 触发 on_chunk
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        stdout_bytes_seen = 0
        stderr_bytes_seen = 0
        truncate_lock = threading.Lock()

        def _reader(stream, kind: str, bucket: list[str]) -> None:
            nonlocal stdout_bytes_seen, stderr_bytes_seen
            try:
                for line in iter(stream.readline, ""):
                    if not line:
                        break
                    with truncate_lock:
                        bucket.append(line)
                        if kind == "stdout":
                            stdout_bytes_seen += len(line)
                            over = stdout_bytes_seen > max_output_bytes
                        else:
                            stderr_bytes_seen += len(line)
                            over = stderr_bytes_seen > max_output_bytes
                    # 超截断阈值后不再 on_chunk (累积仍继续, 最终统一截断)
                    if on_chunk is not None and not over:
                        try:
                            on_chunk(kind, line)
                        except Exception:  # noqa: BLE001
                            logger.debug("on_chunk 抛错 (吞掉, 不影响 sandbox)", exc_info=True)
            finally:
                try:
                    stream.close()
                except Exception:  # noqa: BLE001
                    pass

        t_out = threading.Thread(
            target=_reader, args=(proc.stdout, "stdout", stdout_lines),
            name=f"sandbox-stdout-{proc.pid}", daemon=True,
        )
        t_err = threading.Thread(
            target=_reader, args=(proc.stderr, "stderr", stderr_lines),
            name=f"sandbox-stderr-{proc.pid}", daemon=True,
        )
        t_out.start()
        t_err.start()

        try:
            rc = proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                logger.warning("kill 超时 sandbox 进程失败", exc_info=True)
            try:
                rc = proc.wait(timeout=5)  # 给 kill 5s 收尸
            except subprocess.TimeoutExpired:
                rc = -signal.SIGKILL.value

        # 等 reader thread drain 剩余 buffer (kill 之后 OS 仍可能 flush 几行)
        t_out.join(timeout=2)
        t_err.join(timeout=2)

        elapsed_ms = (time.time() - start) * 1000.0

        stdout = "".join(stdout_lines)
        stderr = "".join(stderr_lines)
        if timed_out:
            stderr += f"\n[catfish-sandbox] 超时 {timeout_s}s, 进程被 SIGKILL"

        # 截断防 LLM 拿到天量
        if len(stdout) > max_output_bytes:
            stdout = stdout[:max_output_bytes] + f"\n... [truncated {len(stdout) - max_output_bytes} bytes]"
        if len(stderr) > max_output_bytes:
            stderr = stderr[:max_output_bytes] + f"\n... [truncated {len(stderr) - max_output_bytes} bytes]"

        return {
            "ok": rc == 0 and not timed_out,
            "sandbox_used": True,
            "sandbox_kind": sandbox_kind,
            "stdout": stdout,
            "stderr": stderr,
            "rc": rc,
            "elapsed_ms": round(elapsed_ms, 2),
            "timed_out": timed_out,
        }
    finally:
        try:
            shutil.rmtree(task_dir, ignore_errors=True)
        except Exception:
            logger.warning("清理沙箱 TASK_DIR 失败: %s", task_dir, exc_info=True)


def detect_lang_from_tool_name(tool_name: str) -> str | None:
    """根据工具名推断 lang. 不认识返回 None (caller 不应调沙箱)."""
    n = tool_name.lower()
    if n in ("python", "execute_code"):
        return "python"
    if n == "bash":
        return "bash"
    if n in ("sh", "shell_exec"):
        return "sh"
    return None
