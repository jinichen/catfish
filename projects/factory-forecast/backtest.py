"""月度需求预测回测 (试点方案 4.4 / 7.3 的做法)。

用法:
    python backtest.py                       # 用模拟的 20 种物料跑
    python backtest.py --csv 历史.csv        # 用真实数据: 列 = item, month(YYYY-MM), qty
    选项: --origins 12  --horizons 1,2  --out 输出目录

做什么:
  1. 滚动切点: 最后 N 个月, 每个月一个切点; 切点前的数据当历史, 预测之后第 h 个月。
     每个切点只用当时之前的数据 (防泄露)。
  2. 每种方法同样切点跑:
       A1 基线·去年同期   A2 基线·近 3 月平均
       B  统计·指数平滑 (ETS, 加法趋势阻尼 + 加法季节; 历史不足两年时退为无季节)
       D  预训练·Chronos-Bolt small (零训练)
       E  组合·B 与 D 的中位数 (区间取两者分位数的平均)
  3. 指标: WAPE、偏差、10%-90% 区间覆盖率、相对基线 (取两种基线里更好的那个)。
  4. 逐品项选方法: WAPE 最低且优于基线; 都赢不了就用基线; 偏差 >10% 或覆盖率偏离 80% 太多标「需复查」。
"""
from __future__ import annotations

import argparse
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

Q_LO, Q_HI = 0.1, 0.9


# ── 数据 ──────────────────────────────────────────────────────────────

def simulate(n_months: int = 36, seed: int = 7) -> dict[str, np.ndarray]:
    """20 种物料, 几类工厂里常见的需求形态。"""
    rng = np.random.default_rng(seed)
    t = np.arange(n_months)
    season = lambda amp, phase: 1 + amp * np.sin(2 * np.pi * (t + phase) / 12)
    items: dict[str, np.ndarray] = {}
    for i in range(8):   # 稳定 + 季节
        items[f"季节型-{i+1}"] = 400 * (1 + i * 0.3) * season(0.25, i) * rng.normal(1, 0.07, n_months)
    for i in range(4):   # 有趋势
        items[f"增长型-{i+1}"] = 300 * (1 + 0.025 * t * (1 if i % 2 == 0 else -0.6)) * season(0.15, i) * rng.normal(1, 0.08, n_months)
    for i in range(4):   # 噪声大
        items[f"波动型-{i+1}"] = 200 * season(0.1, i) * rng.normal(1, 0.30, n_months)
    for i in range(2):   # 间歇需求: 很多月为 0
        hit = rng.random(n_months) < 0.35
        items[f"间歇型-{i+1}"] = np.where(hit, rng.gamma(2.0, 40, n_months), 0.0)
    for i in range(2):   # 第 28 个月起水平突变 (如新客户上量 / 产品停用)
        level = np.where(t < 28, 500, 500 * (1.6 if i == 0 else 0.5))
        items[f"突变型-{i+1}"] = level * season(0.1, i) * rng.normal(1, 0.06, n_months)
    return {k: np.clip(v, 0, None).round() for k, v in items.items()}


def load_csv(path: str) -> dict[str, np.ndarray]:
    df = pd.read_csv(path)
    df["month"] = pd.PeriodIndex(df["month"], freq="M")
    out = {}
    for item, g in df.groupby("item"):
        s = g.groupby("month")["qty"].sum()
        full = pd.period_range(s.index.min(), s.index.max(), freq="M")
        out[str(item)] = s.reindex(full, fill_value=0).to_numpy(dtype=float)
    return out


# ── 方法: 输入历史 hist, 预测第 h 步, 返回 (点预测, 下分位, 上分位) ────────

def base_seasonal(hist: np.ndarray, h: int):
    return (hist[-12 + h - 1] if len(hist) >= 12 else hist[-1]), np.nan, np.nan


def base_mean3(hist: np.ndarray, h: int):
    return hist[-3:].mean(), np.nan, np.nan


FALLBACKS: dict[str, int] = {}


def ets(hist: np.ndarray, h: int):
    """自动 ETS: 在几种常用配置里按 AICc 选最好的 (标准做法); 全部失败才退到近 3 月平均, 并计数。"""
    from statsmodels.tsa.exponential_smoothing.ets import ETSModel
    y = pd.Series(hist, index=pd.period_range("2000-01", periods=len(hist), freq="M"))
    configs = [("add", True, None), (None, False, None)]
    if len(hist) >= 24:
        configs = [("add", True, "add"), (None, False, "add")] + configs
    best = None
    for trend, damped, seasonal in configs:
        try:
            fit = ETSModel(y, error="add", trend=trend, damped_trend=damped, seasonal=seasonal,
                           seasonal_periods=12 if seasonal else None).fit(disp=False)
            if np.isfinite(fit.aicc) and (best is None or fit.aicc < best.aicc):
                best = fit
        except Exception:
            continue
    if best is None:
        FALLBACKS["B 统计·ETS"] = FALLBACKS.get("B 统计·ETS", 0) + 1
        return base_mean3(hist, h)
    sf = best.get_prediction(start=len(hist), end=len(hist) + h - 1).summary_frame(alpha=1 - (Q_HI - Q_LO))
    row = sf.iloc[h - 1]
    return max(row["mean"], 0.0), max(row["pi_lower"], 0.0), max(row["pi_upper"], 0.0)


class Chronos:
    def __init__(self, name: str = "amazon/chronos-bolt-small"):
        import torch
        from chronos import BaseChronosPipeline
        torch.set_num_threads(4)
        self.torch = torch
        self.pipe = BaseChronosPipeline.from_pretrained(name, device_map="cpu", torch_dtype=torch.float32)

    def batch(self, hists: list[np.ndarray], h: int):
        q, _ = self.pipe.predict_quantiles([self.torch.tensor(x, dtype=self.torch.float32) for x in hists],
                                           prediction_length=h, quantile_levels=[Q_LO, 0.5, Q_HI])
        q = q[:, h - 1, :].numpy().clip(min=0)
        return [(r[1], r[0], r[2]) for r in q]


# ── 回测 ──────────────────────────────────────────────────────────────

def run(series: dict[str, np.ndarray], n_origins: int, horizons: list[int]) -> pd.DataFrame:
    chronos = Chronos()
    rows = []
    n = min(len(v) for v in series.values())
    for h in horizons:
        for cut in range(n - n_origins - h + 1, n - h + 1):     # 切点: 用 [0, cut) 预测第 cut+h-1 个月
            target = cut + h - 1
            names = list(series)
            hists = [series[k][:cut] for k in names]
            ch = chronos.batch(hists, h)
            for k, hist, c in zip(names, hists, ch):
                actual = series[k][target]
                preds = {
                    "A1 基线·去年同期": base_seasonal(hist, h),
                    "A2 基线·近3月平均": base_mean3(hist, h),
                    "B 统计·ETS": ets(hist, h),
                    "D 预训练·Chronos": c,
                }
                b, d = preds["B 统计·ETS"], preds["D 预训练·Chronos"]
                preds["E 组合·B+D"] = (float(np.median([b[0], d[0]])), (b[1] + d[1]) / 2, (b[2] + d[2]) / 2)
                for m, (p, lo, hi) in preds.items():
                    rows.append(dict(horizon=h, cut=cut, item=k, method=m, actual=actual, pred=p, lo=lo, hi=hi))
    return pd.DataFrame(rows)


def score(df: pd.DataFrame) -> pd.DataFrame:
    def agg(g):
        a, p = g["actual"], g["pred"]
        denom = a.sum() or np.nan
        cov = ((a >= g["lo"]) & (a <= g["hi"])).mean() if g["lo"].notna().all() else np.nan
        return pd.Series({"WAPE": (a - p).abs().sum() / denom, "偏差": (p - a).sum() / denom,
                          "覆盖率": cov, "n": len(g)})
    return df.groupby(["horizon", "item", "method"]).apply(agg, include_groups=False).reset_index()


def choose(sc: pd.DataFrame) -> pd.DataFrame:
    out = []
    for (h, item), g in sc.groupby(["horizon", "item"]):
        g = g.set_index("method")
        base = g.loc[[m for m in g.index if m.startswith("A")], "WAPE"].min()
        cands = g.loc[[m for m in g.index if not m.startswith("A")]]
        best = cands["WAPE"].idxmin()
        if cands.loc[best, "WAPE"] < base:
            pick, r = best, cands.loc[best]
        else:
            pick = g.loc[[m for m in g.index if m.startswith("A")], "WAPE"].idxmin() + "（无方法胜出）"
            r = g.loc[pick.split("（")[0]]
        flag = []
        if abs(r["偏差"]) > 0.10: flag.append(f"偏差{r['偏差']:+.0%}")
        if pd.notna(r["覆盖率"]) and abs(r["覆盖率"] - 0.8) > 0.2: flag.append(f"覆盖{r['覆盖率']:.0%}")
        out.append(dict(horizon=h, item=item, 基线WAPE=base,
                        **{m.split(" ")[0]: g.loc[m, "WAPE"] for m in g.index if not m.startswith("A")},
                        选用=pick, 选用WAPE=r["WAPE"], 相对基线=r["WAPE"] / base if base else np.nan,
                        需复查="、".join(flag)))
    return pd.DataFrame(out)


def overall(df: pd.DataFrame) -> pd.DataFrame:
    def agg(g):
        a, p = g["actual"], g["pred"]
        cov = ((a >= g["lo"]) & (a <= g["hi"])).mean() if g["lo"].notna().all() else np.nan
        return pd.Series({"WAPE": (a - p).abs().sum() / a.sum(), "偏差": (p - a).sum() / a.sum(), "覆盖率": cov})
    return df.groupby(["horizon", "method"]).apply(agg, include_groups=False).reset_index()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv")
    ap.add_argument("--origins", type=int, default=12)
    ap.add_argument("--horizons", default="1,2")
    ap.add_argument("--out", default=str(Path(__file__).with_name("backtest_out")))
    args = ap.parse_args()
    series = load_csv(args.csv) if args.csv else simulate()
    horizons = [int(x) for x in args.horizons.split(",")]
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    df = run(series, args.origins, horizons)
    took = time.time() - t0
    sc, ov = score(df), overall(df)
    pick = choose(sc)
    df.to_csv(out / "预测明细.csv", index=False)
    sc.to_csv(out / "逐品项指标.csv", index=False)
    pick.to_csv(out / "逐品项选用.csv", index=False)
    ov.to_csv(out / "整体指标.csv", index=False)

    pct = lambda x: "—" if pd.isna(x) else f"{x:.0%}"
    print(f"{len(series)} 种物料 · 切点 {args.origins} 个 · 提前期 {horizons} · 共 {len(df)} 条预测 · 用时 {took:.1f} 秒\n")
    for h in horizons:
        print(f"== 整体 (提前 {h} 个月) ==")
        for _, r in ov[ov.horizon == h].iterrows():
            print(f"  {r['method']:<16} WAPE {pct(r['WAPE']):>5}  偏差 {r['偏差']:+.0%}  区间覆盖 {pct(r['覆盖率'])}")
        p = pick[pick.horizon == h]
        print(f"\n== 逐品项选用 (提前 {h} 个月) ==")
        for _, r in p.iterrows():
            print(f"  {r['item']:<7} 基线 {pct(r['基线WAPE']):>4} | B {pct(r['B']):>4} D {pct(r['D']):>4} E {pct(r['E']):>4} "
                  f"→ {r['选用']:<14} {pct(r['选用WAPE']):>4} 相对基线 {r['相对基线']:.2f} {r['需复查']}")
        counts = p["选用"].str.split(" ").str[0].value_counts().to_dict()
        print(f"  选用统计: {counts}\n")
    print(f"回退次数 (方法失败改用基线): {FALLBACKS or 0}")
    print(f"结果已写入 {out}")


if __name__ == "__main__":
    main()
