"""parse_file.py preview-only mode 单测 (5/5 重构).

跑法: pytest src-tauri/scripts/test_parse_file.py
依赖: openpyxl, pypdfium2, python-docx (上传支持的格式)
"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

THIS = Path(__file__).resolve()
PARSE_PY = THIS.parent / "parse_file.py"


def run_parse(file_path: Path) -> dict:
    """跑 parse_file.py 返 JSON dict (或 raise)."""
    res = subprocess.run(
        [sys.executable, str(PARSE_PY), str(file_path)],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        raise RuntimeError(f"parse_file.py 退出 {res.returncode}: stderr={res.stderr}")
    return json.loads(res.stdout)


# ============================================================
# Text (txt/md/log) — 最简单, 不需要外部依赖
# ============================================================

def test_text_short(tmp_path: Path) -> None:
    """短文本, 不超 PREVIEW_MAX_CHARS, 全文返回."""
    f = tmp_path / "short.txt"
    f.write_text("hello world", encoding="utf-8")
    r = run_parse(f)
    assert r["kind"] == "text"
    assert r["filename"] == "short.txt"
    assert r["meta"]["total_chars"] == 11
    assert "hello world" in r["preview_text"]


def test_text_long(tmp_path: Path) -> None:
    """长文本, 超 PREVIEW_MAX_CHARS (5000), 截 preview + 提示用 execute_code."""
    f = tmp_path / "long.txt"
    content = "a" * 8000  # 8K, 超 5K limit
    f.write_text(content, encoding="utf-8")
    r = run_parse(f)
    assert r["kind"] == "text"
    assert r["meta"]["total_chars"] == 8000
    assert "execute_code" in r["preview_text"]
    assert r["preview_chars"] < 8000  # preview 比原文小


def test_md_kind_is_text(tmp_path: Path) -> None:
    """.md 也归类 text."""
    f = tmp_path / "doc.md"
    f.write_text("# Hello\n\nWorld", encoding="utf-8")
    r = run_parse(f)
    assert r["kind"] == "text"
    assert r["ext"] == ".md"


# ============================================================
# CSV
# ============================================================

def test_csv_small(tmp_path: Path) -> None:
    """小 CSV, 全部进 preview."""
    f = tmp_path / "data.csv"
    with f.open("w", encoding="utf-8", newline="") as out:
        w = csv.writer(out)
        w.writerow(["name", "age"])
        w.writerow(["alice", "30"])
        w.writerow(["bob", "25"])
    r = run_parse(f)
    assert r["kind"] == "csv"
    assert r["meta"]["total_rows"] == 3
    assert "alice" in r["preview_text"]
    assert "bob" in r["preview_text"]


def test_csv_truncated_large(tmp_path: Path) -> None:
    """100 行 CSV, preview 只显前 N 行 + 总行数提示."""
    f = tmp_path / "big.csv"
    with f.open("w", encoding="utf-8", newline="") as out:
        w = csv.writer(out)
        w.writerow(["id", "name"])
        for i in range(100):
            w.writerow([str(i), f"name_{i}"])
    r = run_parse(f)
    assert r["kind"] == "csv"
    assert r["meta"]["total_rows"] == 101  # 100 + header
    assert "execute_code" in r["preview_text"]
    # 前 N 行肯定在 preview 里
    assert "name_0" in r["preview_text"]
    # 第 90 行多半不在 (PREVIEW_ROWS=20)
    assert "name_90" not in r["preview_text"]


# ============================================================
# Excel (5/5 鸿波核心场景)
# ============================================================

@pytest.fixture
def has_openpyxl() -> bool:
    try:
        import openpyxl  # noqa: F401, PLC0415
        return True
    except ImportError:
        pytest.skip("openpyxl 未装")


def test_excel_multi_sheet(tmp_path: Path, has_openpyxl: bool) -> None:
    """多 sheet Excel, preview 列每 sheet 列头 + 前 N 行 + 总行数."""
    from openpyxl import Workbook  # noqa: PLC0415
    f = tmp_path / "multi.xlsx"
    wb = Workbook()
    s1 = wb.active
    s1.title = "1月"
    s1.append(["date", "revenue"])
    for d in range(1, 32):
        s1.append([f"2026-01-{d:02d}", 1000 + d])
    s2 = wb.create_sheet("2月")
    s2.append(["date", "revenue"])
    for d in range(1, 29):
        s2.append([f"2026-02-{d:02d}", 1500 + d])
    wb.save(f)

    r = run_parse(f)
    assert r["kind"] == "excel"
    assert r["meta"]["total_sheets"] == 2
    assert r["meta"]["sheets"] == ["1月", "2月"]
    # 列头在 preview
    assert "date" in r["preview_text"]
    assert "revenue" in r["preview_text"]
    # sheet 名标记在 preview
    assert "## Sheet: 1月" in r["preview_text"]
    assert "## Sheet: 2月" in r["preview_text"]
    # 总行数提示在
    assert "execute_code" in r["preview_text"]


def test_excel_row_counts(tmp_path: Path, has_openpyxl: bool) -> None:
    """row_counts meta 字段正确."""
    from openpyxl import Workbook  # noqa: PLC0415
    f = tmp_path / "counts.xlsx"
    wb = Workbook()
    s = wb.active
    s.title = "Sheet1"
    s.append(["x"])
    for i in range(50):
        s.append([i])
    wb.save(f)

    r = run_parse(f)
    counts = r["meta"]["row_counts"]
    # 51 = 1 header + 50 data
    assert counts["Sheet1"] == 51


# ============================================================
# 错误路径
# ============================================================

def test_unsupported_ext(tmp_path: Path) -> None:
    """不支持的扩展名 → error JSON, 退出码 4."""
    f = tmp_path / "x.bin"
    f.write_bytes(b"\x00\x01\x02")
    res = subprocess.run(
        [sys.executable, str(PARSE_PY), str(f)],
        capture_output=True, text=True,
    )
    assert res.returncode == 4
    err = json.loads(res.stdout)
    assert "不支持" in err["error"]


def test_missing_file() -> None:
    """文件不存在 → error JSON, 退出码 3."""
    res = subprocess.run(
        [sys.executable, str(PARSE_PY), "/tmp/definitely-not-exist-xxx.txt"],
        capture_output=True, text=True,
    )
    assert res.returncode == 3
    err = json.loads(res.stdout)
    assert "不存在" in err["error"]


def test_no_args() -> None:
    """没 args → error, 退出码 2."""
    res = subprocess.run(
        [sys.executable, str(PARSE_PY)],
        capture_output=True, text=True,
    )
    assert res.returncode == 2


# ============================================================
# 关键防回归: 5/5 鸿波报"截断了不能用"
# ============================================================

def test_excel_huge_does_not_dump_full_data(tmp_path: Path, has_openpyxl: bool) -> None:
    """1000 行 Excel: preview 不应含全部数据 (那就是当年的 bug),
    应只含前 ~20 行 + 总行数提示."""
    from openpyxl import Workbook  # noqa: PLC0415
    f = tmp_path / "huge.xlsx"
    wb = Workbook()
    s = wb.active
    s.append(["id", "value"])
    for i in range(1000):
        # 用易识别的 marker 防 preview 误命中
        s.append([i, f"row_data_{i:04d}"])
    wb.save(f)

    r = run_parse(f)
    assert r["kind"] == "excel"
    # 前 20 行在 preview
    assert "row_data_0000" in r["preview_text"]
    # 第 500 行不在 preview (preview-only mode 核心)
    assert "row_data_0500" not in r["preview_text"]
    # 第 999 行不在
    assert "row_data_0999" not in r["preview_text"]
    # 提示用 execute_code
    assert "execute_code" in r["preview_text"]


# ============================================================
# BL-I4 audio + BL-I3.1 video — 5/8 ship
# ============================================================
#
# 真转写需要 ffmpeg + whisper-cli + ggml-small.bin 模型 (~466MB),
# CI 跑不到 — 这里只测 PARSERS 注册 + import + module 内可纯 Python 测的部分.
# 真音频转写测试用 manual_test_audio.sh (员工 mac 真录音 → run_parse 看输出).


def test_audio_extensions_registered() -> None:
    """所有支持的音频扩展都在 PARSERS dict, kind='audio'"""
    sys.path.insert(0, str(THIS.parent))
    import parse_file as pf
    expected = [".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg"]
    for ext in expected:
        assert ext in pf.PARSERS, f"audio 扩展 {ext} 没注册"
        kind, parser = pf.PARSERS[ext]
        assert kind == "audio"
        assert parser is pf.parse_audio_preview


def test_video_extensions_registered() -> None:
    """所有支持的视频扩展都在 PARSERS dict, kind='video'"""
    sys.path.insert(0, str(THIS.parent))
    import parse_file as pf
    expected = [".mp4", ".mov", ".m4v", ".mkv", ".webm"]
    for ext in expected:
        assert ext in pf.PARSERS, f"video 扩展 {ext} 没注册"
        kind, parser = pf.PARSERS[ext]
        assert kind == "video"
        assert parser is pf.parse_video_preview


def test_audio_video_in_full_text_extractors() -> None:
    """audio/video 在 _FULL_TEXT_EXTRACTORS 里, 大文件 (≥50KB 转写) 走 BM25 sidecar"""
    sys.path.insert(0, str(THIS.parent))
    import parse_file as pf
    assert "audio" in pf._FULL_TEXT_EXTRACTORS
    assert "video" in pf._FULL_TEXT_EXTRACTORS
    # 都指向同一个抽全文函数 (内部用 _transcribe_audio_to_text)
    assert pf._FULL_TEXT_EXTRACTORS["audio"] is pf.extract_full_text_audio
    assert pf._FULL_TEXT_EXTRACTORS["video"] is pf.extract_full_text_audio


# 10/1: 音频转写改走 afconvert + 会议组件包 (FunASR), 原来三条 "缺 ffmpeg / 缺 whisper-cli /
# 缺模型" 的测试跟着换掉。打桩照旧打 parse_file_audio (它查自己模块的全局, 见 git 历史里
# 8/15 那段说明), 这里改打 Path.home —— 两个模块用的是同一个 pathlib.Path 类。


def _fake_install(home: Path, python: Path) -> None:
    root = home / ".catfish" / "meeting-asr"
    (root / "models-1.0.0" / "asr").mkdir(parents=True)
    (root / "current.json").write_text(json.dumps({
        "version": "1.0.0", "python": str(python), "models": str(root / "models-1.0.0"),
    }), encoding="utf-8")


def test_transcribe_without_meeting_pack_says_install_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sys.path.insert(0, str(THIS.parent))
    import parse_file as pf
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    with pytest.raises(RuntimeError, match=r"会议组件包"):
        pf._transcribe_audio_to_text(Path("/tmp/fake.mp3"))


def test_meeting_pack_with_deleted_venv_counts_as_not_installed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sys.path.insert(0, str(THIS.parent))
    import parse_file_audio as pfa
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    _fake_install(tmp_path, tmp_path / "gone" / "python")
    assert pfa._meeting_asr_install() is None


@pytest.mark.skipif(not Path("/usr/bin/afconvert").exists(), reason="afconvert 只在 macOS")
def test_transcribe_passes_args_and_reads_text(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """假的"组件包 python": 检查参数, 往 --out 写结果。真模型的端到端在 Rust 的 #[ignore] 测试里。"""
    import wave
    sys.path.insert(0, str(THIS.parent))
    import parse_file_audio as pfa
    fake_py = tmp_path / "python"
    fake_py.write_text(
        "#!/bin/sh\n"
        'while [ $# -gt 0 ]; do case "$1" in --out) out="$2";; --speakers) spk="$2";; esac; shift; done\n'
        '[ "$spk" = "0" ] || exit 9\n'
        'printf \'{"text": "会议纪要测试"}\' > "$out"\n',
        encoding="utf-8",
    )
    fake_py.chmod(0o755)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    _fake_install(tmp_path, fake_py)
    src = tmp_path / "in.wav"
    with wave.open(str(src), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"\x00\x00" * 44100 * 2)
    text, meta = pfa._transcribe_audio_to_text(src)
    assert text == "会议纪要测试"
    assert abs(meta["duration_sec"] - 2.0) < 0.05
    assert "funasr" in meta["model"]


def test_windows_decodes_with_pack_pyav_then_transcribes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """10/2: Windows 没有 afconvert → 用组件包 python 跑 meeting_asr.py --decode (PyAV), 再转写。

    mac / Linux 上模拟: 没有 afconvert + 当成 Windows; 假"组件包 python"是个小 Python 脚本,
    --decode 时写一个 1.5 秒的 16k wav, 转写时写结果。
    """
    sys.path.insert(0, str(THIS.parent))
    import parse_file_audio as pfa
    calls = tmp_path / "calls.txt"
    fake_py = tmp_path / "python"
    fake_py.write_text(
        f"#!{sys.executable}\n"
        "import sys, wave, json\n"
        "a = sys.argv[1:]\n"
        f"open({str(calls)!r}, 'a', encoding='utf-8').write(' '.join(a) + '\\n')\n"
        "out = a[a.index('--out') + 1]\n"
        "if '--decode' in a:\n"
        "    w = wave.open(out, 'wb'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)\n"
        "    w.writeframes(b'\\x00\\x00' * 24000); w.close()\n"
        "else:\n"
        "    assert a[a.index('--speakers') + 1] == '0'\n"
        "    open(out, 'w', encoding='utf-8').write(json.dumps({'text': 'Windows 会议'}))\n",
        encoding="utf-8",
    )
    fake_py.chmod(0o755)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(pfa, "_has_afconvert", lambda: False)
    monkeypatch.setattr(pfa, "_is_windows", lambda: True)
    _fake_install(tmp_path, fake_py)
    src = tmp_path / "memo.m4a"
    src.write_bytes(b"not really m4a")

    text, meta = pfa._transcribe_audio_to_text(src)

    assert text == "Windows 会议"
    assert abs(meta["duration_sec"] - 1.5) < 0.05
    first, second = calls.read_text(encoding="utf-8").splitlines()
    assert "--decode" in first and str(src) in first, "第一步是用组件包 PyAV 解码原文件"
    assert "--audio-file" in second


def test_unsupported_platform_says_so(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sys.path.insert(0, str(THIS.parent))
    import parse_file_audio as pfa
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(pfa, "_has_afconvert", lambda: False)
    monkeypatch.setattr(pfa, "_is_windows", lambda: False)
    _fake_install(tmp_path, Path(sys.executable))
    with pytest.raises(RuntimeError, match="macOS / Windows"):
        pfa._transcribe_audio_to_text(tmp_path / "x.mp3")
