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
    base_endpoints: dict[str, dict] = baseline_data.get("endpoints", {})

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
    args = ap.parse_args()

    if not args.stats.exists():
        print(f"[err] stats {args.stats} 不存在", file=sys.stderr)
        return 2

    result = parse_stats_csv(args.stats)
    if not result.endpoints and not result.aggregated:
        print(f"[err] stats {args.stats} 解出 0 endpoint — locust 没跑/失败", file=sys.stderr)
        return 2

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
