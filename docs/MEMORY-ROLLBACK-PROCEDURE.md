# Memory Ownership Cutover Rollback Procedure

> **状态**: emergency 文档. 5/19 凌晨实盘发现 BL-TOOL-BRIDGE-HERMES-INTEGRATION
> 漏洞后立即写, 防员工 / 客户撞到崩溃.

## 触发条件

任何一个出现, 立即按下面 rollback:

1. Companion 切到 hermes_api 后 chat 30s 超时
2. hermes log 报 `Unknown tool 'catfish_*'` (Unknown tool self-correction loop)
3. catfish memory plugin 加载失败 (`hermes memory status` 不显 active)
4. 员工反馈"鲶鱼变笨了" / "工具不可用了"

## 完整 Rollback (3 步, <3 分钟)

### Step 1. 关 Companion 切 hermes 路径

```bash
~/.hermes/hermes-agent/venv/bin/python <<'PYEOF'
from pathlib import Path
import yaml
cfg = Path.home() / ".catfish" / "companion.yaml"
data = yaml.safe_load(cfg.read_text("utf-8")) or {}
data.setdefault("hermes_api", {})["enabled"] = False
cfg.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), "utf-8")
print("✓ hermes_api.enabled=false, Companion 走老 gateway")
PYEOF
```

Companion 启动时读 yaml, **下一次 chat 自动 fallback 老 gateway** (chat.ts useHermes
分支检测 enabled=false → 走 fetchWithAuth + gateway 8999 路径).

### Step 2. (临时) 恢复 gateway memory_registry inject

5/19 凌晨 BL-GATEWAY-MEMORY-CODE-DEPRECATED 把 bootstrap.py 清空了. Companion
fallback 老 gateway 后**没人 inject memory**. chat 能跑但鲶鱼不"懂员工".

临时方案 — 改 `central/llm-gateway/src/catfish_gateway/memory/bootstrap.py`,
**临时回滚**到老的 register 10 个 provider 逻辑. git checkout 上一版即可:

```bash
cd ~/person_task/catfish
git log --oneline --follow central/llm-gateway/src/catfish_gateway/memory/bootstrap.py | head -3
# 看到 5/19 deprecated commit 之前那个 SHA, 比如 abc1234
git show abc1234:central/llm-gateway/src/catfish_gateway/memory/bootstrap.py \
  > central/llm-gateway/src/catfish_gateway/memory/bootstrap.py
```

然后重启 gateway:
```bash
cd central/llm-gateway
pkill -f 'python -m catfish_gateway'
nohup python -m catfish_gateway.app > /tmp/gateway.log 2>&1 &
```

### Step 3. (可选) 关 catfish-memory plugin

如果 plugin 本身有 bug (initialize / prefetch hang), 也要关 plugin:

```bash
~/.hermes/hermes-agent/venv/bin/python <<'PYEOF'
from pathlib import Path
import yaml
cfg = Path.home() / ".hermes" / "config.yaml"
data = yaml.safe_load(cfg.read_text("utf-8")) or {}
data.setdefault("memory", {})["provider"] = None  # 或者 "" 回 builtin only
cfg.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), "utf-8")
print("✓ memory.provider=null, builtin 兜底")
PYEOF
hermes gateway restart
```

## 单独 Rollback (只 Companion, 不动 gateway memory_registry)

如果只是想"先把 Companion 切回 gateway, 容忍 chat 暂时缺 memory inject":
- 只跑 Step 1
- chat 可用, 鲶鱼对员工的了解暂时弱
- 几小时内修好 #28 (tool-bridge 集成 hermes) 再重 cutover

## 5/19 凌晨实盘状态参考

实盘踩坑过程:

1. `~/.catfish/companion.yaml hermes_api.enabled=true` (setup-catfish-edge.sh 写的)
2. **但 Companion 老 .app 还在跑** (没 build 新版含 chat.ts 切换代码), 实际仍走 gateway
3. curl 直测 hermes 8642 → 30s 超时
4. hermes log 报 `Unknown tool 'catfish_run_skill' — sending error to model for self-correction`
5. 真因: hermes 看不到 catfish-tool-bridge 50 个 catfish_* tool, agent loop 死循环
6. **rollback**: 设 enabled=false (Step 1), 老 Companion 路径未受影响

## 修复时机 (#28 BL-TOOL-BRIDGE-HERMES-INTEGRATION)

修复 #28 之前**不要**真 cutover 到 hermes_api.enabled=true. 修法选项:

- A. 把 catfish-tool-bridge 包成 MCP server, hermes 通过 hermes mcp 调
- B. 找 hermes 的 tools plugin 类型 (类似 plugins/memory/ 但 for tools)
- C. fork hermes 改 import catfish-tool-bridge (最重)

调研 + 实现 3-5 小时, 留 5/19 早上做.

## 怎么知道是否安全 cutover

实盘验证 cutover 安全 (一口气全跑通) 必须满足:

```bash
# 1. hermes API server 8642 监听 ✓
lsof -i :8642

# 2. catfish-memory plugin active ✓
hermes memory status

# 3. **关键**: hermes 真能调到 catfish_* tool
KEY=$(grep "^API_SERVER_KEY=" ~/.hermes/.env | cut -d= -f2)
curl -sS -m 30 http://localhost:8642/v1/chat/completions \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"hermes-agent","messages":[{"role":"user","content":"搜邮件: 王主任工资条"}],"stream":false}'

# 预期: 真返回鲶鱼回答 + 提到调 catfish_email_search tool
# 失败: 30s timeout / "Unknown tool" 错误
```

只有第 3 步通过, 才能把 `enabled=true` 推到员工.
