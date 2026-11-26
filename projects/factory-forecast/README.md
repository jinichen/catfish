# 工厂预测与决策（试点准备）

小鲶在工厂做需求预测、补货与排产决策的方案和回测工具。还没有对应的具体客户，先把方法和脚本备好。

| 文件 | 内容 |
|---|---|
| `方案.html` | 完整方案（架构、方法、开源时序模型清单、计算资源实测、回测做法、试跑结果、实施路线）。在线版：https://claude.ai/artifact/TFt3JGFtuucKfASS5NLkY8 |
| `backtest.py` | 月度需求回测脚本：基线、自动 ETS、Chronos-Bolt small（零训练）、组合，滚动切点、WAPE / 偏差 / 区间覆盖率、逐品项选方法 |
| `requirements.txt` | 2026-11-26 试跑时的版本 |

## 跑法

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python backtest.py                    # 模拟 20 种物料试跑
.venv/bin/python backtest.py --csv data/历史.csv  # 真实数据：列 item, month(YYYY-MM), qty
```

- 首次运行会下载 Chronos-Bolt small 权重（约 190 MB）到 `~/.cache/huggingface`；之后可加 `HF_HUB_OFFLINE=1` 离线跑。
- 结果写到 `backtest_out/`：预测明细、逐品项指标、逐品项选用、整体指标（CSV）。
- 结尾打印「回退次数」：某方法拟合失败、改用基线的次数。不为 0 时要先查原因，否则那一列其实是基线。

## 数据规矩

真实工厂数据放 `data/`，已在 `.gitignore` 里，不进仓库。

## 2026-11-26 试跑（模拟数据）

20 种物料 × 36 个月，最后 12 个月滚动切点，提前 1 / 2 个月，共 2,400 次预测，Apple M4 CPU 41 秒。
整体 WAPE：基线 17% / 17%，ETS 13% / 18%，Chronos 11% / 12%，组合 11% / 14%。
模拟数据只证明流程跑得通，不能据此判断真实数据上哪种方法更好。详见 `方案.html` 7.6。
