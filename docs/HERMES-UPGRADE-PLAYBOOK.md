# Hermes 升级 Playbook — 治本流程 + 历史踩坑全集

> **作者**: 鸿波 + 6/21 sprint 痛苦总结
> **场景**: 每次 hermes-agent 上游 (NousResearch/hermes-agent) 发新 release, catfish 跟版
> **核心原则**: 仔细分析代码, 不要瞎猜. 每步 ground truth verify, 决不扛着旧假设跳到结论.

---

## 一、升级前 audit checklist (按这条做, 别跳)

### Step 0: 拉 hermes 新版本 source, 不要 grep truncated 文件

```bash
HERMES_VERSION="v2026.X.Y"  # 改成真新版本
HERMES_AUDIT_DIR=$(mktemp -d)
cd "$HERMES_AUDIT_DIR"

# 拉关键文件 (用 curl 不用 web_fetch — web_fetch 长文件 truncate, 拿不全)
for f in \
  gateway/platforms/api_server.py \
  gateway/run.py \
  gateway/authz_mixin.py \
  gateway/slash_commands.py \
  gateway/session.py \
  tools/approval.py \
  tools/session_search_tool.py \
  tools/tool_search.py \
  run_agent.py \
  agent/agent_init.py \
  agent/auxiliary_client.py \
  agent/title_generator.py \
  agent/conversation_loop.py \
  agent/memory_manager.py \
  model_tools.py \
  hermes_cli/tools_config.py \
  plugins/memory/__init__.py
do
  mkdir -p "$(dirname "$f")"
  curl -sL --max-time 10 \
    "https://raw.githubusercontent.com/NousResearch/hermes-agent/${HERMES_VERSION}/${f}" \
    -o "$f"
  printf "%5d lines  %s\n" "$(wc -l < "$f" 2>/dev/null)" "$f"
done
```

### Step 1: 跑 audit script 对比 catfish 19 patch target

```bash
HERMES_ROOT="$HERMES_AUDIT_DIR" bash ~/person_task/catfish/edge/catfish-cli/scripts/audit_hermes_compat.sh
# 期望: pass=53 fail=0
# 任何 fail → 看下面 "已知坑分类" 排查
```

### Step 2: P28 outbound string audit (hermes → 微信/IM 中文化保护)

> ⚠ **P3.5.79+ 7/22 补** — 血案: v0.18 → v0.19 Quicksilver (7/20)
> `gateway/run.py:370` 加 `to execute this one operation` 后缀, catfish P28
> `_P28_REPLACEMENTS` str.replace 精确匹配失配, 微信员工看到 approval reply
> 段全英文. audit 时**必须** diff outbound f-string.

**为什么**: catfish P28 patch 靠 `str.replace` 精确匹配 hermes 4 段英文
outbound f-string (approval prompt / 中断提示 / 排队提示 / /approve 命令说明),
每次 hermes 升级都可能改这些字串. 一改, P28 silent miss, 英文泄漏到微信员工端.

**Audit 命令** (diff outbound f-string · Step 0 后跑):

```bash
# 拉 gateway/run.py 新老版对比 outbound f-string (P28 挂点)
cd "$HERMES_AUDIT_DIR"
OLD_HV="v<catfish 上次跟的 hermes 版本>"   # e.g. v2026.7.10
NEW_HV="$HERMES_VERSION"                    # e.g. v2026.7.20

for v in "$OLD_HV" "$NEW_HV"; do
  curl -sL --max-time 10 \
    "https://raw.githubusercontent.com/NousResearch/hermes-agent/${v}/gateway/run.py" \
    -o "run.py.${v}"
done

# P28 关注的 4 段 outbound (更多见 plugin.py:_P28_REPLACEMENTS)
P28_PATTERNS='Dangerous command|Interrupting current task|approve this pattern|Queued for the next turn|Reason:|to execute|to cancel|approve permanently'

diff <(grep -nE "$P28_PATTERNS" "run.py.${OLD_HV}") \
     <(grep -nE "$P28_PATTERNS" "run.py.${NEW_HV}")
# 期望: 空 diff. 有 diff → 立刻 update _P28_REPLACEMENTS.
```

**修 pattern** (若 diff 有输出):

编辑 `edge/hermes-plugins/catfish-xcatfish-user/plugin.py` `_P28_REPLACEMENTS`
list, 加新版 pattern (**长 first 排前** — str.replace 短前缀会先命中打断),
**保留老版 pattern 兜底** (客户装老 hermes 时不破).

### Step 3: 升级时机 (实际操作)

```bash
# 装机版本 pin (员工本机)
cd ~/.hermes/hermes-agent
git fetch
git checkout "$HERMES_VERSION"

# 跑 plugin 验证脚本
HERMES_ROOT=~/.hermes/hermes-agent bash ~/person_task/catfish/edge/catfish-cli/scripts/audit_hermes_compat.sh

# 重启 hermes
hermes gateway restart
sleep 12  # 等 plugin install daemon thread 跑完

# verify plugin install ✓
grep -E "delayed install ✓|catfish-xcatfish-user" ~/.hermes/logs/gateway.log | tail -5
# 期望: 看到 "delayed install ✓ (P3.5.53 路径)" 或类似 INFO

# verify 不再 "未装载"
awk '/2026-XX-XX HH:MM:/,0' ~/.hermes/logs/gateway.error.log | grep "未装载" | head -5
# 期望: 0 命中 (按 hermes restart 时刻过滤)

# verify chat e2e
# Companion 跑一条 "测试" — 看上游 LLM 真返结果, picker 真生效

# verify P28 中文化 e2e (v0.19+ 必跑)
# 微信/WeCom/Slack 中文 IM 发 1 条会触发危险命令的 msg (e.g. "帮我 ls ~/Documents"),
# LLM 会调 execute_code → 触发 approval outbound. 应看到:
#   "⚠️ 危险命令需要审批:" (中文)
#   "回复 `/批准` 执行 (单次), 或 `/批准 本次会话` ..." (中文, 无英文 /approve)
#
# 若仍看到英文 → 查 P28 fail-loud warn:
grep "P28 miss" ~/.hermes/logs/gateway.log | tail -5
# 期望: 空 (无 miss). 若有 → 按 warn 里 text preview 补 _P28_REPLACEMENTS.
```

---

## 二、Audit 真因链 — 8 步顺序 (鸿波 6/21 sprint 真血泪)

```
1. UI picker → 真发的 model 是啥
   ↓ 验: devtools Network → POST /v1/chat/completions → Request Data
       看 body.model 真字符串

2. body 真到 hermes 8642 没
   ↓ 验: tail -f gateway.log + 发 chat, 真看到 "POST /v1/chat/completions"
   ↓ 错则: hermes 8642 LISTEN? curl /health 200? Companion CSP/CORS?

3. hermes 8642 真接到 chat, plugin middleware 真触发没
   ↓ 验: P11 stash middleware 应 set CV_PICKER_MODEL — 加 logger.warning probe 看
   ↓ 错则: middleware register fail / Application 已 frozen

4. P11 装上 ≠ install() 全套 ≠ _INSTALLED=True
   ↓ 区分: P11 通过 Application.__init__ patch (同步段 P8/P9 顺带装上) 真生效,
       但 install() 真跑全套 patch 才 set _INSTALLED=True
   ↓ 验: grep "delayed install ✓" + grep "未装载"

5. _INSTALLED=False → pre_tool_call_safety_check raise → chat tool call block
   ↓ 验: grep "pre_tool_call_safety_check raised" 数量
   ↓ 错则: 看 _delayed_install timeout / install() 真 traceback

6. install fail 真因
   ↓ 看 traceback. 常见:
     - 30s timeout, model_tools 没 ready → 死锁 (P3.5.53 修)
     - import circular (run_agent partial init) → 移到 _delayed_install (P3.5.50 修 P10)
     - middleware register too late (Application frozen) → Application.__init__ patch (老 path)

7. Companion 端 picker UI 显 X 但 send 真发 Y
   ↓ 真因 几乎都是 store reset/effect 改了 model
   ↓ 验: console 看 useChatStore.getState().model 跟 devtools Network 真 body
   ↓ 修: store reset() 别清 modelPickedByUser (P3.5.52)

8. dev mode 通 prod build fail
   ↓ 真因: tauri.conf.json CSP / capability / ATS 严格度差异
   ↓ 验: dev vs prod 对比 CSP connect-src / img-src / ATS
   ↓ 修: connect-src 加缺的 host (P3.5.50.2 加 http://localhost:*)
```

**关键纪律**: 每步 ground truth 出来重新评估假设, **不要扛着前步的假设跑**. 6/21 sprint 我连错 8 次都是因为跳到结论.

---

## 三、已知坑分类 (按 sprint 顺序)

### 坑 1: web_fetch 长文件 truncate, grep 不到 attribute

**症状**: 我用 web_fetch 拉 api_server.py 拿到 2633 行, grep `_run_agent` 0 命中, 武断报 "P15 100% 破".

**真因**: web_fetch 实际 truncated. api_server.py 真 4406 行. `_run_agent` 在 line 3571.

**修**: 用 `curl -sL --max-time 10` 拉, 不用 web_fetch. 拉完先 `wc -l` 跟 GitHub 网页对照, 确认没截断.

### 坑 2: P10 circular import (v0.17 plugin discover 时序变)

**症状**: hermes restart 后 `cannot import name 'AIAgent' from partially initialized module 'run_agent'`. P10 patch 装不上, cascade 把 P15.2 chat_approval middleware 也跳过.

**真因**: v0.17 `run_agent.py:22` 顶 imports `from model_tools import (...)`. model_tools init 时触发 plugin discover → catfish register → P10 同步段 `from run_agent import AIAgent` 撞 partial init.

**修 (P3.5.50)**: P10 从同步段移到 `_delayed_install`. P15.2 拆独立 try (防 cascade). P10 wrap 是 AIAgent **instance** method, LLM first call 通常 > 30s 后, delayed install 装上 on time.

### 坑 3: HERMES_SERVICE_TOKEN 自动续期死链

**症状**: error.log `WARNING: P3.5.44 HERMES_SERVICE_TOKEN 剩 -242044 秒该续但 CATFISH_HERMES_CLIENT_SECRET 没配`. token 过期 74h, /api/* 全 401.

**真因 (3 嵌套)**:
1. `setup-catfish-edge.sh` 装机不自动 mint token, 只注释提示员工"用 mint script"
2. mint script env name `CLIENT_SECRET` ≠ plugin auto-renew env `CATFISH_HERMES_CLIENT_SECRET`
3. mint script 不持久化 secret (注释 "明文不存历史"), plugin 永远拿不到

**修 (P3.5.48)**:
- `_client_secret()` 加 `CLIENT_SECRET` fallback
- mint script 加 `--persist-secret` flag, 写 secret 到 .env
- setup script 加 step 3 检测 + 交互式收 + 调 mint --persist-secret 一次

### 坑 4: RBAC unknown tool warning (v0.17 Progressive Tool Disclosure)

> ⚠️ **P3.5.161 archived (7/3 鸿波)** — 本坑对应的 `_audit_unknown_tools` +
> `KNOWN_BUILTIN_TOOLS` 已全删. hermes v0.18 `tools/registry.py:395-408` 上游
> 已本身防 override, catfish 侧防线冗余. 详见 CHANGELOG P3.5.161.
>
> 保留下述历史记录作为**升级追踪参考** (hermes 升级新 tool 时该 warn 曾出现).

**症状**: log 反复 `BL-RBAC-DAY4-HARDENING: 3 unknown tool name(s): tool_call, tool_describe, tool_search`.

**真因**: v0.17 新加 `tools/tool_search.py` Progressive Tool Disclosure feature. MCP + 非 core plugin tools 超 context 10% 时 hermes 自动用 3 bridge tool 替换暴露给 LLM. catfish `KNOWN_BUILTIN_TOOLS` 是 hermes 0.14 时代清单.

**修 (P3.5.49, 已归档)**: `tools_sanitizer_constants.KNOWN_BUILTIN_TOOLS` 加 `tool_search/tool_describe/tool_call`. warning 文案改成 "hermes 升级新 builtin / plugin 新 tool 嫌疑" (不再说 tool_override).

### 坑 5: build 模式 chat "Could not connect" / dev 模式 OK

**症状**: dev mode chat work, prod build 后 chat 报 `TypeError: Load failed`.

**真因**: `tauri.conf.json:56` CSP `connect-src` 只 allow `http://127.0.0.1:*`, 不 allow `http://localhost:*`. catfish 全栈 `http://localhost:8642`. prod build WKWebView 严格 CSP host 字符串 match (`localhost ≠ 127.0.0.1`).

**修 (P3.5.50.2)**: connect-src 加 `http://localhost:* ws://localhost:*`.

### 坑 6: setup-catfish-edge.sh 后 Companion 仍用老 API_SERVER_KEY

**症状**: rotate 完 hermes restart 后 Companion 401.

**真因**: Companion Cmd+Q 没真 quit (Tauri app 后台仍跑). 老 OnceLock cache 老 key.

**修 (P3.5.50.1)**: setup script 完成后强提示 "Dock 右键 → 退出" (Cmd+W 关窗口 ≠ quit).

### 坑 7: picker UI 显 X 但 send 真发 Y

**症状**: UI 显 Qwen3.6-Flash, gateway log `model=catfish-private-main`.

**真因**: `store/chat.ts:reset()` 清 `modelPickedByUser=false` 但**不清** `model` (BL-GLOBAL-MODEL 5/23 设计). 之后 ChatTab catalog effect 看 `!modelPickedByUser` + `catalog.default !== model` 触发 `setModel(catalog.default, false)`. store.model 被静默覆盖. UI 显示有时滞后.

**修 (P3.5.52)**: `reset()` 删 `modelPickedByUser: false`. 用户 picker 选过的 model 整 Companion lifetime 锁定. "yaml 改 default → picker 跟走" 只在 fresh launch (zustand init) 时生效.

### 坑 8 (真正 root cause): _delayed_install chicken-and-egg 死锁

**症状**: catfish_tool_bridge 进程 install ✓, hermes daemon 进程 install ✗. chat 跑 tool call block. mcp-stderr 看 ✓ 但 gateway.log 看 "未装载".

**真因**: `_delayed_install` daemon thread `while sys.modules.get("model_tools") + hasattr get_tool_definitions`. **但** v0.17 hermes daemon (gateway run 模式) 不主动 import `run_agent` (chat lazy import). run_agent 顶 imports 才会引 model_tools. 没 chat 来 → model_tools 永远不在 sys.modules → 30s timeout → install 跳过 → `_INSTALLED=False` → safety_check raise → 任何 chat 都被 block.

**修 (P3.5.53)**: `_delayed_install` daemon thread 主动 `import model_tools` trigger, 不再被动 poll. sleep 0.5s 让主线程 register 完成跳过 partial init 段, 之后 `import model_tools` → model_tools ready → `_mod.install()` 直接跑. v0.17 model_tools 顶 imports 不撞 catfish plugin (no circular).

### 坑 14: vLLM 上游 model 未配 max_output_tokens → dyn 超 cap 空 error 400 (P3.5.79+ 7/22)

**症状**: hermes 内部 aux LLM 调 (title / summary / proactive) 全 502 · gateway
返 `BadRequestError: OpenAIException - error: code = 400 reason = message =
metadata = map[] cause = <nil>` · **空 reason 空 message** · 极难查. 员工正常
chat (Companion / 完整 hermes chat 带 tools) 200 OK, 但 aux 短请求全挂.

**真因** (5 分钟 curl 二分):

1. `models.yaml` `catfish-private-main`: `context_window: 256000` (虚报 · 上游
   admin 实际 vLLM --max-model-len=250000) · **无 `max_output_tokens`** 配置.
2. `app.py:_compute_max_allowed_output_tokens`: 无 max_out → `upper = cw =
   256000` · 短 prompt dyn = `256000 - 5*1.3 - 2048 ≈ 253945`.
3. gateway 把 max_tokens=253945 发给上游 · 超 vLLM 硬 cap 250000 · **上游返空
   error 400** (它没告诉具体原因).
4. 完整 chat (Companion / hermes chat 带 tools) 因为 body 里带 max_tokens 值
   或 prompt 大 dyn 算出更小 · 侥幸 <250K · 蒙过. **短 aux (prompt<10 token)
   dyn 一定 ≈253945 · 一定挂**.

实证 (curl 二分):
```
max_tokens=245760  → 200 ✓
max_tokens=253945  → 502 (dyn 默认值)
```

**修 (P3.5.79+ 7/22)**:

`models.yaml catfish-private-main` 加 `max_output_tokens: 245760` (留 ~4K prompt
余量). dyn 之后 `upper = min(cw=256000, max_out=245760) = 245760` · 自动 clip.

同款给 `catfish-private-vision` 补 `max_output_tokens: 122880` (对应 cw=128000).

**教训**:
1. **`context_window` 是 (prompt+output) 总上限 · `max_output_tokens` 是单次输出上限**.
   两个字段 · vLLM 硬 cap 只管后者 · 前者 gateway 用来算 dyn.
2. **虚报 `context_window` 是有毒的甜蜜** — 员工看着大 · 但短请求 dyn 算出的
   max_tokens 会超上游 cap · silent 挂. 若一定要虚报 cw, 必配 `max_output_tokens`
   卡真 cap.
3. **上游 vLLM 返空 error 400 是最恶心的 debug 场景** · 建议 catfish gateway
   400 时 log raw request body (redact api_key) · 未来查底立即拿 payload · 不
   用二分 curl.

**预防 (下次装机 / 加 model 必查)**:

```bash
# audit_hermes_compat.sh 或独立 script · 校 models.yaml 每个 upstream openai/* 的
# model 都配了 max_output_tokens
python3 -c "
import yaml, sys
cfg = yaml.safe_load(open('central/llm-gateway/config/models.yaml'))
missing = []
for m in cfg.get('models', []):
    upstream = m.get('upstream', {})
    if upstream.get('model', '').startswith(('openai/', 'nim/', 'vllm/')):
        if not m.get('max_output_tokens'):
            missing.append(m['name'])
if missing:
    print('❌ 无 max_output_tokens (上游 vLLM 可能空 error 400):', missing)
    sys.exit(1)
print('✓ 所有 vLLM 上游 model 已配 max_output_tokens')
"
```

若 IT 换上游 vLLM `--max-model-len` · **必须**同步 `models.yaml.max_output_tokens`.

### 坑 13: hermes 升级改 outbound f-string → P28 中文化 silent 挂 (P3.5.79+ 7/22)

**症状**: v0.19 Quicksilver 升级 (7/20) 后, 微信 ClawBot 危险命令审批 reply
段全英文 ("Reply /approve to execute this one operation, /approve session ..."),
员工看不懂 + 铁律砍 `always` 提示丢失.

**真因** (5 分钟一发命中):

对比 `gateway/run.py:370` 新老版:

- v0.18: `f"Reply \`{command_prefix}approve\` to execute"`
- v0.19: `f"Reply \`{command_prefix}approve\` to execute this one operation"`
                                                      ^^^^^^^^^^^^^^^^^^^^^^^^^ 新增

P28 `_P28_REPLACEMENTS` 里的 str.replace 老 pattern 是 v0.18 原文, v0.19 原文
找不到 → silent miss → 全条不翻译 → 英文泄漏微信.

**为啥其他 3 段没挂**: `"⚠️ **Dangerous command requires approval:**"`, `"Reason: "`,
`"⚡ Interrupting current task"` v0.19 没改, str.replace 仍命中. 只 reply 段
被改.

**修 (P3.5.79+ 7/22)**:
1. `_P28_REPLACEMENTS` 加 v0.19 pattern (长 first 排前), 保留 v0.18 pattern
   兜底 (客户装老版 hermes 时用).
2. `_translate_hermes_zh` 加 **fail-loud 检测**: 翻译完 text 仍含 `/approve`
   英文 (且无中文 "回复"/"批准") → `logger.warning('P28 miss: hermes 上游可能
   改了原文, 需 update _P28_REPLACEMENTS. text preview: %r')` → 下次一改
   立刻知道, 别等员工投诉.

**教训**:
1. **Fork 上游硬编 str.replace = 极其脆弱**. 上游改任何 whitespace / 措辞 →
   silent miss. 无别路: **每次 hermes 升级 diff outbound f-string**.
2. hermes v0.19 audit 21 条我漏了这个, 只看 breaking API 没跑 outbound
   string diff. 加进 Section 一 Step 2 (P28 outbound string audit).
3. **fail-loud > fail-silent**. 无 warn 的 silent fallback = 无声的 bug, 员工
   投诉才发现. 每处 str.replace / regex 匹配后加"是否命中"检测, miss 打 warn.

**预防 (下次升级必跑)**: 见 Section 一 Step 2 "P28 outbound string audit".

### 坑 12: cowork 沙箱 view 跟用户本机 view 不一致 — 不要 alarmist 改文件

**症状**: 沙箱跑 `git status` 显示 `deleted: edge/identity/SOUL.md` (一大批文件), `ls edge/identity/` 也说不存在. 但鸿波本机 catfish 应用跑得好好的, ~/.hermes/SOUL.md 软链有效.

**真因**: cowork 沙箱跟用户 mac 双向 sync 不完美, 某些目录 view 偶尔 stale. 用户本机文件实际在 (mtime 还是历史 commit 时间), 沙箱 view 看不到. cargo build / 应用都在用户本机跑, 不受沙箱 view 影响.

**我犯过的错 (P3.5.56 实施时, 6/21)**: 沙箱 git status 显示 deleted, 我 alarmist 喊 "P3.5.55+P3.5.56 都跑不起来 必须先恢复", 跑了 `git restore edge/identity/` 重写工作树 (mtime 从 Jun 16 → Jun 21). 鸿波 catch: "应用不是跑的好好的吗?". 用户本机文件其实一直在.

**对策**: 沙箱 view 出 anomaly 时, **先核实**, 不急做修复:

1. `git log --diff-filter=D -- <path>` — 看是不是有 rm commit. 空 → HEAD 里文件还在 → 用户本机也大概率在
2. 问用户/看应用状态 — 能正常 run / build → 本机文件在
3. 看 git status 显示 deleted 的文件 mtime — 沙箱 mount 可能 lag
4. 不要急 `git restore` / `rm` — 沙箱跑的命令会通过 cowork sync 影响用户本机

**教训**: 沙箱 view ≠ 用户本机 view. cowork mount 是 best-effort sync, 不是实时镜像. 看到 anomaly 先看上下文是不是用户在用 (跑不跑通), 再判断是不是真问题.

---

### 坑 11: catfish-xcatfish-user plugin 客户场景全失效 (P3.5.55 同款问题)

**症状**: 客户装 Companion.dmg 后 chat 跑了, 但: picker 选 Qwen 实际跑 deepseek (P11 picker chain 没生效); 没多租户 header (P1/P2/P3); 没 SSE 压缩 (P19); 没 P15 chat approval; 没 RBAC 等. 总结: P3.5.47-53 sprint 19 patch 全失效.

**真因**:
1. plugin 装机靠 `bash deploy.sh` 手动跑, `~/.hermes/plugins/catfish-xcatfish-user/` 软链到 catfish 源
2. 客户没 catfish git clone, 没人跑 deploy.sh → plugin 目录不存在
3. `hermes_cli/plugins.py:1192 discover_plugins` 扫 `~/.hermes/plugins/` → 找不到 catfish plugin
4. 即使有目录, `hermes_cli/plugins.py:198 _get_enabled_plugins` 还要 config.yaml plugins.enabled 含 catfish-xcatfish-user
5. 客户机两条件都不满足 → 19 patch 全失效

**修 (P3.5.56)** — 跟 SOUL P3.5.55 严格同款:
- `commands/hermes_plugin.rs` include_str!() 内嵌 9 plugin 文件 (~189KB)
- `bootstrap_hermes_plugin()` Companion setup hook 主动同步, 4 状态枚举 (HealthySymlink 不动 / DanglingSymlink / RegularDir / Missing / Other 全 overwrite)
- `ensure_plugin_enabled_in_config()` config.yaml plugins.enabled 自动 ensure 含 catfish-xcatfish-user
- 不重启 hermes daemon (等下次自然重启 / kickstart 生效)
- env escape hatch: `CATFISH_HERMES_PLUGIN_NO_BOOTSTRAP=1`

**为啥 9 文件不是 2**: plugin 是多模块 Python 包. `__init__.py` 入口 + `plugin.py` 主代码 + `plugin.yaml` manifest + 6 个兄弟模块 (resolver / session_registry / session_search_router / memory_router / memory_enforce / hermes_token_renewal). 任一缺失 plugin import 时 raise ImportError, hermes 不加载.

**教训**: Audit plugin 装机的真实文件数, 必到 `ls -la` 实际目录. Agent 第一轮只说 `__init__.py + plugin.py`, 漏了 6 个兄弟模块. 不审清楚就 baked, 漏文件 plugin import error 全完蛋.

---

### 坑 10: SOUL.md catfish 该是 source of truth (鸿波 2 次 catch 后真理解)

**症状**: 客户装 Companion.dmg 后跑 chat, "你是谁?" 回复出现 "Nous Research" / "AI 助手" 字样. 开发者本机 OK, 只客户场景出. 或者 catfish 升级 SOUL.md 后, 员工本机停在老版本.

**真因 (代码直查)**:
1. `edge/identity/install.sh:64` `ln -s catfish/edge/identity/SOUL.md ~/.hermes/SOUL.md` — 软链反向耦合, hermes 读 catfish 源
2. 客户场景没 catfish git clone → 软链 target 不存在 → dangling → `Path.exists() = false`
3. `hermes-agent/agent/prompt_builder.py:1623 load_soul_md()` 返 None → `system_prompt.py:154` 注不上
4. catfish gateway `identity_inject.py` 同款逻辑 — bundle 全空 → 不注 system
5. LLM 无 system → 退化默认人格

**真正诉求 (鸿波 2nd catch)**:
> "**catfish 应该能修改 hermes soul.md 才对, 保证一致**"

catfish 是 source of truth. 应该 catfish 主动写 ~/.hermes/SOUL.md, **强制跟当前 catfish 版本一致** (overwrite), 不是反向让 hermes 引用 catfish 源, 也不是被动兜底.

**修 (P3.5.55 2nd 版)**:
- `identity_bundle.rs`: `include_str!()` 编译时内嵌 4 个 SOUL (~25KB 进 binary)
- `read_file_or_baked`: fs 非空用 fs / 空缺失用 baked (给 identity_bundle 命令走)
- `bootstrap_soul_files()`: Companion setup hook 主动同步, 4 状态枚举决定行为
  - **HealthySymlink** (开发者软链 OK): 不动
  - **DanglingSymlink** (客户): 删后写 baked
  - **RegularFile** (老 Companion 写的): **overwrite 写当前 baked** ← 关键
  - **Missing**: 写 baked
- `CATFISH_SOUL_NO_BOOTSTRAP=1` env escape hatch (调试)

**为啥 regular file 也 overwrite** (1st 版不做, 鸿波 catch 错):
- catfish V1 → V2 升级, 员工新 Companion 启动应该自动同步 V2 SOUL
- 不 overwrite 永远停 V1, 跟 catfish 脱节
- 员工自定义身份走 Dashboard preamble (X-Catfish-Agent-Name/Personality), 不动 SOUL.md
- SOUL 是品牌字段, catfish 垄断控制

**教训 (1st 版被 catch 的)**:
- "软链 + 改即生效" 是开发者便利, 不是分发策略
- 但**只补"被动兜底"还不够** — 一致性诉求要求 catfish **主动同步**
- "保证一致" = catfish 控制写权, 不是"有就 OK"
- 改之前先听清诉求, 别急着补局部

---

### 坑 9: user msg 三路 SQLite 写, dedup 互相覆不到 → UI 双 user bubble

**症状**: Companion chat 输入 "hi", UI 显两个一模一样的 "hi" 用户气泡 (右边 cyan). polling 5s 后 reload session 才出 (新输入瞬间只 1 个).

**真因 (sqlite3 直查 ground truth)**:
```
session 20260621_185745_5e9945:
  12387 user 'hi' @ 1782039465.224   ← Companion 写
  12388 user 'hi' @ 1782039465.573   ← hermes 350ms 后写
  12389 assistant 'hi，有啥事？'
```
3 路 SQLite 写, dedup 互相看不见:
1. Companion: `useChat.ts:619` IIFE `persistMessage(userMsg)` → Rust `session_message_append`
2. hermes: `gateway/run.py:9786` `append_to_transcript(_user_entry, skip_db=agent_persisted)`; agent 自己用 `_flush_messages_to_session_db()` 写 (#860 注释证实)
3. Rust idempotent guard `session_write.rs:278` 写死 `if input.role == "assistant"`, user 完全不查; hermes 跟 Companion 各用各的 connection, Rust guard 也覆不到 hermes

ChatTab.tsx polling 5s 后 `getSession()` reload → store.messages 多 1 行 user → UI 多 1 个 bubble.

历史 32+ 对 dup 最早 2026-05-24, 一直都有, v0.17 升级 + polling 路径让 UI 才看见.

**修 (P3.5.54)**:
- `useChat.ts`: 砍 `persistMessage(userMsg)` IIFE. hermes 是 user msg 唯一 writer
- 附件场景: `attachment_record` 改 fire-and-forget poll `getSession()` 找 hermes 写的 user row rowid (25 × 200ms = 5s), 拿到再 `attachment_record(messageId=hermesRowid)`. timeout 兜底用 client uuid (image base64 resume 失效但 chip 仍显)
- `scripts/cleanup-user-dup-rows.py`: 一次性清洗历史 dup, 自动 backup state.db, 留 Companion 写的早行删 hermes 写的晚行 (attachments.db messageId 挂在早行上)

**教训**: 不要从 UI 现象猜后端. **优先级: sqlite3 直查 > log grep > 代码静态审计 > UI 观察**.

---

## 四、catfish plugin install 真实结构 (这次 audit 才搞清楚)

```
hermes_cli main → gateway run --replace
  → import 各 platform adapter (gateway/platforms/*.py)
  → hermes_cli.plugins.discover_plugins()
    → 扫 ~/.hermes/plugins/ (catfish-xcatfish-user 软链在这)
    → 调 register(ctx)
      → catfish-xcatfish-user/__init__.py:register()
        Step 1: pre_tool_call_safety_check hook 注册
        Step 2.5: memory tool override (ctx.register_tool)
        Step 2.7 SYNC: P0/P1/P3/P4/P7/P8/P9 直接装
          注意: P8/P9 内的 _patched_app_init 把 _aw.Application.__init__ patch
                让 hermes 之后创 Application 时自动注入 P11 + P7 + P15.2
                middleware (path B, work)
        Step 2.7b SYNC: P15.2 chat_approval middleware (独立 try, P3.5.50 拆)
        Step 3: 启 daemon thread _delayed_install
          P3.5.53: 主动 import model_tools → 触发完整 model_tools/run_agent import
          → _mod.install()
            → _verify_patch_targets()
            → _apply_patches() — 跑 P0-P19 全套
              注意: P0/P1/P3/P4/P7/P8/P9 同步段已跑, 这里幂等再跑
                    P5/P6/P11/P10/P12/P13/P14/P15/P16/P17/P18/P19 真在这里装
            → _PATCHED = True, _INSTALLED = True
        register 返
  → hermes 主流程继续
    → APIServerAdapter.connect()
      → 创 self._app = web.Application(middlewares=mws, ...)
        → _patched_app_init fence 检测 mws 含 cors_middleware → inject:
          - _request_stash_middleware (P11)
          - _proxy_404_middleware (P7)
          - _chat_approval_middleware (P15.2)
      → app listen 8642
```

**关键**: P11/P7/P15.2 中间件**通过 path B** (`_patched_app_init` Application.__init__ patch) 在 P8/P9 同步段被装上, 早于 `_delayed_install`. 即使 `_delayed_install` 失败, 这些 middleware 还是真生效的. 但 `_INSTALLED` flag 没 set, safety_check 会 raise, 让 chat 全 fail.

**结论**: `_delayed_install` 必须 work, 否则 `_INSTALLED` False → chat block.

---

## 五、catfish 在 hermes 进程间分布

```
ai.hermes.gateway.plist (launchd) → hermes 主 daemon
   PID X /usr/.../python -m hermes_cli.main gateway run --replace
   → 8642 API server (Companion chat)
   → 各 platform adapter (weixin/feishu/etc)
   → catfish-xcatfish-user plugin 装这里 (chat tool call block 看这里 _INSTALLED)
   → log: ~/.hermes/logs/gateway.log (stdout, INFO) + gateway.error.log (stderr, WARN+)

  └→ subprocess: catfish_tool_bridge mcp_server
       PID Y /usr/.../python -m catfish_tool_bridge.mcp_server
       → MCP server stdio, hermes daemon 调它
       → 它**也 import catfish plugin** (因为 venv 共享), 也跑 register/install
       → log: ~/.hermes/logs/mcp-stderr.log
       → 这进程 install ✓ ≠ hermes daemon install ✓ !!!

  └→ subprocess: catfish_tool_bridge socket-mode
       PID Z /usr/.../python -m catfish_tool_bridge --socket ~/.catfish/tool-bridge.sock
       → Companion Tauri Rust 端通过 unix socket 调它
       → log: stdout (catfish-tool-bridge 自己 log)
```

**这次最大教训**: 我看到 mcp-stderr.log "install ✓" 就以为 hermes daemon plugin 装好. 错. 这是 mcp_server 子进程的 install. **hermes daemon 自己的 install 在 gateway.log/error.log**, 跟 mcp-stderr 是 2 个独立进程.

---

## 六、cowork 跟鸿波协作的纪律 (8 次错猜后总结)

### 一定要做的
- 每次 hypothesis 提出, 写明"这是猜, 真验证靠 ground truth X"
- 真 ground truth 出来跟 hypothesis 不符 → **立即丢 hypothesis, 重新审**
- 不要在中间步骤跳到结论
- 长文件 / oversized output 一律 bash curl 拉到 host, 不要 web_fetch (truncates)
- 配置/构建模式差异 first principle: CSP / capability / ATS, 不是进程 lifecycle
- 进程 "重启" 一定 verify PID 换过 (`pgrep -fl` 真 truth)

### 一定不要做的
- "应该 work 了" 没真测过就 declare done
- 看到部分 ground truth 就推出全图 (e.g. mcp-stderr ✓ ≠ hermes daemon ✓)
- 把 web_fetch truncated 当完整 source grep
- 把 `<select>.value = ""` 推到第一个 select (页面多个 select, 用 `querySelectorAll` 全列)
- 把 zustand store.model = "X" 等于 picker UI 显 X (HMR 双 instance / selector 时序)
- reset() 改 modelPickedByUser 不动 model = 静默 bug 等 catalog effect 来覆盖

### Audit 顺序模板
1. 用户 visible (UI 显示 / 报错文字)
2. 客户端 state (devtools Console / Network)
3. 网络 (curl / lsof / hosts)
4. 服务端入口 (request log + middleware 真触发)
5. 服务端业务逻辑 (handler / function)
6. 服务端下游 (database / upstream API)
7. 配置 (env / yaml / config)
8. 进程/构建模式差异

每步 ground truth 出来再决定下一步, 不要跳级.

---

## 七、Sprint P3.5.47-P3.5.53 全 ship 清单

| ticket | 修的 | 验证 |
|---|---|---|
| P3.5.47 | hermes v0.17 audit script 补 19 patch + bug fix | 53/0 pass |
| P3.5.48 | HERMES_SERVICE_TOKEN 治本自动续期 (3 嵌套真因) | mint + persist 一次写盘 |
| P3.5.49 | RBAC unknown tool — v0.17 progressive disclosure 3 bridge | log 无 warning |
| P3.5.50 | P10 circular import + P15.2 cascade + bind poll + secret prompt | 6s bind |
| P3.5.50.1 | UX 强提示真 quit Companion | 文案 |
| P3.5.50.2 | CSP connect-src 加 http://localhost:* | build mode chat work |
| P3.5.51 | P11/P6 probe 临时 WARNING (后回 debug) | verified picker chain |
| P3.5.52 | reset 不清 modelPickedByUser | picker UI 锁定 user 选择 |
| **P3.5.53** | **_delayed_install 主动 trigger model_tools import** | **gateway 真用 catfish-public-qwen-flash** |

---

## 八、关键代码位置 cheatsheet

```
edge/hermes-plugins/catfish-xcatfish-user/
├── __init__.py
│   ├── register(ctx) — hermes plugin entry, 同步段 + _delayed_install
│   ├── _delayed_install — P3.5.53 主动 import model_tools 修死锁
│   └── pre_tool_call_safety_check — 看 _INSTALLED, False 时 raise
├── plugin.py
│   ├── install() — _PATCHED guard + _apply_patches + 设 _INSTALLED=True
│   ├── _apply_patches() — 跑 P0-P19 全套
│   ├── _request_stash_middleware (P11) — body.model → CV_PICKER_MODEL
│   ├── _patched_app_init (在 _patch_p8_p9_cors 内) — Application.__init__
│   │   monkey-patch, 创 Application 时自动 inject P11/P7/P15.2 middleware
│   └── patched_create_agent (P6) — agent.model = CV_PICKER_MODEL.get()
├── hermes_token_renewal.py
│   ├── _client_secret() — 读 CATFISH_HERMES_CLIENT_SECRET / CLIENT_SECRET fallback
│   └── get_fresh_service_token() — JWT exp 检 + mint
└── memory_router.py — memory tool 5 kind 路由

edge/companion-app/
├── src/store/chat.ts
│   ├── setModel(model, pickedByUser=true) — 设 store.model + invoke set_picker_model
│   └── reset() — 不清 model 不清 modelPickedByUser (P3.5.52)
├── src/tabs/Chat/ChatTab.tsx — catalog effect propagate default (line 166-172)
├── src/tabs/Chat/ChatModelPicker.tsx — <select value={current}> 真 picker UI
├── src/hooks/useChat.ts — sendModel = useChatStore.getState().model
├── src/lib/chat.ts — body.model = effectiveModel, fetch hermes 8642
├── src-tauri/tauri.conf.json — CSP connect-src (P3.5.50.2)
├── src-tauri/src/services/hermes_api_config.rs — OnceLock cache key
└── src-tauri/src/commands/picker_state.rs — 写 ~/.catfish/picker_state.json

central/llm-gateway/src/catfish_gateway/
├── tools_sanitizer.py — _audit_unknown_tools (P3.5.49, ARCHIVED P3.5.161)
├── tools_sanitizer_constants.py — KNOWN_BUILTIN_TOOLS (ARCHIVED P3.5.161)
└── app.py — chat completions handler

scripts/
├── setup-catfish-edge.sh — 装机 (step 3 token + secret 自动配, P3.5.48)
├── mint-hermes-service-token.sh — token mint (--persist-secret flag, P3.5.48)
└── audit-old-memory.py — memory audit

edge/catfish-cli/scripts/
└── audit_hermes_compat.sh — hermes 升级前/后跑这个 (P3.5.47 加全 19 patch)
```

---

## 九、下次 hermes 升级 — 1 行 verify

```bash
# 升级 + 验证一条龙
HV="v2026.X.Y" && \
  cd ~/.hermes/hermes-agent && git fetch && git checkout "$HV" && \
  HERMES_ROOT=~/.hermes/hermes-agent bash ~/person_task/catfish/edge/catfish-cli/scripts/audit_hermes_compat.sh && \
  hermes gateway restart && sleep 12 && \
  grep -c "delayed install ✓" ~/.hermes/logs/gateway.log && \
  echo "✓ install 成功. 下一步 verify (2 条 e2e):" && \
  echo "  ① Companion 跑一条 chat — 上游 LLM 真返 + picker 真生效" && \
  echo "  ② 微信/IM 发'帮我 ls'触发 approval — 应全中文 (P28)" && \
  echo "     若英文 → grep 'P28 miss' ~/.hermes/logs/gateway.log 拿 miss text preview"
```

**⚠ 别跳 Section 一 Step 2** (P28 outbound string audit) — 升级前必 diff
hermes `gateway/run.py` outbound f-string. v0.18→v0.19 就因跳这步踩了微信英文
泄漏坑 (见 Section 三 坑 13).

出错时, 按本文 "Audit 真因链 — 8 步顺序" 走, 每步 ground truth verify, 不要跳.

---

## 十、6/22 鸿波 catch "审批按钮一直不弹" — 别再绕圈圈了

**症状**: Companion execute_code 调用 (含 `rm` 等 destructive) **全部 silent
auto-approve**, 审批按钮永远不弹.

### 8 次错猜 (绕圈圈实录)

| # | 错猜 | 浪费时间在哪 | 真相 |
|---|---|---|---|
| 1 | P15 patch 失效, hermes v0.17 _stream_q 变量名变了 | grep hermes v0.17 _stream_q | 还在 line 1865 ✓ |
| 2 | hermes v0.17 加了 _is_gateway_approval_context() gate, P15 漏 set platform contextvar | 加 P15.1 set_session_vars(platform=api_server) | hermes _run_agent 内部已自己 set, 不需要 |
| 3 | P15.1 加 _PATCH_TARGETS 让 plugin 装不上 → 全 19 patch 失效 → Companion 断 | 静态 verify 模式 | _check_attr_in_source 模式正确, plugin 装上了 |
| 4 | 闭包反射 _stream_q 拿不到 | 加 INFO log 验 cb.freevars | GOT, freevars=('_stream_q',) ✓ |
| 5 | register_gateway_notify(sid) sid 不对 | 加 INFO log | sid 一致, notify_cb_found=True ✓ |
| 6 | P15.3 wrap check_execute_code_guard 用诊断 | wrap 不稳, 重启时 race crash | revert |
| 7 | 用 yolo 路径绕过 (line 1708) | 加 yolo / mode log | _YOLO_MODE_FROZEN=False, mode=manual ✗ |
| 8 | Companion 多次 "无法连接 hermes API" 误判为 P15.3 wrap crash | 紧急 revert P15.3 | 真因是 launchctl kickstart 后 hermes startup chain 要 100+ 秒, lsof 太早查 0 |

### 真因 (read-only 一发命中)

**用 read-only Python script 一次性查清 `_permanent_approved` set 内容**:

```bash
cd ~/.hermes/hermes-agent && venv/bin/python << 'EOF'
import sys, os
sys.path.insert(0, '.')
from tools import approval
approval.load_permanent_allowlist()
print('_permanent_approved:', sorted(approval._permanent_approved))
print('is_approved(*, "execute_code"):', approval.is_approved('any', 'execute_code'))
EOF
```

**结果暴露真因**:

```
_permanent_approved: ['execute_code',  ← ★★★ 红线!
                      'script execution via -e/-c flag',
                      'script execution via heredoc',
                      'shell command via -c/-lc flag']
is_approved("any", "execute_code"): True
```

**真因链** (4 步, 5 秒钟看完): execute_code 调用 →
`check_execute_code_guard:1749 if is_approved(session_key, "execute_code"):` →
立刻 True → `line 1750 return {"approved": True, "message": None}` → silent
auto-approve, **永远不弹按钮**.

由来: 用户某次审批 UI 点 "always" → `approve_permanent("execute_code")` →
写进 `~/.hermes/config.yaml` 的 `command_allowlist`. 跨重启永久.

### 治本 (P20 patch)

`catfish-xcatfish-user/plugin.py` `_patch_p20_block_execute_code_permanent`:
1. wrap `approve_permanent(pattern_key)` — 拒绝 `pattern_key == "execute_code"`
2. wrap `load_permanent(patterns)` — config 加载时过滤掉 execute_code
3. 装机时一次性 sweep — 把 `_permanent_approved` 已含 execute_code 移除 + 持久化

### 教训 (下次审 hermes approval 不再绕)

1. **审 approval 真因第一步: read-only 看 `_permanent_approved` set 内容**.
   一行 Python, 5 秒, 直接看真实 process state, 不用 wrap 也不用重启.
   *别先调试 wrap chain — wrap 不稳风险高 + 装机 timing 撞 launchd 退避*.

2. **5 个 auto-approve 早返点都返 `{"approved": True, "message": None}` 同
   shape**, 不能靠 result keys 区分. 必须看 hermes 自带状态变量:
   - line 1702 docker: env_type
   - line 1707 yolo: `_YOLO_MODE_FROZEN` + `_session_yolo` + approval_mode
   - line 1731 cron: HERMES_CRON_SESSION env
   - line 1738 not gateway: `_get_session_platform()` contextvar
   - **line 1749 is_approved: `_permanent_approved` set 内容** ← 最容易忽略

3. **`approve_permanent` / `load_permanent` 是入口阻断点, 比 wrap
   check_execute_code_guard 关键函数稳得多**. wrap hermes 内部关键 guard
   函数 = 高风险 (P15.3 教训), 走加入点的 wrap 安全.

4. **Companion "无法连接 hermes API" 误判**: launchctl kickstart 后 hermes
   完整启动到 listen 8642 要 100+ 秒 (MCP 133 tools 注册占大头). lsof
   sleep 10 后查到 0 不代表 hermes crash, sleep 60+ 再查或者 grep
   "API server listening" log.

5. **永远不要给 `execute_code` "always" 选项**. execute_code = LLM 任意
   Python 沙箱权限. always-approved = LLM 完全 shell 权限. P20 wrap 已封死.
   *后续考虑改 hermes-side 审批 UI 文案: execute_code 弹窗不显示 "always"
   按钮, 只显示 "once" / "session" / "deny"*.

### 1 行 quick diagnostic (审批按钮异常时跑)

```bash
cd ~/.hermes/hermes-agent && venv/bin/python -c "
from tools import approval
approval.load_permanent_allowlist()
print('execute_code 在 permanent_approved?', 'execute_code' in approval._permanent_approved)
print('is_approved True?', approval.is_approved('test', 'execute_code'))
"
# True True = 真因; False False = 走 P15 chain 真因诊断 (本文 8 步)
```
