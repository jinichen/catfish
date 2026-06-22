# catfish-bookkeep

本地语音记账 hermes plugin (**P3.5.75**, 6/22 鸿波).

3 个 tool 注册给 hermes (LLM 自动调):

- `bookkeep_add` 💸 — 记一笔收/支
- `bookkeep_query` 🔍 — 查历史
- `bookkeep_summarize` 📊 — 出汇总报表

数据落 `~/.catfish/bookkeep.jsonl` (append-only, **数据零出端**).

## 用法

装机:

```bash
bash deploy.sh
launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway
```

Companion chat (🎤 语音或文字):

```
user: 今天买了 5 块煎饼
→ LLM 调 bookkeep_add(kind=支出, amount=5, category=餐饮, note=煎饼)
→ ~/.catfish/bookkeep.jsonl 落一条

user: 我这周花了多少
→ LLM 调 bookkeep_query(days=7, kind=支出)
→ 列最近 7 天支出

user: 六月账单
→ LLM 调 bookkeep_summarize(start=2026-06-01, end=2026-06-30)
→ 出汇总报表
```

## 数据 schema (`~/.catfish/bookkeep.jsonl` 每行)

```json
{
  "id": "bk_20260622_201433_a3f1",
  "ts": "2026-06-22T20:14:33+08:00",
  "kind": "支出",
  "amount": 320.0,
  "category": "餐饮",
  "note": "中午外卖 + 晚上麻辣烫"
}
```

默认 8 类 (LLM 可自填新的): `餐饮 / 交通 / 购物 / 工资 / 医疗 / 转账 / 房租 / 其他`.

## 设计选择 (P3.5.75 拍板)

| 决策点 | 拍 | 理由 |
|---|---|---|
| 单 CNY vs 多币种 | 单 CNY (无 currency 字段) | YAGNI, 加字段往 jsonl 后兼容 |
| approval | 0 approval | append jsonl 本地, 数据不出端, 跟 SOUL 一致 |
| 月底自动 cron | MVP 不加 | 先 dogfood, 看实际想看的报表形态再加 cron (复用 P3.5.74 picker 联动) |
| 类别强校验 | 不强校验 | 默认 8 类是 description 鼓励, LLM 可自填 (e.g. '订阅', '健身') |

## Tool description 设计 (P3.5.72 教训)

3 个 tool description 都遵循 P3.5.72 整出来的 "直白 + code 例子, **0 审批/沙箱/安全** keyword" 原则.
不让 LLM 把记账解读成"敏感操作 → 让用户自己跑". 详 `bookkeep.py` `BOOKKEEP_*_DESCRIPTION`.

## 测试

```bash
cd edge/hermes-plugins/catfish-bookkeep
python3 -m pytest tests/ -v
```

22 个单测覆盖: helpers / handler round-trip / schema 合法性 / 边界 (空 jsonl / 坏行 / 负数 / 多币种).

## 跟其它 catfish plugin 对比

| Plugin | Kind | hermes API |
|---|---|---|
| `catfish-memory` | MemoryProvider | `ctx.register_memory_provider` + `ctx.register_tool(memory)` |
| `catfish-xcatfish-user` | standalone monkey-patch | 纯 `plugin.install()` (11 处 hermes monkey-patch) |
| **`catfish-bookkeep`** (本) | standalone tool | `ctx.register_tool` × 3 |

## 加载架构

1. `~/.hermes/plugins/catfish-bookkeep/` 软链到本仓库 (deploy.sh 建)
2. `~/.hermes/config.yaml`:
   ```yaml
   plugins:
     enabled:
     - catfish-bookkeep
   ```
3. hermes daemon 启动时扫 `~/.hermes/plugins/`, 看 `__init__.py` 的 `register(ctx)` 函数, 调它
4. `register(ctx)` 调 `ctx.register_tool` × 3, 注册 3 个 bookkeep tool

## 复用 catfish 现有基础设施

- **本地 STT** (Whisper.cpp + ffmpeg avfoundation, `companion-app/src-tauri/src/commands/speech.rs`, 五一 sprint 已 ship) — 语音输入完全本地
- **picker 联动** (P3.5.28/42/74) — 记账时 LLM 用员工选的 picker model 处理
- **数据零出端** (SOUL.md L4 红线) — bookkeep.jsonl 全本地, 不上云
