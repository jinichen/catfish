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


# ─── 重采基线 (9/19) ────────────────────────────────────────


def test_emit_baseline_shape_is_what_compare_actually_reads(tmp_path: Path):
    """--emit-baseline 产出的东西必须真能被 compare() 读。

    9/19: baseline.json 自己的 _comment 教人

        cp bench/result_summary.json bench/baseline.json

    **那是坏的**。两边 endpoints 形状不一样:

        summary   → list,  每项带 name
        baseline  → dict,  按名字索引

    照着 cp 之后 `name not in base_endpoints` 对 list 永远成立, 每个 endpoint
    都被判"新增", 回归检查一条都不报 —— 门禁还在但已经空了, 而且毫无迹象。
    """
    stats = tmp_path / "run_stats.csv"
    stats.write_text(STATS_CSV, encoding="utf-8")
    out = tmp_path / "new-baseline.json"
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--stats", str(stats),
         "--baseline", str(tmp_path / "nonexistent.json"),
         "--users", "300", "--run-time", "3m",
         "--emit-baseline", str(out)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    data = json.loads(out.read_text())

    assert isinstance(data["endpoints"], dict), "endpoints 必须是按名字索引的 dict"
    assert "GET /api/quota/me" in data["endpoints"]
    # config 不能少 —— 少了 check_config_match 就是摆设
    assert data["config"]["users"] == 300
    assert data["config"]["run_time"] == "3m"
    assert data.get("captured_at")


def test_emitted_baseline_round_trips_through_compare(tmp_path: Path):
    """拿产出的基线立刻再比一次, 必须 exit 0 —— 自己跟自己比不该有回归。"""
    stats = tmp_path / "run_stats.csv"
    stats.write_text(STATS_CSV, encoding="utf-8")
    base = tmp_path / "b.json"
    subprocess.run(
        [sys.executable, str(SCRIPT), "--stats", str(stats), "--baseline", str(base),
         "--users", "300", "--run-time", "3m", "--emit-baseline", str(base)],
        capture_output=True, text=True, check=True,
    )
    r = run(tmp_path, base, "--users", "300", "--run-time", "3m")
    assert r.returncode == 0, f"自己跟自己比居然报回归: {r.stdout}{r.stderr}"


def test_a_summary_json_pasted_as_baseline_is_refused(tmp_path: Path):
    """有人真照老注释 cp 了 summary.json 过来 —— 必须当场红, 不能静默零回归。"""
    bad = tmp_path / "baseline.json"
    bad.write_text(json.dumps({
        "endpoints": [{"name": "GET /api/quota/me", "p50_ms": 120}],   # list, 错的形状
    }), encoding="utf-8")
    r = run(tmp_path, bad, "--users", "300", "--run-time", "3m")
    assert r.returncode == 2, "形状不对却放行了 —— 门禁会静默失效"
    assert "endpoints" in r.stderr and "dict" in r.stderr
    assert "--emit-baseline" in r.stderr, "得告诉人正确做法"


def test_emit_without_config_flags_is_refused(tmp_path: Path):
    """漏传 --users/--run-time 采基线 → 拒绝。

    不是洁癖。check_config_match 只在**两边都不是 None** 时才比负载:

        if users is not None and cfg.get("users") is not None and ...

    所以 config 是 null 的基线永远不会因为负载对不上被拒。那正是 6/22 那份
    基线埋了三个月的坑, 只是换了个形状 —— 从"记了个没人跑的负载"变成
    "什么都没记, 于是跟任何负载都不冲突"。后者更难发现。
    """
    stats = tmp_path / "run_stats.csv"
    stats.write_text(STATS_CSV, encoding="utf-8")
    out = tmp_path / "new-baseline.json"
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--stats", str(stats),
         "--baseline", str(tmp_path / "nonexistent.json"),
         "--emit-baseline", str(out)],
        capture_output=True, text=True,
    )
    assert r.returncode == 2, "没给负载参数也让采基线了 —— 采出来的门禁是空的"
    assert not out.exists(), "拒绝了却还是把文件写出去了"
    assert "--users" in r.stderr and "--run-time" in r.stderr


def test_emit_from_a_zero_request_run_is_refused(tmp_path: Path):
    """零个请求完成的跑不能当基线。

    bench-nightly 在 GitHub runner 上连红 6 次, 症状就是"300 用户 3 分钟零个
    请求完成" —— 统计表有行, 计数全 0。拿它采基线会写出 p50=0 / rps=0:
    之后每次跑都是无限倍延迟回归 (吵到没人看), 而 rps 那条永远不会报 (漏)。
    一次失败的压测被记成"正常", 比没有基线坏得多。
    """
    stats = tmp_path / "run_stats.csv"
    stats.write_text(
        "Type,Name,Request Count,Failure Count,Median Response Time,Average Response Time,"
        "Min Response Time,Max Response Time,Average Content Size,Requests/s,Failures/s,"
        "50%,66%,75%,80%,90%,95%,98%,99%,99.9%,99.99%,100%\n"
        "GET,/api/quota/me,0,0,0,0,0,0,0,0.0,0.0,0,0,0,0,0,0,0,0,0,0,0\n"
        ",Aggregated,0,0,0,0,0,0,0,0.0,0.0,0,0,0,0,0,0,0,0,0,0,0\n",
        encoding="utf-8",
    )
    out = tmp_path / "new-baseline.json"
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--stats", str(stats),
         "--baseline", str(tmp_path / "nonexistent.json"),
         "--users", "300", "--run-time", "3m",
         "--emit-baseline", str(out)],
        capture_output=True, text=True,
    )
    assert r.returncode == 2, "零请求的跑被采成基线了"
    assert not out.exists()
    assert "0" in r.stderr


def test_the_refusal_message_itself_does_not_teach_the_broken_cp(tmp_path: Path):
    """配置不匹配那条报错**自己**不许再教 cp summary.json。

    9/19 的教训第二遍: 我改了 baseline.json 的 _comment, 也改了 render_baseline
    的 docstring, 却漏了这条 —— 而这条才是人真正会读到的那句 (门禁报红时打在
    stderr 上)。注释没人看, 报错人人看。修文档的时候先修报错。
    """
    base = write_baseline(tmp_path, users=1000, run_time="10m")
    r = run(tmp_path, base, "--users", "300", "--run-time", "3m")
    assert r.returncode == 2
    assert "cp bench/result_summary.json" not in r.stderr
    assert "--emit-baseline" in r.stderr, "拒绝了就得给出正确做法"


def test_the_shipped_baseline_comment_does_not_teach_the_broken_cp():
    """出厂 baseline.json 的注释不许再教人 cp summary.json。

    这条测的是**文档**: 上一版就是照着那句话做会坏事。留着它, 下一个人照做
    一遍, 回归检查又空一次。
    """
    shipped = json.loads((HERE / "baseline.json").read_text())
    comment = shipped.get("_comment", "")
    assert "cp bench/result_summary.json" not in comment, (
        "注释还在教 cp summary.json —— 那会让回归检查静默失效"
    )


# ─────────────────────────────────────────────────────────────────────
# 9/22: 门禁分层之后的变异测试。
#
# 背景在 parse_results.py 顶部: 延迟阈值降为 advisory (这台 runner 上同一份
# gateway 代码两次跑的 p50 就能差 35%), 红绿改由硬指标决定。
#
# 下面每条都是**变异测试**: 拿健康数据改坏一处, 断言门真的会红。只测"健康
# 数据是绿的"证明不了门还在 —— 一个 `return 0` 也能过。
#
# 每处变异前先 assert 原串在 CSV 里, 否则 replace 静默不生效, 测试就变成
# "改了个没用的东西, 还是绿的" —— 那种绿比红更危险。
# ─────────────────────────────────────────────────────────────────────

HEALTHY_CSV = """Type,Name,Request Count,Failure Count,Median Response Time,Average Response Time,Min Response Time,Max Response Time,Average Content Size,Requests/s,Failures/s,50%,66%,75%,80%,90%,95%,98%,99%,99.9%,99.99%,100%
GET,/api/quota/me,1000,5,120,130,10,400,512,8.9,0.0,120,130,140,150,180,200,220,240,300,350,400
,Aggregated,1000,5,120,130,10,400,512,8.9,0.0,120,130,140,150,180,200,220,240,300,350,400
"""


def _run_csv(tmp: Path, csv_text: str, *extra: str):
    """拿给定 csv 跑一遍, 基线是配套的健康基线。"""
    baseline = tmp / "baseline.json"
    baseline.write_text(json.dumps({
        "captured_at": "2026-09-22",
        "config": {"users": 300, "run_time": "3m"},
        "endpoints": {
            "GET /api/quota/me": {
                "p50_ms": 120, "p95_ms": 200, "p99_ms": 240,
                "rps": 8.9, "fail_rate": 0.005,
            }
        },
        "aggregated": {
            "p50_ms": 120, "p95_ms": 200, "p99_ms": 240,
            "rps": 8.9, "fail_rate": 0.005,
        },
    }), encoding="utf-8")
    stats = tmp / "run_stats.csv"
    stats.write_text(csv_text, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--stats", str(stats),
         "--baseline", str(baseline), "--users", "300", "--run-time", "3m", *extra],
        capture_output=True, text=True,
    )


def _mutate(text: str, old: str, new: str) -> str:
    """改一处并**确认真的改到了**。见本段顶部。"""
    assert old in text, f"变异没生效 —— CSV 里根本没有 {old!r}, 这条测试是假的"
    return text.replace(old, new, 1)


def test_healthy_run_is_green():
    """先钉住基准: 这份数据是健康的, 必须绿。下面的变异才有意义。"""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        r = _run_csv(Path(d), HEALTHY_CSV)
    assert r.returncode == 0, f"健康数据不该红: {r.stderr}"


def test_hard_fail_rate_over_absolute_line_is_red(tmp_path: Path):
    """失败率 5% 以上 = 真故障 (token 挂了 / upstream 断了), 必须红。"""
    # 1000 请求里 200 个失败 = 20%
    bad = _mutate(HEALTHY_CSV, "/api/quota/me,1000,5,", "/api/quota/me,1000,200,")
    bad = _mutate(bad, "Aggregated,1000,5,", "Aggregated,1000,200,")
    r = _run_csv(tmp_path, bad)
    assert r.returncode == 1, f"20% 失败率必须红, 实际 {r.returncode}"
    assert "fail_rate" in r.stderr


def test_fail_rate_just_under_the_line_stays_green(tmp_path: Path):
    """4% 不红 —— 阈值必须是它声称的那个数, 不能实际上更严。

    这条跟上一条成对: 只有上一条的话, 一个"永远红"的实现也能过。"""
    bad = _mutate(HEALTHY_CSV, "/api/quota/me,1000,5,", "/api/quota/me,1000,40,")
    bad = _mutate(bad, "Aggregated,1000,5,", "Aggregated,1000,40,")
    r = _run_csv(tmp_path, bad)
    assert r.returncode == 0, f"4% 在 5% 线以下, 不该红: {r.stderr}"


def test_hard_throughput_collapse_is_red(tmp_path: Path):
    """吞吐掉到基线一半以下 = 相当一部分请求根本没跑起来。"""
    bad = _mutate(HEALTHY_CSV, "512,8.9,0.0", "512,3.0,0.0")
    bad = _mutate(bad, "512,8.9,0.0", "512,3.0,0.0")  # Aggregated 行
    r = _run_csv(tmp_path, bad)
    assert r.returncode == 1, f"吞吐从 8.9 掉到 3.0 必须红, 实际 {r.returncode}"
    assert "throughput" in r.stderr


def test_throughput_dip_within_noise_stays_green(tmp_path: Path):
    """掉 10% 不红 —— 实测噪声就有 8.8%, 在这儿报警就又回到老问题了。"""
    bad = _mutate(HEALTHY_CSV, "512,8.9,0.0", "512,8.0,0.0")
    bad = _mutate(bad, "512,8.9,0.0", "512,8.0,0.0")
    r = _run_csv(tmp_path, bad)
    assert r.returncode == 0, f"掉 10% 在噪声范围内, 不该红: {r.stderr}"


def test_missing_endpoint_is_red(tmp_path: Path):
    """基线里有、这次一条没跑到 = 半个栈没起来。

    这条最重要: 这种情况下"没有回归"是**假绿** —— 没跑的东西当然不退化。"""
    bad = _mutate(HEALTHY_CSV, "GET,/api/quota/me,1000,5,120,130,10,400,512,8.9,0.0,120,130,140,150,180,200,220,240,300,350,400\n", "")
    r = _run_csv(tmp_path, bad)
    assert r.returncode == 1, f"endpoint 缺失必须红, 实际 {r.returncode}"
    assert "missing_endpoints" in r.stderr


def test_latency_blowup_is_reported_but_not_red(tmp_path: Path):
    """延迟涨 10 倍也**不红** —— 这是这次改动的核心, 必须钉死。

    不是说延迟不重要, 是说这台 runner 上的延迟数字没有判据价值 (依据见
    parse_results.py 顶部)。但它必须**出声**: 降级不说话比大声失败更糟。"""
    bad = _mutate(HEALTHY_CSV, ",512,8.9,0.0,120,130,140,150,180,200,220,240,",
                               ",512,8.9,0.0,1200,1300,1400,1500,1800,2000,2200,2400,")
    bad = _mutate(bad, ",512,8.9,0.0,120,130,140,150,180,200,220,240,",
                       ",512,8.9,0.0,1200,1300,1400,1500,1800,2000,2200,2400,")
    r = _run_csv(tmp_path, bad)
    assert r.returncode == 0, "延迟不再决定退出码"
    assert "advisory" in r.stderr, "降级必须出声 —— 不能静悄悄 return 0"
    assert "不拦" in r.stderr


def test_summary_md_shows_improvements_not_only_regressions(tmp_path: Path):
    """摘要要给全貌。

    #94 是一次 p50 -35% 的跑, 摘要却只写「⚠️ 2 regressions」, 因为当时只
    渲染超阈值的那几条。报告本身把人误导了, 这条钉住不许再这样。"""
    # 这次比基线快一半
    fast = _mutate(HEALTHY_CSV, ",512,8.9,0.0,120,130,140,150,180,200,220,240,",
                                ",512,8.9,0.0,60,65,70,75,90,100,110,120,")
    fast = _mutate(fast, ",512,8.9,0.0,120,130,140,150,180,200,220,240,",
                         ",512,8.9,0.0,60,65,70,75,90,100,110,120,")
    md = tmp_path / "summary.md"
    r = _run_csv(tmp_path, fast, "--md", str(md))
    assert r.returncode == 0
    body = md.read_text()
    assert "-50.0%" in body, "变好的那些也要列出来, 不能只列变差的"
    assert "不决定红绿" in body, "摘要必须说清延迟只是参考"


def test_summary_json_says_latency_gate_is_advisory(tmp_path: Path):
    """机器可读的那份也要说。以后接仪表盘的人不会来读注释。"""
    out = tmp_path / "summary.json"
    r = _run_csv(tmp_path, HEALTHY_CSV, "--out", str(out))
    assert r.returncode == 0
    data = json.loads(out.read_text())
    assert data["latency_gate"] == "advisory"
    assert "hard_failures" in data
