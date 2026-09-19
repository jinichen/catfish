#!/usr/bin/env python3
"""P3.5.61 (6/22 鸿波) — 解析 locust _stats.csv, 比 baseline 报 regression.

跟 P3.5.60 /admin/perf 同样的指标 ABI (p50/p95/p99 + RPS + fail rate). 区别:
那是生产 PG audit 聚合 (真员工流量), 这是 CI mock 流量基准 (回归卡).

Usage:
  python bench/parse_results.py \
      --stats bench/csv-1000-20260622-XXXXXX_stats.csv \
      --baseline bench/baseline.json \
      --threshold 0.10 \
      --out bench/result_summary.json

返 exit code:
  0 = 健康 / 改善
  1 = regression > threshold (CI 卡 PR)
  2 = parse 错 (stats 缺失或格式坏)

Baseline 格式 (bench/baseline.json):
  {
    "comment": "P3.5.61 baseline — 1000 users 10m mock upstream",
    "captured_at": "2026-06-22",
    "endpoints": {
      "POST /v1/chat/completions": { "p50_ms": 1200, "p95_ms": 5000, "p99_ms": 9000, "rps": 25.0, "fail_rate": 0.01 },
      ...
    }
  }
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class EndpointStat:
    name: str
    request_count: int
    failure_count: int
    rps: float
    p50_ms: float
    p95_ms: float
    p99_ms: float

    @property
    def fail_rate(self) -> float:
        if self.request_count <= 0:
            return 0.0
        return self.failure_count / self.request_count


@dataclass
class Regression:
    endpoint: str
    metric: str
    baseline: float
    current: float
    delta_pct: float


@dataclass
class Result:
    endpoints: list[EndpointStat]
    aggregated: EndpointStat | None = None
    regressions: list[Regression] = field(default_factory=list)
    new_endpoints: list[str] = field(default_factory=list)
    missing_endpoints: list[str] = field(default_factory=list)


def parse_stats_csv(path: Path) -> Result:
    """Locust _stats.csv → Result. 'Aggregated' 行被单独抓出."""
    endpoints: list[EndpointStat] = []
    aggregated: EndpointStat | None = None
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            type_ = (row.get("Type") or "").strip()
            name = (row.get("Name") or "").strip()
            if not name:
                continue
            # 行 key tolerance: Locust 不同版本字段名微差
            try:
                stat = EndpointStat(
                    name=f"{type_} {name}".strip() if type_ else name,
                    request_count=int(float(row.get("Request Count", 0) or 0)),
                    failure_count=int(float(row.get("Failure Count", 0) or 0)),
                    rps=float(row.get("Requests/s", 0) or 0),
                    p50_ms=float(row.get("50%", 0) or 0),
                    p95_ms=float(row.get("95%", 0) or 0),
                    p99_ms=float(row.get("99%", 0) or 0),
                )
            except (ValueError, TypeError) as e:
                print(f"[warn] skipping malformed row {name}: {e}", file=sys.stderr)
                continue
            if name == "Aggregated":
                aggregated = stat
            else:
                endpoints.append(stat)
    return Result(endpoints=endpoints, aggregated=aggregated)


def check_config_match(baseline_path: Path, users: int | None, run_time: str | None) -> None:
    """跑的负载跟基线不是一个量级的话, 拒绝比较。

    9/19 查 Bench Nightly #90 挂的时候发现:

        baseline.json  captured_at 2026-06-22, config = 1000 users / 10m
        bench-nightly  实跑 300 users / 3m

    两边不是一回事, 比出来的结论**双向失真**:

      · 基线 p50 是 13000ms —— 1000 用户压满队列的数字。300 用户跑出来
        几百毫秒, 要慢 **26 倍**才够触发 10% 阈值。延迟检查等于是死的,
        真回归了也照样绿。
      · 反过来, 任何一点 fail_rate 抖动都会被判成回归, 因为那条规则不看
        负载差异。

    一个既拦不住真回归、又会为噪声报警的门禁, 比没有门禁更坏 —— 它让人
    以为性能有人看着。

    所以配置不一致就**当场报错退出**, 而不是给一个没意义的判决。修法只有
    两条, 错误信息里都写了: 要么把 nightly 跑成基线的负载, 要么按当前负载
    重新采一份基线 (人工 review, 不是脚本自动覆盖)。
    """
    if users is None and run_time is None:
        return  # 调用方没告诉我们跑的什么, 没法判 —— 老调用方兼容
    if not baseline_path.exists():
        return
    try:
        cfg = json.loads(baseline_path.read_text()).get("config") or {}
    except json.JSONDecodeError:
        return  # 解析失败交给 compare() 报, 别在这儿抢

    mismatch = []
    if users is not None and cfg.get("users") is not None and int(cfg["users"]) != int(users):
        mismatch.append(f"users: 基线 {cfg['users']} vs 实跑 {users}")
    if (
        run_time is not None
        and cfg.get("run_time") is not None
        and str(cfg["run_time"]) != str(run_time)
    ):
        mismatch.append(f"run_time: 基线 {cfg['run_time']} vs 实跑 {run_time}")
    if not mismatch:
        return

    print(
        "[err] 基线和这次实跑的负载对不上, 拒绝比较:\n  "
        + "\n  ".join(mismatch)
        + f"\n\n基线采于 {json.loads(baseline_path.read_text()).get('captured_at', '?')}。"
        "\n负载不同的两次压测之间比 p50/p95 没有意义 —— 会同时**漏报真回归**"
        "\n和**为噪声报警**。两条修法二选一:"
        "\n  1. 把 bench-nightly.yml 的 USERS / RUN_TIME 改成跟基线一致"
        "\n  2. 按当前负载重新采基线: 跑一次成功的 bench 之后"
        "\n     cp bench/result_summary.json bench/baseline.json (人工 review)",
        file=sys.stderr,
    )
    sys.exit(2)


def compare(result: Result, baseline_path: Path, threshold: float) -> None:
    """diff result.endpoints vs baseline.json. 副作用: 填 result.regressions /
    new_endpoints / missing_endpoints."""
    if not baseline_path.exists():
        print(
            f"[warn] baseline {baseline_path} 不存在 — 跳 regression 检查, "
            f"先把这次 result 当 baseline 存上",
            file=sys.stderr,
        )
        return
    try:
        baseline_data = json.loads(baseline_path.read_text())
    except json.JSONDecodeError as e:
        print(f"[err] baseline 解 JSON 失败: {e}", file=sys.stderr)
        sys.exit(2)
    base_endpoints = baseline_data.get("endpoints", {})
    # 9/19: 形状不对就当场报, 别静默变成"零回归"。
    # summary.json 的 endpoints 是 list, baseline 要的是 dict —— 拿 list 当
    # dict 用的话 `name not in base_endpoints` 永远成立, 每个 endpoint 都被
    # 判成新增, 门禁还在但已经空了。这种失效没有任何外部迹象。
    if not isinstance(base_endpoints, dict):
        print(
            f"[err] baseline {baseline_path} 的 endpoints 是 "
            f"{type(base_endpoints).__name__}, 应该是按名字索引的 dict。\n"
            "      八成是手工 cp 了 summary.json —— 那两个形状不一样。\n"
            "      正确做法: parse_results.py --stats <csv> --emit-baseline <路径>",
            file=sys.stderr,
        )
        sys.exit(2)

    current_by_name = {ep.name: ep for ep in result.endpoints}

    # 检 regression — current 每个 endpoint 比 baseline 同名
    for name, ep in current_by_name.items():
        if name not in base_endpoints:
            result.new_endpoints.append(name)
            continue
        b = base_endpoints[name]
        for metric in ("p50_ms", "p95_ms", "p99_ms"):
            base_v = float(b.get(metric, 0) or 0)
            curr_v = float(getattr(ep, metric))
            if base_v <= 0:
                continue  # baseline 无, 不报
            delta = (curr_v - base_v) / base_v
            if delta > threshold:
                result.regressions.append(
                    Regression(
                        endpoint=name,
                        metric=metric,
                        baseline=base_v,
                        current=curr_v,
                        delta_pct=delta * 100,
                    )
                )
        # fail_rate 任何上升都报 (不在 threshold 范围, 因为 baseline 通常 0)
        base_fail = float(b.get("fail_rate", 0) or 0)
        if ep.fail_rate > base_fail + 0.01 and ep.fail_rate > 0.01:
            result.regressions.append(
                Regression(
                    endpoint=name,
                    metric="fail_rate",
                    baseline=base_fail,
                    current=ep.fail_rate,
                    delta_pct=((ep.fail_rate - base_fail) * 100),
                )
            )

    # missing — baseline 有但 current 没跑
    for name in base_endpoints:
        if name not in current_by_name:
            result.missing_endpoints.append(name)


def render_baseline(result: Result, users: int | None, run_time: str | None) -> dict:
    """产出一份**能直接当 baseline.json 用**的结构。

    9/19: 加这个是因为 baseline.json 自己的 _comment 写的是

        想 reset baseline: 跑成功后 cp bench/result_summary.json bench/baseline.json

    而那条**是坏的**。两边的 endpoints 形状根本不一样:

        render_summary → list, 每项带 name     [{"name": "GET /x", "p50_ms": ...}]
        compare()      → dict, 按名字取         base_endpoints[name]

    照着 cp 过去之后, `name not in base_endpoints` 对一个 list 永远成立, 于是
    每个 endpoint 都被判成"新增", **回归检查从此一条都不报** —— 门禁还在,
    但已经空了, 而且没有任何迹象。

    所以不留"照着抄"的路子, 直接给一个产出正确形状的开关。顺便把 config
    和 captured_at 一起写进去 —— 没有它们, check_config_match 就是摆设。
    """
    return {
        "_comment": (
            "nightly bench baseline。重采: parse_results.py --emit-baseline <路径> "
            "(**别**手工 cp summary.json —— 两者 endpoints 形状不同, 见 render_baseline)。"
            "采完人工 review 再替换, 不要脚本自动覆盖。"
        ),
        "captured_at": _today(),
        "captured_from": f"{users or '?'} users / {run_time or '?'}",
        "config": {"users": users, "run_time": run_time},
        "endpoints": {
            ep.name: {
                "p50_ms": ep.p50_ms,
                "p95_ms": ep.p95_ms,
                "p99_ms": ep.p99_ms,
                "rps": round(ep.rps, 2),
                "fail_rate": round(ep.fail_rate, 4),
            }
            for ep in result.endpoints
        },
        "aggregated": (
            {
                "p50_ms": result.aggregated.p50_ms,
                "p95_ms": result.aggregated.p95_ms,
                "p99_ms": result.aggregated.p99_ms,
                "rps": round(result.aggregated.rps, 2),
                "fail_rate": round(result.aggregated.fail_rate, 4),
            }
            if result.aggregated
            else None
        ),
    }


def _today() -> str:
    from datetime import date

    return date.today().isoformat()


def render_summary(result: Result, threshold: float, baseline_path: Path) -> dict:
    return {
        "threshold_pct": threshold * 100,
        "baseline_file": str(baseline_path),
        "endpoint_count": len(result.endpoints),
        "regressions": [
            {
                "endpoint": r.endpoint,
                "metric": r.metric,
                "baseline": r.baseline,
                "current": r.current,
                "delta_pct": round(r.delta_pct, 2),
            }
            for r in result.regressions
        ],
        "new_endpoints": result.new_endpoints,
        "missing_endpoints": result.missing_endpoints,
        "aggregated": (
            {
                "request_count": result.aggregated.request_count,
                "failure_count": result.aggregated.failure_count,
                "rps": round(result.aggregated.rps, 2),
                "p50_ms": result.aggregated.p50_ms,
                "p95_ms": result.aggregated.p95_ms,
                "p99_ms": result.aggregated.p99_ms,
                "fail_rate": round(result.aggregated.fail_rate, 4),
            }
            if result.aggregated
            else None
        ),
        "endpoints": [
            {
                "name": ep.name,
                "request_count": ep.request_count,
                "failure_count": ep.failure_count,
                "rps": round(ep.rps, 2),
                "p50_ms": ep.p50_ms,
                "p95_ms": ep.p95_ms,
                "p99_ms": ep.p99_ms,
                "fail_rate": round(ep.fail_rate, 4),
            }
            for ep in result.endpoints
        ],
    }


def render_markdown(summary: dict) -> str:
    """给 GH PR comment 用. 短表 + regression 高亮."""
    lines: list[str] = ["## 🔬 Bench nightly result", ""]
    agg = summary.get("aggregated")
    if agg:
        lines.extend([
            f"**Aggregated**: {agg['request_count']:,} req · {agg['rps']:.1f} RPS · "
            f"p50 {agg['p50_ms']:.0f}ms · p95 {agg['p95_ms']:.0f}ms · "
            f"p99 {agg['p99_ms']:.0f}ms · fail {agg['fail_rate']*100:.2f}%",
            "",
        ])
    regs = summary.get("regressions", [])
    if regs:
        lines.append(f"### ⚠️ {len(regs)} regression(s) (> {summary['threshold_pct']:.0f}%)")
        lines.append("| Endpoint | Metric | Baseline | Current | Δ |")
        lines.append("|---|---|---:|---:|---:|")
        for r in regs:
            lines.append(
                f"| `{r['endpoint']}` | {r['metric']} | "
                f"{r['baseline']:.0f} | {r['current']:.0f} | "
                f"**+{r['delta_pct']:.1f}%** |"
            )
        lines.append("")
    else:
        lines.append("### ✅ No regressions vs baseline")
        lines.append("")
    if summary.get("new_endpoints"):
        lines.append(f"### 🆕 New endpoints (not in baseline)")
        for n in summary["new_endpoints"]:
            lines.append(f"- `{n}`")
        lines.append("")
    if summary.get("missing_endpoints"):
        lines.append(f"### 👻 Missing (in baseline, not run)")
        for n in summary["missing_endpoints"]:
            lines.append(f"- `{n}`")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stats", required=True, type=Path, help="locust *_stats.csv")
    ap.add_argument("--baseline", required=True, type=Path, help="baseline.json")
    ap.add_argument("--threshold", type=float, default=0.10, help="regression threshold (0.10 = 10%)")
    ap.add_argument("--out", type=Path, help="JSON summary out path (optional)")
    ap.add_argument("--md", type=Path, help="markdown summary out path (optional)")
    # 9/19: 这两个是给 check_config_match 用的 —— locust 的 csv 不记负载参数,
    # 只有调用方知道自己跑的是多少用户、多久。不传就退化成老行为 (不校验)。
    ap.add_argument("--users", type=int, help="这次实跑的并发用户数 (校验基线可比性)")
    ap.add_argument("--run-time", dest="run_time", help="这次实跑的时长, 如 3m")
    ap.add_argument(
        "--emit-baseline",
        type=Path,
        help="把这次结果写成一份新的 baseline.json (形状正确, 含 config)。"
        "写完不比较、直接退出 0 —— 重采基线时用, 人工 review 后再替换正式文件。",
    )
    args = ap.parse_args()

    if not args.stats.exists():
        print(f"[err] stats {args.stats} 不存在", file=sys.stderr)
        return 2

    result = parse_stats_csv(args.stats)
    if not result.endpoints and not result.aggregated:
        print(f"[err] stats {args.stats} 解出 0 endpoint — locust 没跑/失败", file=sys.stderr)
        return 2

    if args.emit_baseline:
        args.emit_baseline.parent.mkdir(parents=True, exist_ok=True)
        args.emit_baseline.write_text(
            json.dumps(render_baseline(result, args.users, args.run_time),
                       indent=2, ensure_ascii=False) + "\n"
        )
        print(f"[ok] 新基线已写到 {args.emit_baseline} —— 请人工 review 后再替换正式 baseline.json")
        return 0

    check_config_match(args.baseline, args.users, args.run_time)
    compare(result, args.baseline, args.threshold)
    summary = render_summary(result, args.threshold, args.baseline)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    if args.md:
        args.md.parent.mkdir(parents=True, exist_ok=True)
        args.md.write_text(render_markdown(summary))

    print(json.dumps(summary, indent=2, ensure_ascii=False))

    if result.regressions:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
