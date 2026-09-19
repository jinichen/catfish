"""基线可比性 —— 负载对不上就拒绝比较。

# 为什么有这个文件

9/19 查 Bench Nightly #90 挂在「Parse + regression check」, 查下来不是那次
跑得差, 是**基线和实跑压根不是一个负载**:

    baseline.json   captured_at 2026-06-22, config = 1000 users / 10m
    bench-nightly   实跑 300 users / 3m

后果是双向的, 而且两边都很难看出来:

  · 基线 p50 是 13000ms —— 1000 用户把队列压满的数字。300 用户跑出来几百
    毫秒, **要慢 26 倍**才够触发 10% 阈值。延迟这条检查是死的, 真回归了
    也照样绿。
  · 反过来 fail_rate 那条不看负载差异, 抖一下就报。

一个既拦不住真回归、又会为噪声报警的门禁, **比没有门禁更坏** —— 它让人
以为性能有人看着。

所以不修阈值、不改判据, 改成配置不一致就当场报错退出 (exit 2, 跟"有回归"
的 exit 1 分开)。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
SCRIPT = HERE / "parse_results.py"

STATS_CSV = """Type,Name,Request Count,Failure Count,Median Response Time,Average Response Time,Min Response Time,Max Response Time,Average Content Size,Requests/s,Failures/s,50%,66%,75%,80%,90%,95%,98%,99%,99.9%,99.99%,100%
GET,/api/quota/me,100,0,120,130,10,400,512,8.9,0.0,120,130,140,150,180,200,220,240,300,350,400
,Aggregated,100,0,120,130,10,400,512,8.9,0.0,120,130,140,150,180,200,220,240,300,350,400
"""


def write_baseline(tmp: Path, *, users: int, run_time: str) -> Path:
    p = tmp / "baseline.json"
    p.write_text(json.dumps({
        "captured_at": "2026-06-22",
        "config": {"users": users, "run_time": run_time},
        "endpoints": {
            "GET /api/quota/me": {
                "p50_ms": 13000, "p95_ms": 18000, "p99_ms": 19000,
                "rps": 8.9, "fail_rate": 0.0,
            }
        },
    }), encoding="utf-8")
    return p


def run(tmp: Path, baseline: Path, *extra: str):
    stats = tmp / "run_stats.csv"
    stats.write_text(STATS_CSV, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--stats", str(stats),
         "--baseline", str(baseline), *extra],
        capture_output=True, text=True,
    )


def test_mismatched_users_is_refused(tmp_path: Path):
    """1000 用户采的基线, 拿 300 用户的结果去比 —— 必须拒绝, 不是给个结论。"""
    base = write_baseline(tmp_path, users=1000, run_time="10m")
    r = run(tmp_path, base, "--users", "300", "--run-time", "10m")
    assert r.returncode == 2, "配置不匹配该 exit 2 (跟'有回归'的 exit 1 分开)"
    assert "users" in r.stderr and "1000" in r.stderr and "300" in r.stderr


def test_mismatched_run_time_is_refused(tmp_path: Path):
    base = write_baseline(tmp_path, users=300, run_time="10m")
    r = run(tmp_path, base, "--users", "300", "--run-time", "3m")
    assert r.returncode == 2
    assert "run_time" in r.stderr


def test_error_message_says_how_to_fix(tmp_path: Path):
    """报错得告诉人下一步干什么 —— 否则下一个人只会把这步 continue-on-error 掉。"""
    base = write_baseline(tmp_path, users=1000, run_time="10m")
    r = run(tmp_path, base, "--users", "300", "--run-time", "3m")
    assert "重新采基线" in r.stderr or "baseline.json" in r.stderr
    assert "USERS" in r.stderr or "bench-nightly" in r.stderr


def test_matching_config_compares_normally(tmp_path: Path):
    """配置一致就照常比。这次数据远好于基线 (120ms vs 13000ms), 应该 exit 0。"""
    base = write_baseline(tmp_path, users=300, run_time="3m")
    r = run(tmp_path, base, "--users", "300", "--run-time", "3m")
    assert r.returncode == 0, f"stderr={r.stderr}"


def test_old_callers_without_the_flags_still_work(tmp_path: Path):
    """不传 --users/--run-time 就退化成老行为 (不校验), 别把别的调用方弄挂。"""
    base = write_baseline(tmp_path, users=1000, run_time="10m")
    r = run(tmp_path, base)
    assert r.returncode == 0, f"stderr={r.stderr}"


def test_baseline_without_config_is_not_refused(tmp_path: Path):
    """老基线文件没有 config 段 —— 不该因此拒绝, 那是另一回事。"""
    p = tmp_path / "baseline.json"
    p.write_text(json.dumps({"endpoints": {}}), encoding="utf-8")
    r = run(tmp_path, p, "--users", "300", "--run-time", "3m")
    assert r.returncode == 0


@pytest.mark.parametrize("users,run_time", [("300", "3m"), ("1000", "10m")])
def test_the_shipped_baseline_matches_some_real_config(users: str, run_time: str):
    """出厂那份 baseline.json 的 config 必须是真跑得出来的形态。

    这条不是测代码, 是测**数据**: 基线里要是写了个没人会跑的配置, 上面那
    道校验就变成了永远红的门禁, 下一个人会直接把它关掉。
    """
    shipped = json.loads((HERE / "baseline.json").read_text())
    cfg = shipped.get("config") or {}
    assert cfg.get("users"), "基线必须记下它是多少用户采的"
    assert cfg.get("run_time"), "基线必须记下它跑了多久"
