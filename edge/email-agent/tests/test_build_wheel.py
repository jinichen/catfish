"""打包用的标准库 wheel 构建器 (scripts/build_wheel.py, 9/28)。

原来 `pip wheel` 要联网下载 setuptools, 代理没开就打不出包。换成自己拼 zip 之后,
要钉住的是"打出来的东西跟原来一样能装、能用":
  · 包里的 .py 一个不少 (少一个就是 imap_archive_read 那次: 装上了, 一 import 就挂)
  · RECORD 的哈希对得上 (uv / pip 装的时候会核)
  · console script 在 (Companion 找的就是 venv/bin/catfish-email)
  · 真能离线装上并 import
"""
from __future__ import annotations

import base64
import csv
import hashlib
import importlib.util
import io
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _builder():
    spec = importlib.util.spec_from_file_location("build_wheel", ROOT / "scripts" / "build_wheel.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_wheel_carries_every_module_and_a_valid_record(tmp_path):
    wheel = _builder().build_wheel(tmp_path)
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        expected = {
            p.relative_to(ROOT / "src").as_posix()
            for p in (ROOT / "src" / "catfish_email").rglob("*.py")
            if "__pycache__" not in p.parts
        }
        assert expected <= names, f"少了: {sorted(expected - names)}"
        dist_info = next(n.split("/")[0] for n in names if n.endswith(".dist-info/RECORD"))
        record = archive.read(f"{dist_info}/RECORD").decode()
        for path, digest, size in csv.reader(io.StringIO(record)):
            if not digest:
                continue
            payload = archive.read(path)
            want = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=").decode()
            assert digest == f"sha256={want}" and int(size) == len(payload), path
        assert "catfish-email = catfish_email.__main__:main" in archive.read(
            f"{dist_info}/entry_points.txt").decode()


def test_same_source_builds_the_same_bytes(tmp_path):
    """固定时间戳: 源码没变, 包就不变 —— Companion 靠包的指纹决定要不要重装邮件组件。"""
    first = _builder().build_wheel(tmp_path / "a").read_bytes()
    second = _builder().build_wheel(tmp_path / "b").read_bytes()
    assert first == second


def test_wheel_installs_offline_and_imports(tmp_path):
    wheel = _builder().build_wheel(tmp_path / "dist")
    target = tmp_path / "site"
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-deps", "--no-index", "-q",
         "--target", str(target), str(wheel)],
        check=True,
    )
    probe = "import catfish_email.__main__, catfish_email.adapters.imap_archive_read; print('ok')"
    out = subprocess.run([sys.executable, "-c", probe], cwd=target, capture_output=True, text=True)
    assert out.stdout.strip() == "ok", out.stderr
