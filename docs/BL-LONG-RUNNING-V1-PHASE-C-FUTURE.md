# BL-LONG-RUNNING-V1-PHASE-C — 后续: 真 checkpoint (未来计划)

> **状态**: 🔵 未来计划, 当前**不做**
> **触发**: 等任务种类多样化 (multi-step LLM agent / 多 LLM 调用串联) 后再评估

---

## 6/1 已 ship 的 A+B (实用兜底)

| 部分 | 改动 | 工作量 |
|---|---|---|
| **A. 启动检测 + 标 interrupted** | `mark_interrupted_on_startup()` 扫 jsonl 找 pending 没对应 completed → 标 interrupted | ~20 行 |
| **B. retry helper** | `catfish_task_retry(task_id)` 拿原 payload 启新 task | ~30 行 + schema |
| 配套 | jsonl schema 加 `payload`, `Task` dataclass 加 `payload` 字段, submit() 立即写 pending row | ~30 行 |

**ship 效果**:
- task_manager 重启时立刻识别 stuck task, Companion TasksCard 显"中断"不再永远显"运行中"
- LLM / user 看到中断后能一键 retry, 拿原 input 重跑 (新 task_id)
- jsonl 现在跨重启可读完整任务历史 (含 payload)

**不假装能续跑中间状态** — 那是 Phase C 真 checkpoint 的事, 见下.

---

## Phase C 真 checkpoint (未来)

### 触发条件

只在以下任一发生时, 才动手做 Phase C:

1. **任务种类多于 1 个** — 现在只有 `execute_code` (subprocess Python sandbox), 进程死了 state 丢, 没法续. 出现 `multi_step_agent` / `multi_llm_pipeline` / `long_polling_task` 这种**有中间 state 可保留**的 kind 后, checkpoint 才有意义.

2. **真 user 抱怨** — 跑 1 小时任务挂了 retry 浪费 token / 时间. 现状 retry 也快, 没人抱怨就别造.

3. **任务设计支持 snapshot** — kind 自己定义 `snapshot()` / `restore()` protocol, task_manager 才能用. **task_manager 不应该 magically 知道某个 kind 的中间 state 怎么序列化**.

### 设计草案 (等真做时参考)

```python
class Task(Protocol):
    """新 kind 想支持 checkpoint 时, runner factory 返这个."""
    async def snapshot(self) -> dict: ...   # 序列化中间 state
    async def restore(self, state: dict): ... # 从 state 恢复
    async def run(self) -> Any: ...            # 跑到下一个 checkpoint

# task_manager:
async def _run_with_checkpoints(task: Task, kind: str):
    while not done:
        result = await task.run()              # 跑一段
        state = await task.snapshot()           # 取 state
        write_checkpoint(task_id, state)        # 写 ~/.catfish/checkpoints/<task_id>.json
        if result.is_complete:
            break
    cleanup_checkpoint(task_id)

# 启动时 (in mark_interrupted_on_startup 之后):
for task_id in stuck_tasks_with_checkpoint:
    state = read_checkpoint(task_id)
    new_task = create_task(kind)
    new_task.restore(state)
    submit_resumed(new_task)
```

### 风险

- **状态文件膨胀**: 长任务 snapshot 可能 MB+ 级. 需要清理策略.
- **跨版本兼容**: snapshot 格式跟 task kind 实现绑定. kind 代码升级时 schema 也要 migrate.
- **隐私**: snapshot 含中间数据 (LLM 半成品输出 / 工具调用结果). 跟 task payload 同密级, 应该写本机 `~/.catfish/checkpoints/`, 不上中央.
- **并发**: 多 task 同时 snapshot 时 IO 竞争. 加 lock 或单线程串行 write.

### 工程量估

| 任务 | 工时 |
|---|---|
| 设计 Task Protocol + 选 snapshot 格式 (json / pickle) | 2h |
| task_manager `_run_with_checkpoints` 实现 + 单测 | 4h |
| 第一个 checkpoint-aware kind (例 multi_step_agent) 实现 + 单测 | 6h |
| 启动恢复路径 + 单测 | 3h |
| 文档 + 部署脚本 | 2h |
| **总** | **~2 天** |

### 替代方案 (做了 Phase C 之前)

- **重试**: retry_task (B 已 ship), 简单暴力. 短任务足够.
- **拆小**: kind 设计成"幂等小步骤"组合, 失败重跑某一步, 不影响别的. 这种**架构层面**的设计比 checkpoint 更好.
- **外部存储**: 任务自己写中间结果到 db / s3, retry 时直接读, 不依赖 task_manager checkpoint.

**推荐**: 真做 multi-step agent 时, **先选拆小架构 + 外部存储**, 而不是 task_manager 通用 checkpoint. 通用 checkpoint 是后端复杂度高 / 实用性低的方案.

---

## 何时回 review 这个 ticket

- 出现新 task kind (`multi_step_agent` 等) — 必看
- 真生产某 task 反复跑挂浪费 token — 看 retry 够不够, 不够再考虑 checkpoint
- 用户主动问 "能不能不要每次都从头开始" — 直接问场景, 别 over-engineer

---

*作者: 鸿波 + Claude (Cowork)*
*关联 ticket: BL-LONG-RUNNING-V1, BL-LONG-RUNNING-V1-PHASE-E*
*创建时间: 2026-06-01*
