#!/usr/bin/env python3
"""校验 Hermes 归档里 pyproject.toml 与 uv.lock 的 exclude-newer 设置一致。

# 这个文件取代了 patch_hermes_bundle.py (9/18)

原先的 patch_hermes_bundle.py 会把 ``[tool.uv] exclude-newer = "14 days"``
从归档的 pyproject.toml 里**删掉**, 理由写在它的 docstring 里:
"uv expects an absolute date there, not a duration"。

这个前提早就不成立了, 而且那个删除动作**正是装机变慢变脆的原因**。
用钉定的 uv 0.12.3 做的对照实验 (edge/hermes-fork/test_verify_hermes_bundle.py
把它钉成了回归测试)::

    原样归档:   uv lock --check --offline  → exit 0, "Resolved 255 packages in 1ms"
    patch 之后: uv lock --check --offline  → exit 1
                "Resolving despite existing lockfile due to removal of
                 global exclude newer"

原因: uv.lock 的 ``[options]`` 段记着 ``exclude-newer-span = "P14D"``, 是从
pyproject 的 ``"14 days"`` 推导来的。把 pyproject 那行删掉, 两边就对不上,
uv 判定 lock 过期 → **放弃 lockfile 重新解析**。

后果在 install.ps1 的 Tier-0 (``uv sync --extra all --locked``) 上:
  · lock 带 SHA256, 是唯一能挡住"传递依赖被投毒"的路径 —— 这条废了
  · 退到 Tier 1-3 的 ``uv pip install``, 不校验哈希, 从 PyPI 现场重解析
  · 断网现场直接装不上

所以现在**不改归档**, 只校验。校验失败就让构建红, 而不是让它悄悄退化。

# 两层校验

  ① 静态一致性 (总是跑, 不需要任何工具链)
     pyproject 的 exclude-newer 与 uv.lock 的 [options] 必须对得上。
  ② uv lock --check (uv 可用时才跑)
     跟 Tier-0 的 ``--locked`` 同一套判据, 最贴近真实失败面。

② 需要 uv 和一个符合 requires-python 的解释器, 开发机上不一定有; 缺了只
警告不报错。① 覆盖了已知的那个失败模式, 且零依赖, 所以它是硬闸。
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

#: uv 在 lock 里给相对 cutoff 写的占位值, 按它自己的注释"has no effect"
_NO_EFFECT_DATE = "0001-01-01T00:00:00Z"
_DURATION = re.compile(r"^\s*(?P<n>\d+)\s*(?P<unit>day|days|week|weeks|hour|hours)\s*$")
_SECTION = re.compile(r"(?m)^\s*\[(?P<name>[^\]]+)\]\s*$")


def section_value(text: str, section: str, key: str) -> str | None:
    """取 TOML 里 ``[section]`` 段内 ``key = "值"`` 的字符串值; 没有则 None。

    不用 tomllib: 构建机的 Python 版本不可控 (3.10 就没有它), 而一个构建闸门
    不该因为解释器版本而失效。只认段内**行首**的 ``key =``, 所以注释里出现
    同名字样不会误匹配; 扫到下一个 ``[...]`` 段头即停, 所以
    ``[tool.uv.exclude-newer-package]`` 这种兄弟段不会串进来。
    """
    start = None
    end = len(text)
    for match in _SECTION.finditer(text):
        if start is not None:
            end = match.start()
            break
        if match.group("name").strip() == section:
            start = match.end()
    if start is None:
        return None
    body = text[start:end]
    pattern = re.compile(
        rf"(?m)^[ \t]*{re.escape(key)}[ \t]*=[ \t]*(?P<q>[\"'])(?P<value>.*?)(?P=q)"
    )
    found = pattern.search(body)
    return found.group("value") if found else None


def duration_to_span(value: str) -> str | None:
    """``"14 days"`` → ``"P14D"``; 不是 duration 形式就 None。"""
    match = _DURATION.match(value)
    if match is None:
        return None
    count = int(match.group("n"))
    unit = match.group("unit").rstrip("s")
    if unit == "day":
        return f"P{count}D"
    if unit == "week":
        return f"P{count * 7}D"
    return f"PT{count}H"


def _root_member(members: list[tarfile.TarInfo], name: str) -> tarfile.TarInfo:
    candidates = [
        member
        for member in members
        if member.isfile()
        and PurePosixPath(member.name).name == name
        and len(PurePosixPath(member.name).parts) == 2
    ]
    if len(candidates) != 1:
        raise ValueError(
            f"Hermes 归档应有且仅有一个根目录 {name}，实际找到 {len(candidates)} 个"
        )
    return candidates[0]


def _read(source: tarfile.TarFile, member: tarfile.TarInfo) -> str:
    raw = source.extractfile(member)
    if raw is None:
        raise ValueError(f"无法读取 {member.name}")
    return raw.read().decode("utf-8")


def check_consistency(pyproject_text: str, lock_text: str) -> list[str]:
    """静态比对两边的 exclude-newer 设置, 返回问题列表 (空 = 一致)。"""
    problems: list[str] = []
    declared = section_value(pyproject_text, "tool.uv", "exclude-newer")
    lock_span = section_value(lock_text, "options", "exclude-newer-span")
    lock_date = section_value(lock_text, "options", "exclude-newer")
    if lock_date == _NO_EFFECT_DATE:
        lock_date = None

    if declared is None:
        if lock_span is not None or lock_date is not None:
            problems.append(
                "pyproject 没有 [tool.uv] exclude-newer, 但 uv.lock 记着 "
                f"exclude-newer-span={lock_span!r} / exclude-newer={lock_date!r} —— "
                "uv 会判定 lock 过期并放弃 lockfile 重新解析 "
                "(Tier-0 的 --locked 会失败, 断网装不上)"
            )
        return problems

    span = duration_to_span(declared)
    if span is not None:
        if lock_span != span:
            problems.append(
                f"pyproject 写的是相对窗口 {declared!r} (= {span}), "
                f"但 uv.lock 的 exclude-newer-span={lock_span!r}"
            )
    elif lock_date != declared:
        problems.append(
            f"pyproject 写的是绝对日期 {declared!r}, "
            f"但 uv.lock 的 exclude-newer={lock_date!r}"
        )
    return problems


def run_uv_lock_check(pyproject_text: str, lock_text: str) -> tuple[bool, str]:
    """用 uv 自己判一次 lock 新鲜度。返回 (是否跳过, 说明)。"""
    uv = shutil.which("uv")
    if uv is None:
        return True, "PATH 上没有 uv, 跳过 uv lock --check (静态校验已通过)"
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "pyproject.toml").write_text(pyproject_text, encoding="utf-8")
        (root / "uv.lock").write_text(lock_text, encoding="utf-8")
        result = subprocess.run(
            [uv, "lock", "--check", "--offline"],
            cwd=root, capture_output=True, text=True, check=False,
        )
    if result.returncode == 0:
        return False, "uv lock --check 通过 (Tier-0 的 --locked 不会重新解析)"
    detail = " ".join((result.stdout + result.stderr).split())[:500]
    raise ValueError(f"uv lock --check 失败 (exit {result.returncode}): {detail}")


def verify_archive(path: Path) -> None:
    with tarfile.open(path, mode="r:gz") as source:
        members = source.getmembers()
        pyproject_text = _read(source, _root_member(members, "pyproject.toml"))
        lock_text = _read(source, _root_member(members, "uv.lock"))

    problems = check_consistency(pyproject_text, lock_text)
    if problems:
        raise ValueError("; ".join(problems))
    print("[OK] pyproject 与 uv.lock 的 exclude-newer 一致")

    skipped, note = run_uv_lock_check(pyproject_text, lock_text)
    print(f"[{'SKIP' if skipped else 'OK'}] {note}")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="backslashreplace")
            except (LookupError, OSError, ValueError):
                pass

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Hermes tar.gz")
    args = parser.parse_args(argv)

    if not args.input.is_file():
        print(f"[ERROR] 归档不存在: {args.input}", file=sys.stderr)
        return 1
    try:
        verify_archive(args.input)
    except (OSError, tarfile.TarError, UnicodeError, ValueError) as exc:
        print(f"[ERROR] Hermes 归档校验失败: {exc}", file=sys.stderr)
        print(
            "[提示] 归档的 pyproject.toml 和 uv.lock 必须成对更新。"
            "改了 [tool.uv] exclude-newer 就要在 hermes 侧重新 uv lock。",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
