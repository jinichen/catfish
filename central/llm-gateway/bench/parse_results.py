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
  0 = 硬指标通过 (延迟可能有漂移, 只报不拦 —— 见下)
  1 = 硬指标失败 (endpoint 缺失 / 失败率超绝对线 / 吞吐跌到基线一半以下)
  2 = parse 错 (stats 缺失, 格式坏, 或负载跟基线不可比)

⚠ 9/22: **延迟阈值不再决定退出码**, 降为 advisory。

为什么 —— 有实测依据, 不是嫌它烦:

  Bench Nightly #93 (bf0ea03) 和 #94 (ff30ddd) 之间, gateway 侧代码
  一个字没改 (两次之间只动了 companion-app 和 baseline.json, 都不在
  bench 栈里)。所以两次的差值就是这套 bench 的**重复性实测值**:

      p50   每个 endpoint 都 -31% ~ -37%
      p95   -29% ~ -39%
      p99   -33% ~ +11%

  10% 的阈值架在 35% 的噪声上, 报出来的东西没有信息量。#94 就是这么
  报的: 一次 p50 -35%、p95 -33%、失败率下降、吞吐 +9% 的跑, 摘要顶上
  写着「⚠️ 2 regressions」—— 两条 p99 各 +11%。

噪声不是运气, 是量出来的:

      GET /v1/catalog          读个配置列表        p50 2400ms
      GET /api/quota/me        查一次 PG           p50 2400ms
      GET /api/advisory/feed   读 JSON             p50 2400ms
      POST /v1/chat/... [TTFB] 流式转发 LLM        p50 2400ms

  一个读配置的 GET 和一个流式 LLM 调用延迟一模一样 —— 这不是各自的
  工作量, 是**同一个队列的排队时间**。旧的 1000-user 基线同样如此
  (13000/14000/13000/13000/13000)。UVICORN_WORKERS=1, 60% 流量是流式
  chat, 每条在单个 event loop 上活 ~5.5s、每 55ms 醒一次写 chunk;
  系统跑在吞吐上限附近, 这时 runner 的 CPU 快一点慢一点会被排队放大
  成几十个百分点。compose 还给 gateway 要了 cpus:'4.0', 而 locust
  (跑在 host)、PG、mock upstream 全在同一台 4 核 runner 上抢。

  也就是说: 这个门禁测的是「今晚这台 runner 有多闲」, 不是 gateway
  有多快。调阈值救不了 —— 放宽到 40% 才不误报, 那时真有 30% 的退化
  也照样放过。

所以不装作还有延迟门禁。延迟照报 (而且现在**全量报**, 不只报变差的那
几条 —— 只报坏消息正是 #94 那份摘要误导人的原因), 但退出码只认那些
无论 runner 多慢都不该发生的事。真修复 (把负载降到不饱和 / 多 worker /
流式改测明确的 TTFB) 见 BACKLOG 里的 bench 饱和条目。

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
class HardFailure:
    """让 job 变红的东西。

    跟 Regression 的区别不是"更严重", 是**判据的性质不同**: Regression 比
    的是跟基线的相对变化, 而基线在这台 runner 上本身就带 35% 的噪声;
    HardFailure 比的是绝对值, 不看基线, 所以 runner 慢不慢都不影响判断。
    """

    check: str
    detail: str


# ── 硬指标的绝对阈值 ────────────────────────────────────────────────
#
# 定法: 必须低于/高于**任何一次健康跑的实测值**, 且余量要大到 runner
# 抖动够不着。实测样本 (全部是健康跑):
#
#   fail_rate  单 endpoint 最高 1.16% (#93 advisory / 6月 1000-user 跑)
#              聚合 0.47% ~ 0.86%
#   rps (聚合) 18.52 (#93) / 20.15 (#94) / 44.88 (6月 1000-user)
#
# 失败率取 5% —— 是实测上限的 4 倍多。真故障长什么样: token 校验挂了、
# upstream 连不上、PG 连接池耗尽, 这些都是几十个百分点起步, 5% 抓得住。
#
# 吞吐则**不能**写成绝对数字。第一版写了 `HARD_MIN_AGG_RPS = 10.0`,
# 立刻被单测的小样本 fixture (8.9 rps) 打红 —— 那不是测试碰巧, 是它说对了:
# 每秒多少请求是**负载的属性**, 不是这个解析器的属性。换个 users 数,
# 写死的 10 就是错的, 而且是那种没人会发现的错。
#
# 所以吞吐这条按基线的比例算: 掉到基线的一半以下才算。这是四条硬指标里
# 唯一还依赖基线的 —— 可以这么做, 是因为吞吐的实测噪声只有 8.8%
# (#93 18.52 → #94 20.15, 同代码), 砍半的带宽是它的 5 倍多, 抖不进来。
# 它抓的是"一半的请求根本没跑起来", 不是"今晚慢了点"。
HARD_MAX_FAIL_RATE = 0.05
HARD_MIN_RPS_RATIO = 0.5


@dataclass
class Result:
    endpoints: list[EndpointStat]
    aggregated: EndpointStat | None = None
    regressions: list[Regression] = field(default_factory=list)
    #: 全量 delta (含变好的)。只报变差的那几条会给人错误印象 —— #94 就是
    #: 一次全面变好的跑被摘要写成「2 regressions」。
    deltas: list[Regression] = field(default_factory=list)
    hard_failures: list[HardFailure] = field(default_factory=list)
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
        "\n  2. 按当前负载重新采基线 —— 在**同一台机器**上跑一次干净的 bench, 然后"
        f"\n     parse_results.py --stats <那次的 *_stats.csv> --baseline {baseline_path} \\"
        f"\n         --users {users} --run-time {run_time} --emit-baseline /tmp/new-baseline.json"
        "\n     人工 review /tmp/new-baseline.json 再替换。**不要**手工 cp summary 产物:"
        "\n     两者 endpoints 形状不同 (list vs 按名字索引的 dict), 拷过去会让回归检查静默失效。",
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
            entry = Regression(
                endpoint=name,
                metric=metric,
                baseline=base_v,
                current=curr_v,
                delta_pct=delta * 100,
            )
            # 两边都记: deltas 是给人看全貌的 (含变好的), regressions 是
            # 超阈值的子集。9/22 起 regressions **不再决定退出码**, 见模块
            # 顶部那段 —— 这台 runner 上同代码两次跑就能差 35%。
            result.deltas.append(entry)
            if delta > threshold:
                result.regressions.append(entry)
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


def _baseline_agg_rps(baseline_path: Path) -> float | None:
    """基线的整体吞吐。优先用 aggregated, 没有就把各 endpoint 加起来。

    老基线 (和单测的 fixture) 没有 aggregated 段, 那不是坏数据, 只是旧格式 ——
    endpoint 的 rps 加总是同一个量。两条路都走不通才返回 None。
    """
    try:
        data = json.loads(baseline_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    agg = data.get("aggregated")
    if isinstance(agg, dict) and float(agg.get("rps", 0) or 0) > 0:
        return float(agg["rps"])
    endpoints = data.get("endpoints")
    if isinstance(endpoints, dict):
        total = sum(float(v.get("rps", 0) or 0) for v in endpoints.values() if isinstance(v, dict))
        if total > 0:
            return total
    return None


def check_hard_limits(result: Result, baseline_path: Path) -> None:
    """填 result.hard_failures —— 唯一能让 job 变红的东西。

    这里每一条都是**绝对判据**, 不跟基线比。理由见模块顶部: 基线比较在这台
    runner 上的噪声有 35%, 而下面这些事情无论 runner 多慢都不该发生。

    反过来说也成立 —— 这些条件必须真的能抓住事故。写完之后是拿变异测试验
    的 (test_parse_results.py 里 test_hard_*), 不是看着觉得对。
    """
    # ① baseline 里有、这次没跑出来 = 半个栈没起来, 或者 locust 根本没打到。
    #    这种时候"没有回归"是假的绿: 没跑的东西当然不会退化。
    if result.missing_endpoints:
        result.hard_failures.append(
            HardFailure(
                check="missing_endpoints",
                detail=(
                    f"基线里有 {len(result.missing_endpoints)} 个 endpoint 这次一条请求都没跑到: "
                    + ", ".join(result.missing_endpoints)
                    + "。半个栈没起来 / locust 没打到 —— 这时候「没有回归」是假绿。"
                ),
            )
        )

    # ② 跑到了但一条都没成功, 也是同一类事 (计数为 0 的 endpoint 不会进
    #    missing, 但同样意味着这块没被测到)。
    for ep in result.endpoints:
        if ep.request_count <= 0:
            result.hard_failures.append(
                HardFailure(
                    check="zero_requests",
                    detail=f"{ep.name} 请求数为 0 —— 这个 endpoint 这次完全没被压到。",
                )
            )

    # ③ 失败率。绝对线, 跟基线无关 —— token 校验挂了、upstream 连不上、
    #    连接池耗尽, 都是几十个百分点。
    for ep in result.endpoints:
        if ep.fail_rate > HARD_MAX_FAIL_RATE:
            result.hard_failures.append(
                HardFailure(
                    check="fail_rate",
                    detail=(
                        f"{ep.name} 失败率 {ep.fail_rate*100:.2f}% "
                        f"超过绝对上限 {HARD_MAX_FAIL_RATE*100:.0f}% "
                        f"({ep.failure_count}/{ep.request_count})。"
                    ),
                )
            )
    if result.aggregated and result.aggregated.fail_rate > HARD_MAX_FAIL_RATE:
        result.hard_failures.append(
            HardFailure(
                check="fail_rate",
                detail=(
                    f"整体失败率 {result.aggregated.fail_rate*100:.2f}% "
                    f"超过绝对上限 {HARD_MAX_FAIL_RATE*100:.0f}%。"
                ),
            )
        )

    # ④ 吞吐。runner 慢会让延迟涨, 但不会让吞吐掉一半 —— 掉一半是"一半的
    #    请求根本没跑起来"。底线按基线比例算, 理由见 HARD_MIN_RPS_RATIO。
    base_rps = _baseline_agg_rps(baseline_path)
    if result.aggregated is None:
        # locust 的 csv 没有 Aggregated 行 —— 要么 csv 被截断了, 要么根本没跑完。
        result.hard_failures.append(
            HardFailure(
                check="no_aggregated_row",
                detail="stats csv 里没有 Aggregated 行 —— 这次跑没跑完 / csv 被截断。",
            )
        )
    elif base_rps is None:
        # 降级必须出声: 少了这条检查要让人知道, 而不是静悄悄少守一样东西。
        print(
            f"[warn] 基线 {baseline_path} 里既没有 aggregated.rps 也没有可加总的 "
            "endpoint rps —— **吞吐这条硬指标这次没有守**。"
            "\n       重采一次基线 (--emit-baseline) 就会带上 aggregated 段。",
            file=sys.stderr,
        )
    elif result.aggregated.rps < base_rps * HARD_MIN_RPS_RATIO:
        result.hard_failures.append(
            HardFailure(
                check="throughput",
                detail=(
                    f"整体吞吐 {result.aggregated.rps:.2f} RPS, 不到基线 "
                    f"{base_rps:.2f} RPS 的 {HARD_MIN_RPS_RATIO*100:.0f}% "
                    f"(下限 {base_rps * HARD_MIN_RPS_RATIO:.2f})。"
                    "吞吐的实测噪声只有 8.8%, 掉这么多不是 runner 慢, "
                    "是有相当一部分请求根本没跑起来。"
                ),
            )
        )


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
        # 9/22: 延迟门禁是 advisory —— 消费 summary.json 的人 (以后的仪表盘/
        # 脚本) 不该再把 regressions 非空当成"坏了"。写在数据里, 不只写在注释里。
        "latency_gate": "advisory",
        "hard_limits": {
            "max_fail_rate": HARD_MAX_FAIL_RATE,
            "min_rps_ratio_vs_baseline": HARD_MIN_RPS_RATIO,
        },
        "hard_failures": [
            {"check": h.check, "detail": h.detail} for h in result.hard_failures
        ],
        "deltas": [
            {
                "endpoint": d.endpoint,
                "metric": d.metric,
                "baseline": d.baseline,
                "current": d.current,
                "delta_pct": round(d.delta_pct, 2),
            }
            for d in result.deltas
        ],
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
    # ① 硬指标先说 —— 这是唯一决定红绿的东西, 必须在最上面。
    hard = summary.get("hard_failures", [])
    if hard:
        lines.append(f"### ❌ {len(hard)} 项硬指标不过 (job 因此变红)")
        for h in hard:
            lines.append(f"- **{h['check']}** — {h['detail']}")
        lines.append("")
    else:
        lines.append("### ✅ 硬指标全过")
        lines.append(
            f"（失败率 < {summary['hard_limits']['max_fail_rate']*100:.0f}%、"
            f"整体吞吐 > 基线的 {summary['hard_limits']['min_rps_ratio_vs_baseline']*100:.0f}%、"
            "基线里的 endpoint 都跑到了）"
        )
        lines.append("")

    # ② 延迟部分。**全量列出**, 不只列变差的 ——
    #    #94 就是一次 p50 全面 -35% 的跑, 被摘要写成「⚠️ 2 regressions」,
    #    因为当时只渲染超阈值的那几条。报告本身把人误导了。
    deltas = summary.get("deltas", [])
    if deltas:
        regs = summary.get("regressions", [])
        lines.append(
            f"### 📊 延迟 vs 基线 — **仅供参考, 不决定红绿** "
            f"({len(regs)} 项超 {summary['threshold_pct']:.0f}%)"
        )
        lines.append(
            "> 这台 runner 上, **同一份 gateway 代码**两次跑的 p50 就能差 35% "
            "(实测: Bench Nightly #93 vs #94, 两次之间 gateway 侧零改动)。"
            "五个 endpoint 无论干什么活延迟都一样 —— 测到的是队列, 不是代码。"
            "所以下表用来看趋势, 不用来判成败。真修复见 BACKLOG 的 bench 饱和条目。"
        )
        lines.append("")
        lines.append("| Endpoint | Metric | Baseline | Current | Δ |")
        lines.append("|---|---|---:|---:|---:|")
        for d in deltas:
            mark = "⚠️ " if d["delta_pct"] > summary["threshold_pct"] else ""
            lines.append(
                f"| `{d['endpoint']}` | {d['metric']} | "
                f"{d['baseline']:.0f} | {d['current']:.0f} | "
                f"{mark}{d['delta_pct']:+.1f}% |"
            )
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
        # ⚠ 下面两道拦的是同一件事: **别把一次坏的跑记成"正常"**。基线的全部
        #   价值在于它代表健康状态, 一旦记错, 门禁不是失灵而是反过来背书。
        #
        # 1) 没有 config 的基线, check_config_match 会整条跳过 (它只在两边都
        #    不是 None 时才比)。也就是说漏传 --users/--run-time 采出来的基线,
        #    永远不会因为负载对不上被拒 —— 正是 6/22 那份基线埋了三个月的坑,
        #    只是换成了"null 对什么都不冲突"的形式。
        if args.users is None or args.run_time is None:
            print(
                "[err] --emit-baseline 必须同时给 --users 和 --run-time。\n"
                "      locust 的 csv 不记负载参数, 只有调用方知道。少了它们,\n"
                "      采出来的基线 config 是 null, 而 check_config_match 对 null\n"
                "      一律放过 —— 门禁看着在, 实际上永远不会拒绝任何负载。",
                file=sys.stderr,
            )
            return 2

        # 2) 一个请求都没完成的跑不能当基线。bench-nightly 在 GitHub runner 上
        #    连红 6 次的症状恰恰是"300 用户 3 分钟零个请求完成", 统计表有行但
        #    计数全 0。拿它采基线会写出 p50=0 / rps=0, 之后每次都是无限倍回归
        #    (吵), 而 rps 那条永远不会报 (漏)。
        total_requests = sum(ep.request_count for ep in result.endpoints)
        if result.aggregated is not None:
            total_requests = max(total_requests, result.aggregated.request_count)
        if total_requests <= 0:
            print(
                f"[err] {args.stats} 里一个完成的请求都没有 (总请求数 0), 拒绝当基线。\n"
                "      这次 bench 是失败的, 不是跑得快。先查 locust/gateway 为什么零完成\n"
                "      (bench-nightly.yml 里那道单发流式探针就是为这个加的), 跑出真数据再采。",
                file=sys.stderr,
            )
            return 2

        args.emit_baseline.parent.mkdir(parents=True, exist_ok=True)
        args.emit_baseline.write_text(
            json.dumps(render_baseline(result, args.users, args.run_time),
                       indent=2, ensure_ascii=False) + "\n"
        )
        print(f"[ok] 新基线已写到 {args.emit_baseline} —— 请人工 review 后再替换正式 baseline.json")
        return 0

    check_config_match(args.baseline, args.users, args.run_time)
    compare(result, args.baseline, args.threshold)
    check_hard_limits(result, args.baseline)
    summary = render_summary(result, args.threshold, args.baseline)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    if args.md:
        args.md.parent.mkdir(parents=True, exist_ok=True)
        args.md.write_text(render_markdown(summary))

    print(json.dumps(summary, indent=2, ensure_ascii=False))

    # 延迟漂移照说, 但不决定退出码。不出声的降级比响亮的失败更糟 —— 如果
    # 这里静悄悄地 return 0, 下一个人会以为延迟还有人守着。
    if result.regressions:
        print(
            f"[advisory] {len(result.regressions)} 项延迟指标超过 "
            f"{args.threshold*100:.0f}% —— **不拦**。"
            "\n           这台 runner 上同代码两次跑的 p50 就能差 35%, 阈值比噪声小,"
            "\n           报出来的东西没有信息量 (依据见 parse_results.py 顶部)。"
            "\n           延迟趋势看 summary.md 的全量表。",
            file=sys.stderr,
        )

    if result.hard_failures:
        print(
            f"[err] {len(result.hard_failures)} 项硬指标不过:",
            file=sys.stderr,
        )
        for h in result.hard_failures:
            print(f"  · [{h.check}] {h.detail}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
