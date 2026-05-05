# Hermes 升级阶段 C — 真升级 0.10 → 0.12 Runbook

> **目的**: 5/15+ demo 后真升级 hermes 时, **打开本文档照着每条命令敲**. 不用回忆 5/5 准备了啥, 出问题翻 § 8 故障树.
>
> **预计时间**: 顺利 ~3 小时 / 出问题 ~1 天 (回归测试 80+ 条)
>
> **前置依赖**:
> - 阶段 A 完成 (5/5 已 ✅) — 评估报告 + checklist + 决策书
> - 阶段 B 完成 (5/5 已 ✅) — brand patch 28 条 / brand check / self-heal
> - demo 已结束 (≥ 5/15)
> - 当天有 1 整天可投入 (CHECKLIST 第 5-12 节人工目检需要时间)
>
> **关键文档**:
> - 阶段 A 报告: `docs/HERMES-UPGRADE-PHASE-A-RESULT.md`
> - 升级 checklist: `docs/HERMES-UPGRADE-CHECKLIST.md`
> - 详细背景: `docs/HERMES-UPGRADE.md`

---

## 0. 启动前 (5 分钟)

### 0.1 确认前置条件

- [ ] 今天日期 ≥ 2026-05-15 (demo 已过)
- [ ] 当前 hermes 版本是 0.10.0
  ```bash
  hermes --version
  # 期望: Hermes Agent v0.10.0 (2026.4.16) ...
  ```
- [ ] catfish gateway 在跑
  ```bash
  curl -s http://127.0.0.1:8999/healthz
  # 期望: {"status":"ok"}
  ```
- [ ] BL-MM2 + 阶段 A/B 都 ship 了
  ```bash
  cd ~/person_task/catfish && git log --oneline -5
  # 应该能看到: BL-MM2, P0+BL-F19+, Hermes Phase A+B
  ```

任何一条不满足, **停止**, 先把缺的做完.

### 0.2 启动一个全程不退出的终端

升级期间**保持**这个终端不关, 开着 gateway log 在另一个终端窗口实时看. 出问题第一时间能 grep traceback.

```bash
# 终端 1: 你的工作台 (跑命令)
cd ~/person_task/catfish

# 终端 2: gateway log 实时 (新开个窗口)
tail -f ~/Library/Logs/catfish/gateway.log
# 或者直接看 gateway 进程的 stdout

# 终端 3: hermes 运行时 (升级后启动 catfish 用)
```

---

## 1. 备份 (10 分钟)

### 1.1 git tag 升级前状态

```bash
cd ~/person_task/catfish
git status --short    # 应该是 clean. 有未提交的先 commit
git tag -a phase-c-pre-upgrade -m "升级前 (5/15+ 阶段 C 第 1 天)"
git push origin phase-c-pre-upgrade   # 推到 remote 防本地丢
```

### 1.2 整个 ~/.hermes/ 拷贝出来

```bash
HERMES_BACKUP_DIR="$HOME/.hermes-backup-$(date +%Y%m%d_%H%M%S)"
cp -a ~/.hermes "$HERMES_BACKUP_DIR"
echo "备份点: $HERMES_BACKUP_DIR"
du -sh "$HERMES_BACKUP_DIR"
# 期望: ~500MB-1GB (含 venv + skills + memories + state.db)
```

记下这个路径, 升级出大问题就 `mv ~/.hermes ~/.hermes-broken && mv "$HERMES_BACKUP_DIR" ~/.hermes` 回滚.

### 1.3 dump 当前关键 metadata

```bash
mkdir -p /tmp/upgrade-snapshots
hermes --version > /tmp/upgrade-snapshots/version-before.txt
ls ~/.hermes/skills/ > /tmp/upgrade-snapshots/skills-before.txt
sqlite3 ~/.hermes/state.db "SELECT count(*) from sessions" > /tmp/upgrade-snapshots/sessions-count.txt 2>/dev/null || true
sqlite3 ~/.hermes/state.db "SELECT count(*) from messages" > /tmp/upgrade-snapshots/messages-count.txt 2>/dev/null || true
echo "snapshots:"; ls -la /tmp/upgrade-snapshots/
```

升级后再 dump 一次, 确保 sessions / skills / messages 数没莫名丢.

---

## 2. 真升级 hermes 0.10 → 0.12 (5 分钟)

### 2.1 退出所有 hermes / catfish 进程

```bash
# 关掉所有 catfish CLI 终端
# 关掉 Companion (macOS dock 上右键退出)

# 确认没残留进程
ps aux | grep -E "hermes|catfish" | grep -v grep
# 期望只剩 gateway / tool-bridge 后台进程

# 关掉 gateway / tool-bridge (升级后重启)
lsof -i:8999 | awk '/Python/ {print $2}' | xargs -r kill
lsof -i:8997 | awk '/Python/ {print $2}' | xargs -r kill   # tool-bridge
```

### 2.2 pip 升级到 0.12.0

```bash
~/.hermes/hermes-agent/venv/bin/pip install --upgrade hermes-agent==0.12.0
# 看输出: Successfully installed hermes-agent-0.12.0 ...
# 如果有依赖冲突: 先 pip list 看现有版本, 手动 resolve
```

### 2.3 验证 hermes runtime 真升了

```bash
hermes --version
# 期望: Hermes Agent v0.12.0 (2026.4.30) · upstream xxxxx
# 注: 这时还没 brand patch, 仍显 "Hermes Agent" 字样, 不慌, 第 3 步会修

ls ~/.hermes/hermes-agent/
# 期望: 应该多了 agent/curator.py / agent/curator_state.py 等 0.12 新文件
ls ~/.hermes/hermes-agent/agent/curator.py
# 期望: 文件存在 ✓
```

如果 `ls .../curator.py` 不存在, 升级**没生效**, 看 `pip install` 输出有没 error. 重跑 § 2.2.

---

## 3. catfish self-heal brand patch (2 分钟)

### 3.1 启动 catfish, 看 self-heal 触发

```bash
catfish --no-banner --version
# 期望输出三行 (按序):
#   brand patch 漂移 (hermes 升级了?), 自动修中…
#   brand patch 已自愈.
#   underlying: 鲶鱼 v0.12.0 (2026.4.30) ...
```

如果**没看到**前两行 self-heal 输出, 可能:
- (a) brand patch 已经在 0.12 上 100% 命中 (不太可能 — 0.12 文件 hash 全变了, 之前 patch 备份的 .before-catfish 跟 0.12 文件位置对不上)
- (b) ensure_brand_patch 函数没找到 catfish 仓库根 → 看 `which catfish` 走的是哪个版本入口

详见 § 8.1 故障排查.

### 3.2 验证 brand check 全过

```bash
catfish brand-check
# 期望最后:
#   PASS: 21
#   FAIL: 0
#   WARN: 0
#   brand check 全过. 升级 hermes 安全.
```

如果有 **FAIL**: 0.12 里有新加的 hermes 字面量, 我们的 28 条规则没覆盖. 看具体 FAIL 哪一条:

```
FAIL  cli.py 不含 ... — 'XXX' 仍在 cli.py
```

去 `apply_brand_patch.py` 加一条新规则, 然后:

```bash
catfish brand-fix    # re-apply
catfish brand-check  # re-verify
```

如果**所有 21 条都 FAIL**, 是 self-heal 没真跑. 看 § 8.1.

---

## 4. 启动 gateway + tool-bridge (5 分钟)

### 4.1 重启 gateway

```bash
cd ~/person_task/catfish/central/llm-gateway
python -m catfish_gateway.app &
# 等启动 banner: 🐟 鲶鱼网关 · 网络层 ...

# 健康检查
sleep 5
curl -s http://127.0.0.1:8999/healthz | python3 -m json.tool
# 期望: {"status": "ok"}
```

### 4.2 重启 tool-bridge

```bash
cd ~/person_task/catfish/edge/tool-bridge
python -m catfish_tool_bridge &

# 等几秒, 看 socket 起来
ls /tmp/catfish-tool-bridge.sock 2>/dev/null && echo "✓ tool-bridge ready"
```

### 4.3 跑一条 chat 验证 0.12 + gateway + tools 链路

```bash
TOKEN=$(grep CATFISH_DEV_TOKEN ~/person_task/catfish/central/llm-gateway/.env | cut -d= -f2)

curl -X POST http://127.0.0.1:8999/v1/chat/completions \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Catfish-Internal: true" \
  -H "Content-Type: application/json" \
  -d '{"model":"catfish-public-deepseek-flash","messages":[{"role":"user","content":"hi 你是谁"}]}' \
  -m 30 | python3 -m json.tool
```

期望返回含 `"choices": [{"message": {"content": "我是鲶鱼..."}}]`.

如果 500 / 502, 看 gateway log:
- `UnboundLocalError` → P0 bug 又回来了 (5/5 修过), 看 `app.py:1012` is_internal_call 赋值是不是又被注释了
- `502 free tier exhausted` → DashScope 免费层用完, 换 catfish-public-deepseek-flash 或 gemini-flash
- `connection refused` → gateway 没起来, 重 § 4.1

---

## 5. 跑 CHECKLIST 第 1-3 节 (机器可验, 30 分钟)

打开 `docs/HERMES-UPGRADE-CHECKLIST.md`, 按章节顺序跑.

### 5.1 第 1 节: brand 字符串扫描

```bash
catfish 2>&1 | head -100 > /tmp/catfish_boot.txt &
CATFISH_PID=$!
sleep 5
kill $CATFISH_PID 2>/dev/null

# 必须返 0 命中
grep -E "Hermes|Nous Research|⚕|Goodbye!" /tmp/catfish_boot.txt
echo "exit: $?"   # 期望 1 (grep 没命中, 即 brand 干净)
```

按 CHECKLIST § 1 的 11 条**人工目检**勾, banner / Welcome / 退出语 / NOUS HERMES 大字 / TIPS 列表 等.

### 5.2 第 2 节: tool registry 接口

```bash
# 能拿全 tool 列表
curl -s http://127.0.0.1:8999/v1/catalog | python3 -c "import sys,json; d=json.load(sys.stdin); print('models:', len(d['data']))"
# 期望: models: 7 (跟升级前一致)

# adapter 能 dispatch tool
# (这步要在 catfish 里手动 chat 调 tool — § 7 走)
```

### 5.3 第 3 节: dispatch 响应 brand 脱敏

调几次 `memory_*` tool, 看返回 message 不含 `~/.hermes/` 字眼.

```bash
# 在 catfish 终端里 chat:
> 帮我看下 memory 里有什么
# 鲶鱼应该调 memory_recall, 返回里如果出现 ~/.hermes/memories/... 是 BUG
# 应该被 scrub 成"鲶鱼本机存储"或类似
```

如果泄漏: `adapter.py: scrub_brand_in_result` 的 regex 要扩展, 见 `docs/HERMES-UPGRADE.md § 5 阶段 A 加固`.

---

## 6. Curator 4 步集成 (1-2 小时)

> 详细方案见 `docs/HERMES-UPGRADE.md § 8`. 5/5 阶段 A.2 verify 后简化为 4 步.

### 6.1 步骤 1: 写保守 config

```bash
# 看 ~/.hermes/config.yaml 是否已有 curator 段
grep -A 5 "^curator:" ~/.hermes/config.yaml 2>/dev/null

# 如果没, 加上保守参数 (央企季度性脚本宽容):
cat >> ~/.hermes/config.yaml <<'EOF'

# Curator (0.12 加, hermes 自己的 skill housekeeping)
# 保守参数: 央企季度性脚本多, 默认 30 天 stale 太激进
curator:
  enabled: true
  interval_hours: 168       # 1 周一次
  min_idle_hours: 4         # idle 4h 才跑 (默认 2 太激进)
  stale_after_days: 60      # 60 天没用算 stale
  archive_after_days: 180   # 180 天 archive (可恢复)
EOF
```

### 6.2 步骤 2 (取消): catfish 装的 skill 不需要 pin

5/5 阶段 A.2 verify 发现 catfish skill 装在 `catfish/skills/` (物理隔离), Curator 扫的是 `~/.hermes/skills/`, 根本不重叠. **跳过这一步**.

### 6.3 步骤 3: Onboarding consent toggle

新员工 onboarding 时给个 toggle. **改 OnboardingWizard.tsx**:

```tsx
// edge/companion-app/src/components/OnboardingWizard.tsx
// 找 step 列表, 加一个新 step:
{
  title: "脚本整理",
  body: (
    <label>
      <input
        type="checkbox"
        checked={curatorEnabled}
        onChange={(e) => setCuratorEnabled(e.target.checked)}
      />
      让小鲶定期帮我整理工作脚本 (推荐)
      <small>180 天没用的脚本会归档 (可恢复 · 不真删) ·
      长得像的脚本会合并 · 高质量的会自动标记</small>
    </label>
  ),
}
```

完成后调 `set_agent_prefs` 把 `curatorEnabled=false` 时写入 `~/.hermes/config.yaml` `curator.enabled: false`.

### 6.4 步骤 4: Dashboard "整理记录"卡 (可选, 后做)

读 `~/.hermes/skills/.curator_state` 的 `last_run_at + last_run_summary`, 显示在 Dashboard. 跟 RelationCard 同思路, 透明可控.

**这一步可以延到 5/22+ 第 2 周**做, 不阻塞升级完成.

### 6.5 验证 Curator 不动 catfish skill

```bash
# 列 ~/.hermes/skills/ 跟 catfish/skills/, 升级前后对比
ls ~/.hermes/skills/ > /tmp/hermes-skills-after.txt
diff /tmp/upgrade-snapshots/skills-before.txt /tmp/hermes-skills-after.txt
# 期望: 没差异 (Curator 第一次跑要 4h idle, 这时不可能跑过)

# 跟踪 24h 后
# (24 小时后再 check 一次)
```

---

## 7. BL-MM3 hermes memory_save 包版本化 (~0.5 天)

### 7.1 设计

跟 BL-MM2 catfish_remember 同思路, 但是包 hermes 的 `memory_save` tool. read-modify-write 双调用模拟版本数组:

```python
# edge/tool-bridge/.../adapter.py 加 wrapper
async def memory_save_versioned(args):
    """先 read 旧值, 再 save 新值含 inline 备注."""
    name = args["name"]
    new_content = args["content"]
    
    # 1. read 旧值 (调 hermes memory_recall)
    old = await dispatch("memory_recall", {"query": name})
    old_text = old.get("content", "")
    
    # 2. write 新值: 含 inline 旧值备注
    if old_text:
        wrapped_content = (
            f"{new_content}\n\n"
            f"---\n"
            f"_(上次值, 已废: {old_text[:200]} ...)_"
        )
    else:
        wrapped_content = new_content
    
    # 3. 调 hermes memory_save
    result = await dispatch("memory_save", {
        "name": name,
        "content": wrapped_content,
    })
    return result
```

### 7.2 测试

加 `tests/test_memory_save_versioned.py` 覆盖 read-modify-write + 旧值缺失 + read 失败兜底.

### 7.3 ship

跟阶段 C 一起 commit.

---

## 8. CHECKLIST 第 4-12 节 (人工目检, 半天-1 天)

第 4-12 节涵盖 SOUL inject / BL-E11/E15/E16 / 主动闲聊 / Quota / RBAC / Skills Hub / SSO / brand assets / 性能, 大部分需要**真在 Companion 浮窗里点**.

按 `docs/HERMES-UPGRADE-CHECKLIST.md` 顺序逐条勾. 没过的写到底部 issue list.

> ⏱ 这一段最耗时间, 出问题最多. 跑完中间出错可以收工再续, 但 § 1-7 必须当天连续做完.

---

## 9. 故障树

### 9.1 self-heal 没触发 / brand check 全 FAIL

```
Symptom: catfish 启动后看不到 "brand patch 漂移" 提示
         catfish brand-check 显示 21 个 FAIL
```

排查步骤:
1. `which catfish` — 确认走的是哪个 catfish 入口. 如果是 `~/.local/bin/catfish` 旧二进制, **重 install**:
   ```bash
   bash ~/person_task/catfish/edge/branding/install.sh
   ```
2. `catfish brand-check 2>&1 | head -5` — 看 "目标: ..." 是哪个目录. 如果不是 `~/.hermes/hermes-agent`, 设 env:
   ```bash
   HERMES_DIR=~/.hermes/hermes-agent catfish brand-check
   ```
3. 手动跑一次 patch:
   ```bash
   python3 ~/person_task/catfish/edge/hermes-fork/apply_brand_patch.py --apply
   ```
   看 PATCH/SKIP/MISS 各多少. 如果有 SKIP/MISS, 是 0.12 改了文件路径或字面量, 加规则 → 重跑 brand-check.

### 9.2 chat_completions 又 500 UnboundLocalError

```
Symptom: chat 全 500
         gateway log: UnboundLocalError: cannot access local variable 'is_internal_call'
```

参考 5/5 17:13 P0 修复. 看 `app.py:1012` 附近:

```python
# 应该有这一行, 不是注释:
is_internal_call = (
    request.headers.get("x-catfish-internal", "").lower() in ("true", "1", "yes")
)
```

如果被注释了, 取消注释. 跑 `pytest tests/test_chat_completions_static.py` 验证.

### 9.3 主动闲聊全 fallback 模板

```
Symptom: dashboard 看到模板话术 "进展如何, 卡哪了?" (硬编码), source=fallback
```

跑诊断:

```bash
TOKEN=$(grep CATFISH_DEV_TOKEN .../llm-gateway/.env | cut -d= -f2)
curl -s "http://127.0.0.1:8999/api/proactive/starter" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
# 看 source 字段
```

看 gateway log 找最近 `generate_starter` 行:
- 全 502 → 上游模型挂了 (DashScope 免费层 / VPN 断 / API key 错)
- 502 → 切下一个 → 又 502 → ... → 全 6 候选都失败 → fallback 模板. 至少 1 个候选要健康.
- "切到第 3 候选成功" 但仍 fallback → BL-F19+ reasoning_content fallback 又坏了, 看 `proactive.py:214`

### 9.4 Curator 第一次跑误删了 agent skill

```
Symptom: ~/.hermes/skills/<skillname>/ 不见了
         ~/.hermes/skills/.archive/<skillname>/ 出现
```

不是 BUG, 是 Curator **archive (可恢复)**. 跑:

```bash
hermes  # 进 hermes
> /skill restore <skillname>
# 或者从 .archive/<skillname>/ 直接 mv 回去
mv ~/.hermes/skills/.archive/<skillname> ~/.hermes/skills/
```

如果是 catfish 装的 skill 被误档 (理论不可能, A.2 verify 物理隔离), **立刻 stop Curator**:

```bash
# 永久关 Curator
echo '{"paused": true}' > ~/.hermes/skills/.curator_state
# 或改 ~/.hermes/config.yaml 设 curator.enabled: false
```

### 9.5 内存爆 / 0.12 lazy import 副作用

```
Symptom: catfish 启动慢明显 (>10s)
         内存占用从 ~200MB 升到 >1GB
```

0.12 lazy import 优化是 57% 冷启动改进, 不应该慢. 如果慢:
1. `top -p $(pgrep -f hermes)` 看 CPU/RAM
2. `~/.hermes/hermes-agent/venv/bin/python -X importtime -c "import hermes_cli" 2>&1 | head -50` 看哪个 import 慢
3. 如果某个第三方库 (mcp / playwright / ...), 升级它

### 9.6 完全 stuck 决定回滚

```bash
# 1. 杀所有进程
ps aux | grep -E "hermes|catfish|gateway|tool-bridge" | grep -v grep | awk '{print $2}' | xargs -r kill -9

# 2. 还原 ~/.hermes
mv ~/.hermes ~/.hermes-broken-$(date +%Y%m%d)
mv "$HERMES_BACKUP_DIR" ~/.hermes
# (HERMES_BACKUP_DIR 是 § 1.2 记的备份路径)

# 3. 还原 git
cd ~/person_task/catfish
git reset --hard phase-c-pre-upgrade

# 4. 重启 catfish, 应该回到 0.10 状态
hermes --version
# 期望: Hermes Agent v0.10.0
```

发 Slack 求助, 写 issue 详情, **不要再硬冲**. 5/15+ 不是 demo 紧迫期, 慢慢修.

---

## 10. 完成验收 (升级成功标准)

全部满足才算 ship:

- [ ] `hermes --version` 显 0.12.0
- [ ] `catfish` 启动 banner 显鲶鱼 (不显 Hermes Agent)
- [ ] `catfish brand-check` 21 PASS / 0 FAIL
- [ ] `curl /api/proactive/starter` 返 `source: "llm"` (不是 fallback)
- [ ] gateway 测试套件 552+ passed (跑 `pytest`)
- [ ] tool-bridge 测试套件 214+ passed
- [ ] CHECKLIST 第 1-3 节机器验证全过
- [ ] CHECKLIST 第 4-12 节人工目检 ≥ 95% 过 (允许 ≤ 5% warn, 0 fail)
- [ ] 1 整天员工真用催 dogfood, 0 报 brand 泄漏 / chat 500 / 主动闲聊不来
- [ ] BL-MM3 ship + 测试过
- [ ] Curator config 写 + 验证 catfish skill 没动

任一不满足: **不上线给同事**, 留单人 dogfood 状态修.

---

## 11. ship 后写

升级完成后:

- [ ] CHANGELOG.md 加 "5/15+ Hermes 0.10 → 0.12 升级 + Curator 集成 + BL-MM3"
- [ ] BACKLOG.md 把 BL-MM3 / 阶段 C 标 ✅
- [ ] HERMES-UPGRADE-PLAYBOOK.md (新文件) — 把这次升级踩的坑 / 学到的 / 没预料的, 写下来给下次升级 (5/30+ 0.13?) 用
- [ ] 给 NousResearch 提 issue 请求 i18n / branding hook (引用 catfish 用例 — 中国 SOE 场景需要本地化)

---

## 12. 时间轴预估 (顺利场景)

| 阶段 | 时长 | 累计 |
|---|---|---|
| § 0 启动前 | 5 min | 5 min |
| § 1 备份 | 10 min | 15 min |
| § 2 真升级 | 5 min | 20 min |
| § 3 self-heal | 2 min | 22 min |
| § 4 重启 gateway | 5 min | 27 min |
| § 5 CHECKLIST 第 1-3 节 | 30 min | 57 min |
| § 6 Curator 4 步 | 1-2 h | ~3 h |
| § 7 BL-MM3 | 4 h | ~7 h |
| § 8 CHECKLIST 第 4-12 节 | 4-8 h | ~12-15 h |
| § 9 故障 (如果有) | + ? | + ? |

**单人 1.5-2 天可完成**. 第二天主要是 § 8 慢慢人工跑.

---

最后更新: 2026-05-05 (写于阶段 B ship 后)
执行: 等 5/15+ demo 后
