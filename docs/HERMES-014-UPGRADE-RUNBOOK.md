# Hermes 0.14 升级 Runbook (客户 IT 操作清单)

发布 2026-05-17. 适用 hermes 从 0.13.x 升级到 v0.14.0 (`v2026.5.16`,
"The Foundation Release", 808 commit / 633 PR / 12 P0 + 50 P1 closures).

## 🎯 目标读者

- 客户 IT 管理员: 负责员工工作站推送 hermes 升级
- catfish 中央 ops: 负责 catfish-gateway / catfish-identity 监测

## 📋 前置检查 (升级前 30 分钟)

### 1. 客户机备份

每台员工工作站 (有 hermes 装的)运行:

```bash
# 备份 hermes 用户数据 (memories / config / skills)
tar czf ~/hermes-013-backup-$(date +%Y%m%d).tgz \
    ~/.hermes/memories \
    ~/.hermes/USER.md 2>/dev/null \
    ~/.hermes/config.yaml \
    ~/.hermes/skills

# 备份 catfish 工作站数据
tar czf ~/catfish-edge-backup-$(date +%Y%m%d).tgz \
    ~/.catfish \
    ~/.hermes/.catfish_audit.jsonl 2>/dev/null
```

### 2. 中央服务器准备

catfish-gateway / catfish-identity 不依赖 hermes 安装, 但**确认已部署以下加固**:

- ✅ **BL-RBAC-DAY4-HARDENING** (5/17): tools_sanitizer `_audit_unknown_tools`
  + `X-Catfish-Source` header audit
- ✅ **BL-CACHE-AUDIT** (5/17): 5 维 inject 排序 + `cache_control` marker
- ✅ **BL-RBAC-DAY4** (5/17): allowed_tools per-dept enforce

中央服务器升级 git pull + restart 即可, 跟客户机升级独立。

## 🚀 升级步骤 (每台客户机)

### 1. 确认 hermes 安装路径

```bash
ls ~/.hermes/hermes-agent/model_tools.py
# 应该存在. 不存在表示 hermes 装在别的路径, 检查 HERMES_AGENT_PATH env var.
```

### 2. 升级 hermes

```bash
cd ~/.hermes/hermes-agent
git fetch --tags --all
git checkout v2026.5.16   # The Foundation Release
pip install -e .           # 重装 (lazy-deps 自动处理)
```

**注意**: 0.14 把 `[all]` extras 缩水 (#24515), 改用 `--extra all` 不是
`--all-extras`。 如果之前装机脚本写 `pip install -e .[all] --all-extras` 要改成
`pip install -e ".[all]"` 或简单 `pip install -e .` 让 lazy-deps 自动处理。

### 3. 验 hermes 0.14 自身能跑

```bash
hermes --version          # 应显 v0.14.0 (v2026.5.16)
hermes doctor             # 自检
hermes tools              # 列出可用 tool, 应含新加的 x_search / computer_use / video_generate
```

### 4. 重启 catfish-tool-bridge (跟 hermes 同 venv)

```bash
# 停旧 tool-bridge daemon
pkill -f catfish-tool-bridge
sleep 2

# 启新 (Companion autostart 会自动 respawn, 但手动启一次验)
catfish-tool-bridge --foreground 2>&1 | head -30
```

**关键观察**: 如果看到 `BL-HERMES-014-LAZY: import model_tools 失败` →
按错误信息提示的诊断步骤排查, 99% 是 `pip install -e .` 没跑或失败。

### 5. Companion 验

打开 Companion 应用, 发一条测试消息 "你好":

- ✅ 正常回复 → 工作站层面升级成功
- ❌ "tool-bridge unavailable" 或类似 → tool-bridge 没起来, 看
  `~/.catfish/logs/tool-bridge.log` 找根因
- ❌ Companion 卡住不回 → 可能是 catfish-gateway 跟 0.14 hermes
  tool 列表里某个新 tool 撞 (e.g. `x_search` 没在 dept allowed_tools), 看
  `BL-RBAC-DAY4-HARDENING` audit 行

## 🔍 升级后验证 (24 小时内)

### 客户 IT 跑

```bash
# 1. 看每个工作站 hermes 版本一致性
for host in $employees; do
    ssh $host 'cd ~/.hermes/hermes-agent && git log --oneline -1' 
done | sort | uniq -c
# 期望所有 host 都在 v2026.5.16

# 2. 看 tool-bridge 是否 import 失败
grep -l "BL-HERMES-014-LAZY" ~/.catfish/logs/*.log 2>/dev/null
# 任何匹配 = 该工作站 hermes 升级出问题
```

### catfish 中央 ops 跑

```bash
# 3. X-Catfish-Source unknown 比例 (BL-RBAC-DAY4-HARDENING)
jq -r 'select(.type == "llm_request") | .source // "unknown"' \
    /var/log/catfish-gateway/audit.jsonl | sort | uniq -c
# > 5% unknown = 部署里有 plugin ctx.llm bypass

# 4. unknown tool 频率 (tool_override 嫌疑)
grep "BL-RBAC-DAY4-HARDENING" /var/log/catfish-gateway/*.log | wc -l
# 单 tool 名出现 > 50 次 / 天 = 部署里有 plugin 改 tool 名

# 5. Anthropic prompt cache hit (BL-CACHE-AUDIT)
jq -r 'select(.cache_read_tokens) | 
       {model, cache_read: .cache_read_tokens, prompt: .prompt_tokens}' \
    /var/log/catfish-gateway/audit.jsonl | head -20
# 看 cache_read / prompt 比例, 一周内应 > 70% (说明 inject 排序+marker 起作用)
```

## ⏪ 回滚 (如果发生大规模问题)

每台客户机:

```bash
cd ~/.hermes/hermes-agent
git checkout v2026.5.7      # 回 0.13.0
pip install -e .
pkill -f catfish-tool-bridge  # Companion 重启 respawn
```

中央服务器**不需要回滚** — catfish-gateway / catfish-identity 兼容 0.13 + 0.14.

## ❗ 已知限制 / 风险

1. **catfish-autocompress plugin (`edge/hermes-plugins/`)**: 0.14 引入
   `transform_llm_output` plugin hook (#21235, 0.13 已有), 可能跟现有
   `ContextEngine` 接口竞争。 升级后看 hermes 启动 log 是否 plugin
   load 警告。 (BL-HERMES-014-UPGRADE-STEP2 task #82 待 audit)

2. **`tool_override` plugin (新 plugin 装)**: catfish-gateway 看不到 rename,
   靠 `_audit_unknown_tools` + dept admin 警觉。 见
   `docs/RBAC-PLUGIN-THREAT-MODEL.md`。

3. **`ctx.llm` bypass**: 真正堵需要客户公司 firewall 出口锁回
   catfish-gateway, 不是代码事。 见同文档 §2。

4. **`computer_use` 非 Anthropic** (#21967): RBAC 假设过时, 客户 dept
   admin 应该 review 哪些 dept 该有 `computer_use` 权限 (不再隐含
   "Anthropic 用户 = computer_use 用户")。

## 📞 升级期间联系

升级 24 小时内有 catfish 中央 ops 待命:

- audit log 监控
- RBAC 异常实时响应
- tool-bridge 失败 hotfix

## 📎 相关文档

- `docs/RBAC-PLUGIN-THREAT-MODEL.md` — tool_override + ctx.llm 防御
- `docs/CATFISH-HERMES-BOUNDARY.md` — catfish / hermes 责任边界
- `docs/HERMES-014-AUDIT.md` — 0.14 完整影响审计 (待写 #79)
- Hermes 0.14 release notes: https://github.com/NousResearch/hermes-agent/releases/tag/v2026.5.16
