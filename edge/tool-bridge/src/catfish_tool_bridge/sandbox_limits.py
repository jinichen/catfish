"""macOS child-process limits, imported lazily only by the macOS backend."""

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
    import resource  # Unix-only: never import during Windows daemon startup.

    for name, limit in _MACOS_SANDBOX_RLIMITS.items():
        try:
            res = getattr(resource, name, None)
            if res is None:
                continue
            resource.setrlimit(res, (limit, limit))
        except (ValueError, OSError):
            # 单个 rlimit 失败不抛, 静默. macOS quirk 概率小但有.
            pass
