#!/usr/bin/env python3
"""Remove the expired duration-valued uv cutoff from a Hermes bundle.

Hermes temporarily used ``exclude-newer = "14 days"`` in ``[tool.uv]``.
uv expects an absolute date there, not a duration.  The source bundle is
generated from an external Hermes checkout, so this tool patches only the
archive that will be shipped and never edits the developer's checkout.
"""
from __future__ import annotations

import argparse
import copy
import io
import re
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


_DURATION_LINE = re.compile(
    r"(?m)^(?P<indent>[ \t]*)exclude-newer[ \t]*=[ \t]*"
    r"(?P<quote>[\"'])(?P<value>\d+ days)(?P=quote)[ \t]*(?:\r?\n|$)"
)
_ANY_EXCLUDE_NEWER = re.compile(r"(?m)^\s*exclude-newer\s*=")


def patch_pyproject(text: str) -> tuple[str, bool]:
    """Remove one stale duration setting and leave valid date settings alone."""
    matches = list(_DURATION_LINE.finditer(text))
    if len(matches) > 1:
        raise ValueError(
            f"发现 {len(matches)} 个 duration 形式的 exclude-newer，无法安全判断目标"
        )
    if not matches:
        return text, False
    match = matches[0]
    return text[: match.start()] + text[match.end() :], True


def assert_clean_pyproject(text: str) -> None:
    """Fail closed if a duration-valued cutoff survived the patch."""
    if _DURATION_LINE.search(text):
        raise ValueError("pyproject.toml 仍含 duration 形式的 exclude-newer")


def _project_member(members: list[tarfile.TarInfo]) -> tarfile.TarInfo:
    candidates = [
        member
        for member in members
        if member.isfile()
        and PurePosixPath(member.name).name == "pyproject.toml"
        and len(PurePosixPath(member.name).parts) == 2
    ]
    if len(candidates) != 1:
        raise ValueError(
            "Hermes 归档应有且仅有一个根目录 pyproject.toml，"
            f"实际找到 {len(candidates)} 个"
        )
    return candidates[0]


def patch_archive(input_path: Path, output_path: Path) -> bool:
    """Rewrite one gzip tar archive, changing only Hermes root pyproject.toml."""
    with tarfile.open(input_path, mode="r:gz") as source:
        members = source.getmembers()
        target = _project_member(members)
        raw = source.extractfile(target)
        if raw is None:
            raise ValueError(f"无法读取 {target.name}")
        original = raw.read().decode("utf-8")
        patched, changed = patch_pyproject(original)
        assert_clean_pyproject(patched)

        if not changed:
            if input_path != output_path:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(input_path, output_path)
            return False

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            dir=output_path.parent,
            delete=False,
        ) as temp:
            temp_path = Path(temp.name)

        try:
            with tarfile.open(temp_path, mode="w:gz") as destination:
                for member in members:
                    if member.name == target.name:
                        payload = patched.encode("utf-8")
                        member = copy.copy(member)
                        member.size = len(payload)
                        destination.addfile(member, fileobj=io.BytesIO(payload))
                    else:
                        fileobj = source.extractfile(member) if member.isfile() else None
                        destination.addfile(member, fileobj=fileobj)
            temp_path.replace(output_path)
        finally:
            temp_path.unlink(missing_ok=True)
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Hermes tar.gz")
    parser.add_argument("--output", type=Path, help="patched tar.gz")
    parser.add_argument(
        "--check", action="store_true", help="只检查，不写出新归档"
    )
    args = parser.parse_args(argv)

    if not args.input.is_file():
        print(f"[ERROR] 归档不存在: {args.input}", file=sys.stderr)
        return 1
    if not args.check and args.output is None:
        parser.error("非 --check 模式必须提供 --output")

    try:
        if args.check:
            with tarfile.open(args.input, mode="r:gz") as source:
                target = _project_member(source.getmembers())
                raw = source.extractfile(target)
                if raw is None:
                    raise ValueError(f"无法读取 {target.name}")
                assert_clean_pyproject(raw.read().decode("utf-8"))
            print(f"[OK] Hermes 归档配置干净: {args.input}")
            return 0

        assert args.output is not None
        changed = patch_archive(args.input, args.output)
        print(
            f"[OK] Hermes 归档 {'已移除过期 exclude-newer' if changed else '无需修改'}"
            f" → {args.output}"
        )
        return 0
    except (OSError, tarfile.TarError, UnicodeError, ValueError) as exc:
        print(f"[ERROR] Hermes 归档修补失败: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
