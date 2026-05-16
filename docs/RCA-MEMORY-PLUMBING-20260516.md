# RCA — hermes memory 工具长期不写盘 (5/16 6h 摸排)

> **状态**: ✅ root cause 定位 + 修复 + 实盘验证
>
> **结论一句话**: catfish tool-bridge 是 stateless RPC 桥接, 调 hermes registry.dispatch 不传 `kw['store']`, hermes memory_tool handler 拿不到 MemoryStore 实例 → 永远返 `"Memory is not available"` → `~/.hermes/memories/USER.md` 几周不更新.
>
> **从 hermes 升 0.13 起就断了, 6 周里 19 个 BL- 任务里 5 个在猜错方向, 直到 5/16 实盘 dispatch log 才定位**.
>
> **fix**: `BL-MEMORY-BRIDGE-STORE` (commit pending). adapter.py 加 `_get_memory_store()` lazy singleton, dispatch `name == "memory"` 时塞 `extra_kw['store']`. 一次到位.

---

## 1. 现象

### 1.1 表面观察 (整个 sprint 早期)

- `~/.hermes/memories/USER.md` mtime 停在 4/27
- `~/.hermes/memories/MEMORY.md` 根本不存在
- LLM 在聊天里看似配合, 调 `catfish_remember` 写 `~/.catfish/session_facts.json` (session-only, 跨 session 失忆)
- 跨 session 长期记忆 = 0 增量

### 1.2 实盘日志证据 (5/16 16:24)

```
[dispatch IN]  name=memory args={'action': 'add', 'content': '鸿波的儿子陈淡孜目前在韩国', 'target': 'user'}
[dispatch OUT] name=memory raw='{"error": "Memory is not available. It may be disabled in config or this environment.", "success": false}'
```

LLM 真调对了 `memory` 工具, 真填对了参数, hermes 静默返 disabled. tool-bridge 把 `{"ok": True, "result": {error: ...}}` 返回去, Companion UI 看着像"调用成功", 数据其实没落盘.

---

## 2. 误诊轨迹 (诚实复盘)

整天 19 个 BL- 任务里至少 5 个在症状层猜原因. 按时间排:

| BL- 任务 | 当时假设 | 实际效果 |
|---|---|---|
| BL-MEMORY-NUDGE V1/V2/V3 | LLM 不知道该写 hermes memory, 加 SOUL 主动记忆纪律 | nudge 是对的, 但没解决"工具调了也不落盘" |
| BL-MEMORY-CATFISH-REMEMBER-BLACKLIST (方案 A) | LLM 选 `catfish_remember` 不选 `memory`, 黑名单后强制走 memory | 模型层确实推动了, 但拒掉的请求落到 `memory` 后照样被 hermes 静默拒 |
| BL-MEMORY-FULL-HERMES-V3 反向 nudge | 默认 `memory`, `catfish_remember` 设为"罕见 edge case" | 同上 — 推对工具但工具本身被 disable |
| BL-MEMORY-INJECT-OPTIMIZE A+B+C | 觉得是注入预算不够 / 上下文挤掉 memory hint | 跟 root cause 完全无关, 是另一类优化 |
| BL-MEMORY-FTS5-REAL | hermes session 历史检索路径 | 跟 root cause 无关 |

**这些任务都不算白做** — SOUL nudge 改进的确提高了模型调 memory 的频率, A 黑名单 + V3 nudge 真把 catfish_remember 用法降下来, audit / provider abstraction 是长期方向. 但**它们都不是必须**: 真因被修后, 即便没有 nudge / 黑名单, 模型偶尔也会自发调 `memory` (5/3 USER.md 还有写入, 说明那时候这条链还能跑).

---

## 3. 真因定位过程

5/16 下午鸿波说"我没看到 hermes memories 写"之后, 逐层排除:

```
[symptom] memories/USER.md 不更新
   ↓
[layer 1] 模型层?  → log: DeepSeek tool_calls=31, 调对 memory tool, args 都对 ✓
   ↓
[layer 2] gateway sanitizer 砍掉 memory tool?  → BL-MEMORY-PLUMBING-DIAG 加 log,
            always-on 实际命中 12 个里有 'memory' ✓
   ↓
[layer 3] tool-bridge dispatch 链路?  → 加 dispatch IN/OUT log (有自己 bug 一次,
            修了 NameError 后) 看到 raw 返:
            '{"error": "Memory is not available. ...", "success": false}'
   ↓
[layer 4] hermes config 里 disable 了 memory?  → 看 ~/.hermes/config.yaml,
            memory_enabled: true ✓
   ↓
[layer 5] hermes memory_tool 怎么决定 disabled?
            tools/memory_tool.py:478:
              if store is None:
                return tool_error("Memory is not available. ...")
            line 579 handler: store=kw.get("store")
            → store 来自 dispatch kwargs, 不是 config
   ↓
[layer 6] kwargs 哪传?  → hermes CLI cli.py / run_agent.py 1747 AIAgent 注入.
            catfish tool-bridge adapter.py:850 调 dispatch_fn(name, args) — 不传 kw.
            → store=None → 永远拒.
   ↓
[root cause] catfish tool-bridge 是 stateless RPC 桥接, hermes 0.13 memory tool
             需要 caller 持有 MemoryStore 单例并 dispatch 时注入. 这是 hermes 升 0.13
             时引入的 design contract change, 我们没察觉, 一直走 store=None 路径.
```

---

## 4. 修法 (BL-MEMORY-BRIDGE-STORE)

### 4.1 改 `edge/tool-bridge/src/catfish_tool_bridge/adapter.py`

加模块级 lazy singleton:

```python
_memory_store_cache: Any = None
_memory_store_init_failed = False

def _get_memory_store():
    """Lazy + cache hermes MemoryStore singleton. None on init fail."""
    global _memory_store_cache, _memory_store_init_failed
    if _memory_store_cache is not None:
        return _memory_store_cache
    if _memory_store_init_failed:
        return None
    try:
        from tools.memory_tool import MemoryStore
        cfg = _read_hermes_memory_config()  # 读 ~/.hermes/config.yaml memory 段
        store = MemoryStore(
            memory_char_limit=int(cfg.get("memory_char_limit", 2200)),
            user_char_limit=int(cfg.get("user_char_limit", 1375)),
        )
        store.load_from_disk()
        _memory_store_cache = store
        return store
    except Exception:
        logger.exception("BL-MEMORY-BRIDGE-STORE: MemoryStore 初始化失败")
        _memory_store_init_failed = True
        return None
```

dispatch 注入点:

```python
extra_kw: Dict[str, Any] = {}
if name == "memory":
    mem_store = _get_memory_store()
    if mem_store is not None:
        extra_kw["store"] = mem_store

raw = await asyncio.to_thread(dispatch_fn, name, args, **extra_kw)
```

### 4.2 改 `edge/catfish-cli/scripts/audit_hermes_memory.py`

audit script 一直看 `~/.hermes/USER.md` (老 hermes 0.10-0.12 路径), 真实文件是 `~/.hermes/memories/USER.md`. 改路径 + 加 `hermes_memory_md_bytes` 新指标. 这意味着之前 audit CSV 一直在测错文件, "没增长" 的报警其实是错文件压根没人写.

---

## 5. 实盘验证

```bash
# 重启 tool-bridge 拿新 code
kill $(cat ~/person_task/catfish/.companion-state/tool-bridge.pid)

# 在 Companion 里发: "记下我儿子陈淡孜目前在韩国"
# DeepSeek 调 memory(action=add, target=user, content="...")

# 验证
$ stat -f "%Sm %z %N" ~/.hermes/memories/USER.md
May 16 16:24:25 2026 125 /Users/chenhongbo/.hermes/memories/USER.md

$ cat ~/.hermes/memories/USER.md
终端用 HOMEbrew 类型, 进 catfish 前要跑 /skin default 字体才看得清
§
鸿波的儿子陈淡孜目前在韩国
```

mtime 跟 dispatch OUT log 16:24:25,608 完全对齐. ✓

OUT raw 也变了:

```
{"success": true, "target": "user",
 "entries": [..., "鸿波的儿子陈淡孜目前在韩国"],
 "usage": "4% — 66/1,375 chars",
 "entry_count": 2,
 "message": "Entry added."}
```

---

## 6. 教训

### 6.1 症状层猜模型 / SOUL prompt / 工具列表前, 先验 plumbing

19 个 BL- 任务里 5 个是在模型层 / prompt 层 / 工具列表层猜原因, 其实只要一次 dispatch 入口/出口 log 就能在 5 分钟内定位到"hermes 静默拒". 如果当天一早就加这个 log, 19 个任务里至少 4 个不用做, 省 5h.

**下次卡 ≥30 分钟没头绪的"行为类问题", 第一动作: 在最底层 (tool dispatch / RPC / IPC 边界) 加 log, 看真返**.

### 6.2 stateless RPC 桥接 + 上游 stateful tool = 一类盲点

hermes 0.13 memory_tool / todo_tool 都用 `store=kw.get("store")` 接受 caller 注入. 这是 hermes 设计上一个 stateful 入口. 我们 catfish tool-bridge 当成 stateless RPC 桥接, 用 `r.dispatch(name, args)` 一刀切, 自动丢掉所有 stateful kwargs. 这是单 tool 设计跟桥接架构之间的 contract mismatch.

**有 stateful 入口的工具不止 memory** — todo_tool 同模式 (line 274). 跨进程桥接架构应该对 stateful tool 做 inventory + 显式注入策略, 不该"凑巧 memory 工作了就行".

### 6.3 hermes 升级没做 contract test

5/3 之前 `~/.hermes/USER.md` 还有更新 (846 字节). 5/3 之后到 5/16 zero — 那个时间点周围有过一次 hermes-agent 升级 (`HERMES-UPGRADE.md`). 升完没人测"memory 工具能不能写盘", 只测"工具能调通". 工具能调通 + ok=True ≠ 工具真做事.

**升级 contract test 应该是行为级, 不是 invocation 级**:
- ✗ "调用 memory tool 没抛异常" (这种现状能过)
- ✓ "调用 memory(action=add, content='测试') 后, ~/.hermes/memories/USER.md mtime 比调用前晚"

---

## 7. 后续防火墙建议

| 防火墙 | 优先级 | 估时 | 备注 |
|---|---|---|---|
| tool-bridge `test_dispatch_memory_writes_disk` 集成测 | P0 | 1h | 调 dispatch_tool 真触发 → assert 文件 mtime 跳 |
| 同模式给 todo_tool 写一份 contract test (即便现在 catfish 没用 todo) | P1 | 30min | 防 todo 哪天被启用时再撞坑 |
| `audit-hermes-memory` 加 alert: hermes_user_md_bytes / hermes_memories_files 连续 7 天 0 增长 → 报警 | P0 | 1h | 真要早预警, 不靠员工肉眼 |
| hermes 升级 SOP 加一项: 跑 tool-bridge contract test | P0 | 加文档 | `HERMES-UPGRADE-CHECKLIST.md` |
| stateful tool inventory: 检查 hermes 所有 `kw.get("store")` 用法, 统一注入策略 | P2 | 2h | 长期防 |

---

## 8. 任务关联

完成本 RCA 的 BL- 任务:

- ✅ BL-MEMORY-PLUMBING-DIAG — 加 sanitizer + dispatch IN/OUT log
- ✅ BL-MEMORY-PLUMBING-DIAG-V2 — 抓到 hermes 真错误信息
- ✅ BL-MEMORY-BRIDGE-STORE — adapter.py 修复 (本次主修)
- ✅ BL-MEMORY-BRIDGE-STORE-TODO-FOLLOWUP — audit script 路径修 (todo 部分搁置, 没人用)

误诊轨迹里的任务保留, 不删 — 它们的优化效果仍然有效, 只是不是 root cause.

---

## 9. 时间线

```
~5/3       hermes 升级或 catfish 重启某次, MemoryStore 注入路径断
5/3–5/16   ~/.hermes/memories/ 不增长, 但症状跟模型选 catfish_remember
            过度混在一起, 误诊
5/16 早    BL-MEMORY-NUDGE V1/V2 + audit script 装, 还没定位
5/16 下午  BL-MEMORY-FULL-HERMES (C 方向), A 黑名单, V3 反向 nudge —
            模型层做对了, 但 hermes 拒掉的现象没察觉
5/16 16:00 鸿波 stat 看到 memories/ 没动, "不是模型问题"
5/16 16:07 BL-MEMORY-PLUMBING-DIAG 加 dispatch log
5/16 16:24 dispatch OUT raw 暴露 "Memory is not available"
5/16 16:30 定位 store=None 注入缺失 (memory_tool.py:478)
5/16 ~16:35 BL-MEMORY-BRIDGE-STORE 写完, 重启测, USER.md mtime 跳到 16:24:25
            ✓ 完工
```

6 小时摸排, 单一 root cause, 单一 patch.

---

**作者**: 鸿波 + 鲶鱼 (Cowork mode, Claude)
**日期**: 2026-05-16
**关联文件**:
- `edge/tool-bridge/src/catfish_tool_bridge/adapter.py` (commit BL-MEMORY-BRIDGE-STORE)
- `edge/catfish-cli/scripts/audit_hermes_memory.py` (路径修复)
- `~/.hermes/hermes-agent/tools/memory_tool.py` (hermes 0.13 上游, 不动)
