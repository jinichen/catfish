# Week 2 Step D 部署 runbook — gateway 减债切到 plugin 路径

> **状态**: Step B + C 代码已 push, 未真切. 本文件是部署 + 实盘观察 SOP.
>
> **何时执行**: 5/20 起任意清醒时段, 至少留 30 分钟操作窗口 + 24-72h 观察期.
>
> **疲劳态禁动**: 改 launchd 配置, 错一步守护进程不起来. 必须头脑清醒.

## 前提确认

push 之后, 代码状态:
- gateway `app.py`: 旧 caller 包了 env gate, 默认 `CATFISH_GATEWAY_LEGACY_SUMMARIZE=1` (旧 caller 跑)
- catfish-memory plugin: `on_session_end` 写路径就位, 但 env 没设时自动 skip
- **结论**: push 后行为零变化, Step D 改 env 才真切

## Step D-0 — 显式预设 internal dev token (BL-FIX37 妥协)

### 背景

老 BL-FIX37 设计: gateway 启动 `ensure_internal_dev_token()` 自动生成 32B random, 仅进程内存, 不落盘. 外部抓不到, 重启即变.

**Step D 必须妥协这一点**: plugin 跑在 hermes 进程, 必须跨进程拿同一个 token. 显式预设到 `.env` 文件, gateway 启动时跳过自动生成走"已设直接返"分支, plugin 也读同一个 env.

### 操作

```bash
# 1. 生成强随机 token
TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
echo "TOKEN=$TOKEN"  # 记一下, 后面要写两处

# 2. 写到 .env (跟 HERMES_SERVICE_TOKEN 同套路, 同一个文件)
# 假设你的 .env 在 ~/.hermes/.env (BL-HERMES-LAUNCHD-ENV-LOAD 路径)
echo "" >> ~/.hermes/.env
echo "# Week 2 Step D: gateway internal dev token, gateway + plugin 共享" >> ~/.hermes/.env
echo "CATFISH_INTERNAL_DEV_TOKEN=$TOKEN" >> ~/.hermes/.env

# 3. 同时在 gateway 自己的 .env / launchd plist 里也设上
# 假设 gateway 的 env 文件是 ~/.catfish/.env 或 launchd plist EnvironmentVariables
echo "CATFISH_INTERNAL_DEV_TOKEN=$TOKEN" >> ~/.catfish/.env  # 调整为你实际路径

# 4. 文件权限锁紧 (token 跟 service token 同级敏感度)
chmod 600 ~/.hermes/.env ~/.catfish/.env

# 5. 确认这些 .env 都在 .gitignore 里, 永不进 git
grep -rE "\.env$" ~/.hermes/.gitignore ~/.catfish/.gitignore 2>/dev/null
```

**安全权衡说明**: token 落盘是 BL-FIX37 安全特性的退步. 我们靠两层兜底:
1. `.env` 文件 `chmod 600` 只 owner 可读
2. `.gitignore` 永不进 git
3. token 仍然是 32B 随机, 重启 hermes/gateway 时**不会**自动轮换 (需要手动更新 .env)
4. 如果泄露怀疑: 重生成一遍 token, 改两个 .env, 重启两边进程, 1 分钟换完

## Step D-1 — 给 hermes 加 plugin 启用 env

编辑 `~/.hermes/.env` (或你 wrapper.sh 里 source 的 .env 文件), 末尾加:

```bash
# Week 2 Step D: 启用 catfish-memory plugin 写路径 (替代 gateway 旧 session_summarizer)
CATFISH_PLUGIN_SUMMARIZE=1
CATFISH_PLUGIN_SUMMARIZE_MODEL=catfish-public-qwen-flash
# CATFISH_INTERNAL_DEV_TOKEN 已在 Step D-0 设好
```

**model 怎么选**:
- 内网员工默认用 qwen-flash: `catfish-public-qwen-flash` (但这个名字含 "public" 易混淆)
- 真正稳的: 跟员工最常用的 model 一致. 看你内部 catalog 里的实际 model name.
- 如果不确定, 先用 `catfish-public-qwen-flash` 跑 24h, 看 summary 质量再调

## Step D-2 — 给 gateway 关旧 caller

编辑 gateway 启动 env (launchd plist `EnvironmentVariables` 或 wrapper.sh source 的 .env), 加:

```bash
# Week 2 Step C: 关 gateway 旧 session_summarizer + memory_distill caller
# plugin 接管, 见 catfish-memory plugin on_session_end
CATFISH_GATEWAY_LEGACY_SUMMARIZE=0
```

**关键**: 这一行**晚于** Step D-1 设 — 必须保证 plugin 那边 env 全设好了, 再关 gateway 旧 caller. 否则会出现"gateway 关了 plugin 没起" 的全黑窗口期, 中间这段 chat 不写 journal.

## Step D-3 — 重启 + 烟雾测试

```bash
# 1. 重启 hermes
launchctl unload ~/Library/LaunchAgents/<hermes-plist>.plist
launchctl load ~/Library/LaunchAgents/<hermes-plist>.plist

# 2. 重启 gateway
launchctl unload ~/Library/LaunchAgents/<gateway-plist>.plist
launchctl load ~/Library/LaunchAgents/<gateway-plist>.plist

# 3. 看进程起来
ps aux | grep -E "hermes|catfish-gateway" | grep -v grep

# 4. 看 env 真的传进去了 (在两个进程的 env 都查)
# 这个最重要 — env 没传到进程, plugin 自动 skip, 你看不到任何错
pid_hermes=$(pgrep -f hermes-agent | head -1)
pid_gateway=$(pgrep -f catfish-gateway | head -1)
sudo cat /proc/$pid_hermes/environ 2>/dev/null | tr '\0' '\n' | grep -E "CATFISH_PLUGIN|CATFISH_INTERNAL"
# macOS 上没 /proc/, 用:
ps eww $pid_hermes | tr ' ' '\n' | grep -E "CATFISH_PLUGIN|CATFISH_INTERNAL"
ps eww $pid_gateway | tr ' ' '\n' | grep -E "CATFISH_INTERNAL|LEGACY_SUMMARIZE"

# 5. 烟雾测试: 在 Companion 里随便聊 1-2 句, 让 session 结束 (关闭 chat 或开新 session)
# 等 30 秒 (LLM 调用 + 写文件), 看 journal mtime
stat -f "%Sm" ~/.catfish/employee_journal.md  # macOS stat

# 6. 看 plugin 日志确认是它写的 (不是 gateway)
tail -100 ~/.hermes/logs/*.log | grep -i "catfish-memory"
# 应该看到: "catfish-memory: 起后台 thread summarize session=..."
# 应该看到: "catfish-memory bg session=...: ✓ 写 journal N 字节"

# 7. 看 gateway 日志确认旧 caller 没跑
tail -100 ~/Library/Logs/catfish-gateway.log 2>/dev/null | grep -i "summarize_one_session"
# 应该**没有** "summarize_one_session" 这条 (LEGACY=0 关了)
```

### 验通过标志

| 指标 | 期望 |
|---|---|
| `~/.catfish/employee_journal.md` mtime | chat 完成后 30 秒内 |
| hermes 日志 `catfish-memory bg ✓ 写 journal` | 至少 1 条 |
| gateway 日志 `summarize_one_session` | **零条** (LEGACY=0) |
| plugin 日志无 `WARNING / 异常` | 0 错 |

### 任意一项不达标怎么办

- env 没进进程: 检查 wrapper.sh source 顺序, 或 launchd plist `EnvironmentVariables` 项
- plugin 没起后台 thread: env 4 项缺一就 skip, 重读 D-0 / D-1 三个 env 是不是真设了
- LLM 调用失败: 检查 `CATFISH_INTERNAL_DEV_TOKEN` gateway 和 plugin 两边值是否一致
- gateway 还在跑旧 caller: 检查 gateway env 是不是真有 `CATFISH_GATEWAY_LEGACY_SUMMARIZE=0`

## Step D-4 — 24-72h 实盘观察

每 24h 看一次:

```bash
# journal 在持续被追加 (size 缓慢增长, 每天员工 chat N 次 → N 段)
ls -la ~/.catfish/employee_journal.md

# 不该有 silent fail
grep -E "catfish-memory.*(失败|异常|WARNING)" ~/.hermes/logs/*.log

# distilled 24h 内被 plugin 写一次 (蒸馏跑了)
ls -la ~/.catfish/distilled_facts.md
head -3 ~/.catfish/distilled_facts.md  # 应该有 "Generated by catfish-memory plugin" header

# 检查 plugin 写的 state file
cat ~/.catfish/memory_distill_state.json
# 应该有: {"source": "catfish-memory-plugin", "last_run_iso": "..."}
```

### 观察期发现问题 → 回滚

**软回滚 (1 分钟, 不重启 hermes)** — 旧 caller 重新开启:

```bash
launchctl setenv CATFISH_GATEWAY_LEGACY_SUMMARIZE 1
launchctl unload ~/Library/LaunchAgents/<gateway-plist>.plist
launchctl load ~/Library/LaunchAgents/<gateway-plist>.plist
# plugin 不用关 — 它有 mtime 5min 幂等, 看 gateway 刚写过自动 skip
```

**硬回滚 (代码层面, 整个 Week 2 退回)**:

```bash
cd /Users/chenhongbo/person_task/catfish
git reset --hard gateway-pre-cleanup-week2
git push --force-with-lease origin main
```

## Step D 完成判定

观察期 ≥ 72h, 全部满足:
- ✓ employee_journal.md 持续被 plugin 追加 (每个 chat 后 30 秒内 mtime 更新)
- ✓ distilled_facts.md 24h 内被 plugin 蒸馏覆写一次
- ✓ plugin 日志零 WARNING / 异常
- ✓ gateway 日志零 `summarize_one_session` (LEGACY=0 真生效)
- ✓ 员工反馈无"鲶鱼忘了我说过什么" 投诉增加

满足后 Step D ✓, 进入 Step E (硬删 gateway 旧 module). Step E **不在本 sprint 做**, 留到下 sprint, 至少 10 天双跑期 + 鸿波 explicit approval.

## 附: 一键检查脚本

把以下存为 `~/.hermes/scripts/check_week2_deploy.sh`, `chmod +x`, 每天跑一次:

```bash
#!/bin/bash
echo "=== Week 2 Step D 健康检查 $(date) ==="
echo ""
echo "[1] journal mtime (应在 24h 内):"
stat -f "%Sm  %z 字节" ~/.catfish/employee_journal.md 2>/dev/null || echo "  ❌ 文件不存在"
echo ""
echo "[2] distilled mtime + 来源:"
stat -f "%Sm" ~/.catfish/distilled_facts.md 2>/dev/null
head -2 ~/.catfish/distilled_facts.md 2>/dev/null | grep -q "catfish-memory plugin" && \
  echo "  ✓ plugin 来源" || echo "  ❌ 不是 plugin 写的"
echo ""
echo "[3] plugin 24h 内日志统计:"
since=$(date -v-1d +%Y-%m-%d)
grep "catfish-memory" ~/.hermes/logs/*.log 2>/dev/null | \
  awk -v since="$since" '$1 >= since' | \
  grep -cE "(✓ 写 journal|✓ 蒸馏)" | xargs echo "  ✓ 写入次数:"
grep "catfish-memory" ~/.hermes/logs/*.log 2>/dev/null | \
  grep -cE "(失败|异常|WARNING)" | xargs echo "  ❌ 错误次数:"
echo ""
echo "[4] gateway 旧 caller 是否真关:"
grep -c "summarize_one_session" ~/Library/Logs/catfish-gateway.log 2>/dev/null | \
  awk '{print "  " ($1==0 ? "✓ 零次 (LEGACY=0 生效)" : "❌ " $1 " 次 (LEGACY 没关)")}'
echo ""
echo "[5] env 现状:"
launchctl getenv CATFISH_GATEWAY_LEGACY_SUMMARIZE | xargs -I{} echo "  CATFISH_GATEWAY_LEGACY_SUMMARIZE={}"
echo "=== 健康检查完 ==="
```

---

**作者**: 鸿波 + Claude (Cowork mode), 2026-05-19 晚
**Ref**: GATEWAY-CLEANUP-WEEK2-MIGRATION-PLAN.md, BL-FIX37, BL-HERMES-LAUNCHD-ENV-LOAD
