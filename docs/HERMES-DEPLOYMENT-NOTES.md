# hermes 部署 — catfish 集成约束 (P3.3.5, 2026-06-09 鸿波)

> hermes 是 catfish 上游依赖, 不归 catfish 仓库管. 但 catfish 装机时**必须**做
> 下面的调整, 不然 hermes 会反复被 launchd 重启, Companion fetch 间歇性挂.

## 根因复盘

`ai.hermes.gateway` LaunchAgent 装机 default 配置 + hermes 内置的
`lark` / `weixin` / `feishu` IM platform 同时启用. 网络抖 (Clash 切 /
VPN 重连 / DNS 暂停解析) 时, lark/weixin/feishu websocket 反复
reconnect, hermes upstream 内部某 watchdog 逻辑触发 `sys.exit(15)`
(具体路径在 hermes upstream 代码, catfish 没改).

launchd `KeepAlive: SuccessfulExit=false` 看到非 0 exit, 立刻拉新
instance. 新 instance ProgramArguments 有 `--replace` flag, **会 SIGTERM
任何同名 hermes 实例**, 形成 cascade restart 风暴. 每次重启有 1-3
分钟 downtime, Companion fetch 全挂.

历史观测: 6/9 16:05–17:05 一小时内 hermes 反复 3 次重启,
17 分钟 / 10 分钟为周期挂.

## 3 处必改

### 1. hermes plist 加 ThrottleInterval + KeepAlive Crashed limit

文件: `~/Library/LaunchAgents/ai.hermes.gateway.plist`

样板见 `edge/catfish-cli/launchd/ai.hermes.gateway.plist.reference`.

关键 2 段:

```xml
<key>KeepAlive</key>
<dict>
    <key>SuccessfulExit</key>
    <false/>
    <key>Crashed</key>
    <true/>           <!-- ★ 只 crashed 重启, 不 nudge restart -->
</dict>

<key>ThrottleInterval</key>
<integer>300</integer>  <!-- ★ 5 分钟最小启动间隔 -->
```

reload:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/ai.hermes.gateway.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.hermes.gateway.plist
```

### 2. 关 hermes 内置 feishu platform

`~/.hermes/.env`:

```bash
sed -i.bak 's/^FEISHU_APP_ID=/#FEISHU_APP_ID=/' ~/.hermes/.env
sed -i.bak 's/^FEISHU_APP_SECRET=/#FEISHU_APP_SECRET=/' ~/.hermes/.env
```

效果: hermes 启动时 `check_feishu_requirements()` 失败, 打 warning
"FEISHU_APP_ID/SECRET not set" 跳过 feishu adapter. 启动 platform 数
从 3 (api_server + feishu + weixin) 降到 2 (api_server + weixin).

**飞书 IM 接收交给上层** (catfish-feishu-monitor 或别的方案, 不依赖 hermes).

> ⚠️ 注意: catfish 仓库 `ai.catfish.feishu-monitor.plist` 引用的
> `catfish_feishu.cli` Python 模块**目前不在仓库里**, 装机后这个
> LaunchAgent 会一直 ImportError. 需要 bootout 它:
>
> ```bash
> launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/ai.catfish.feishu-monitor.plist
> mv ~/Library/LaunchAgents/ai.catfish.feishu-monitor.plist{,.disabled}
> ```

### 3. (待跟进) 关 hermes 内置 weixin platform

实测关掉 feishu 后, weixin 也会触发同样 sys.exit (DNS 失败时).
如果你不需要微信 IM → LLM agent 路径, 顺手关掉:

```bash
sed -i.bak 's/^WEIXIN_ACCOUNT_ID=/#WEIXIN_ACCOUNT_ID=/' ~/.hermes/.env
sed -i.bak 's/^WEIXIN_TOKEN=/#WEIXIN_TOKEN=/' ~/.hermes/.env
```

关掉后 hermes 只跑 api_server, 100% 稳, 但微信收到的消息不会进 hermes
agent 处理.

## Companion 端兜底 (P3.3.5)

即使做了 1+2+3, hermes 还是可能间歇性挂 (内部 watchdog 路径多). Companion
已经加了 graceful retry:

- `lib/chat.ts` 检测 `TypeError: Load failed` / `Failed to fetch` /
  `NetworkError` → 自动重试 1 次 (5s 间隔)
- `components/HermesReconnectBanner.tsx` — 顶部固定琥珀 banner
  "hermes 重启中, 自动重试…" + 倒计时
- 重试期间不显错红框; 失败再走老 onError 路径

用户体感: hermes 挂时, 看到顶部 banner 转 5s, 然后消息正常发出. 不再
是 "无法连接 hermes API: Load failed" 红框报错.

## 验证清单 (装机后跑)

```bash
# 1. hermes 健康
lsof -i :8642        # 应 python3 LISTEN
launchctl list | grep ai.hermes.gateway   # PID 数字 (非 "-"), exit 0

# 2. hermes log 只看到 2 个 platform (api_server + weixin), 没 feishu connect
grep -E "Connecting to|connected" ~/.hermes/logs/gateway.log | tail -10

# 3. 验 ThrottleInterval 生效
grep ThrottleInterval ~/Library/LaunchAgents/ai.hermes.gateway.plist
# 应输出 1 行: <integer>300</integer>

# 4. 验 feishu env 注释了
grep "^#\?FEISHU_APP" ~/.hermes/.env
# 应看到 #FEISHU_APP_ID=... 和 #FEISHU_APP_SECRET=...

# 5. Companion 工作台发条消息 — 应该通
```

## 真实部署坑 — catfish-gateway launchd Bootstrap fail (6/9)

`com.catfish.gateway.plist` 装机时 `launchctl bootstrap` 报:

```
Bootstrap failed: 5: Input/output error
Try re-running the command as root for richer errors.
```

诊断 (已验证):

- `plutil -lint` plist 语法 OK
- `xattr -c` 清掉 `com.apple.provenance` 后还失败
- venv python 路径真实存在
- `sudo launchctl bootstrap` 同样失败 (不是权限)
- `log show --predicate 'process == "launchd"'` 没找到拒原因

可能根因 (没确诊): macOS 15/16 Sequoia/Tahoe 加严 LaunchAgent 限制,
对 `~/person_task/` 下面的 binary / `LimitLoadToSessionType: Aqua` +
`ProcessType: Interactive` 组合不再接受, 但 launchd 不给具体错.

**Fallback** (实测可用): 用 `crontab @reboot` + shell alias 替代 launchd:

```bash
# 1. 开机自启 (crontab)
(crontab -l 2>/dev/null;
 echo "@reboot /Users/chenhongbo/person_task/catfish/central/llm-gateway/venv/bin/python -m catfish_gateway.app > /tmp/catfish-gateway.log 2>&1"
) | crontab -

# 2. 手动拉 alias (挂了重启用)
echo "alias catfish-gateway-start='cd ~/person_task/catfish/central/llm-gateway && nohup ./venv/bin/python -m catfish_gateway.app > /tmp/catfish-gateway.log 2>&1 &'" >> ~/.zshrc
```

代价: 没 KeepAlive 自动重启, 挂了得手动. 但比 launchd 装不上强.

后续要修: 简化 plist (砍 ProcessType / LimitLoadToSessionType / 复杂
EnvironmentVariables), 或者整体迁移到 user-level systemd-like 方案
(launchd 在 mac 上越来越折腾, 部署给客户成本高).

## v0.15.2 → v0.16.0 实测升级流程 (2026-06-10 鸿波)

跨一个 minor (0.15→0.16) 但 hermes upstream 内部塞了 874 commit / 1962
files / 205K insertions / 46K deletions, 实际是大版本变更. 升级原因:
CVE-2026-48710 (Starlette BadHost) 修复需要 Starlette ≥1.0.1, v0.15.2
pin 的是 1.0.0.

### 前提状态

本地 fork (`5-27-catfish-contrib` branch) 比 upstream main 多 7 个 catfish
commit, 其中:
- 3 个 5/29 当天 加 + 砍 (net-zero, 不需 cherry-pick):
  `9c42b2f65 BL-HERMES-PICKER-WIRE-MODEL-OVERRIDE` (5/29 加 638 行)
  `b9be2464d BL-CATFISH-USER-FORWARD-AUX-PORT-015` (5/29 加 137 行)
  `7dad3a88d BL-CATFISH-PATCH-REVERT-TO-PLUGIN` (5/29 砍上面 + 改走 plugin)
- 4 个 真要 cherry-pick:
  `13223aff0` cors: allow tauri://localhost origin
  `92eb9a221` cors: allow X-Catfish-User header
  `112fcdcc6` cors: allow http://localhost:1420 (Tauri 2 vite devUrl)
  `a18c25b3c` BL-HERMES-CATFISH-SKIN (鲶鱼 brand banner / tips / UI)

另外 working tree 有 580 行未 commit 的 catfish 6/4 patch (api_server.py
+505 / run_agent.py +73 / branding.tsx ±4 / uv.lock ±2), 是 5/29
REVERT-TO-PLUGIN 后实测 plugin cover 不全又加回来的源码层 patch.

### Phase 1 — commit working tree

```bash
cd ~/.hermes/hermes-agent

# clean .orig / .rej (patch 失败副产物)
rm gateway/platforms/api_server.py.orig gateway/platforms/api_server.py.rej \
   gateway/platforms/api_server.py.rej.orig run_agent.py.orig

# commit 6/4 未 commit 的 catfish 源码层 patch (X-Catfish-User 透传必需)
git add gateway/platforms/api_server.py run_agent.py \
        ui-tui/src/components/branding.tsx uv.lock
git commit -m "BL-CATFISH-RUNTIME-PATCH-RESTORE 6/4: catfish_outgoing_user inject 重新 patch 进源码"
# → commit 8d5845aaa

# 备份
git tag pre-v0.16-upgrade-$(date +%Y%m%d-%H%M%S)
git branch backup-v0.15.2
cp -r venv venv.bak.v0.15.2
cp ~/.hermes/.env ~/.hermes/.env.bak.pre-v0.16
cp ~/.hermes/config.yaml ~/.hermes/config.yaml.bak.pre-v0.16
```

### Phase 2 — checkout v0.16 + cherry-pick

```bash
git checkout -b upgrade-v0.16 v2026.6.5

# 5 个 commit 一次 cherry-pick (上面 4 个 + 6/4 那个 8d5845aaa)
git cherry-pick 13223aff0 92eb9a221 112fcdcc6 a18c25b3c 8d5845aaa
```

冲突结果 (实测):
| commit | 状态 |
|---|---|
| 13223aff0 (cors tauri) | ✓ auto-merge 干净 |
| 92eb9a221 (cors X-Catfish-User) | ✓ auto-merge 干净 |
| 112fcdcc6 (cors localhost:1420) | ✓ auto-merge 干净 |
| **a18c25b3c (brand skin)** | ✗ 撞 `hermes_cli/banner.py` + `hermes_cli/tips.py` — **skip** (cosmetic, 不影响 chat) |
| 8d5845aaa (BL-CATFISH-RUNTIME-PATCH-RESTORE) | ⚠️ 撞 3 文件: `uv.lock` / `ui-tui/branding.tsx` / `gateway/platforms/api_server.py` |

#### resolve 策略

```bash
# brand skin 撞 → 跳过
git cherry-pick --skip

# 8d5845aaa 撞:
# 1. uv.lock keep ours (pip install 会重生成)
git checkout --ours uv.lock && git add uv.lock

# 2. branding.tsx keep ours (cosmetic)
git checkout --ours ui-tui/src/components/branding.tsx
git add ui-tui/src/components/branding.tsx

# 3. api_server.py 只 1 处冲突 (line 3833-3839), theirs 块跟前面
#    92eb9a221 cherry-pick 重复 (都加 catfish_outgoing_user_run = ...)
#    → 删 conflict markers + theirs 块
sed -i.bak '3833,3839d' gateway/platforms/api_server.py
rm gateway/platforms/api_server.py.bak
git add gateway/platforms/api_server.py

# 继续
git cherry-pick --continue
```

最终落 4 个 commit (跳过 brand skin):
```
5dfdeece8 BL-CATFISH-RUNTIME-PATCH-RESTORE
3f84e53b0 cors: allow http://localhost:1420
78b615099 cors: allow X-Catfish-User header
f0aaedfd5 api_server: allow tauri://localhost origin
3c231eb39 chore: release v0.16.0 (2026.6.5) ← v0.16 base
```

### Phase 3 — 装依赖

```bash
source venv/bin/activate
pip install -e . --upgrade        # hermes 0.15.1 → 0.16.0
pip install 'starlette>=1.0.1'    # CVE-2026-48710 (1.0.0 → 1.2.1)
```

实测装上的新依赖: `pathspec 1.1.1`, `Markdown 3.10.2`.

### Phase 4 — catfish plugin self-test (反射目标静态 grep)

```bash
python3 -c "
import os, re
root = os.path.expanduser('~/.hermes/hermes-agent')
api = open(f'{root}/gateway/platforms/api_server.py').read()
appr = open(f'{root}/tools/approval.py').read()

checks = {
    'P15 _stream_q.put':              r'\b_stream_q\.put\b',
    'P15 _on_delta':                  r'def\s+_on_delta\s*\(',
    'P7 _create_agent':               r'def\s+_create_agent\s*\(',
    'P8 _CORS_HEADERS':               r'_CORS_HEADERS',
    'P8 _cors_headers_for_origin':    r'def\s+_cors_headers_for_origin\s*\(',
    'P8 _TAURI_ORIGINS':              r'_TAURI_ORIGINS',
    'P5 _extract_catfish_outgoing_user': r'def\s+_extract_catfish_outgoing_user\s*\(',
}
for name, pat in checks.items():
    print(f'{name:40s}', bool(re.search(pat, api)))

for name, pat in {
    'P15.2 resolve_gateway_approval': r'def\s+resolve_gateway_approval',
    'P15.2 register_gateway_notify':  r'def\s+register_gateway_notify',
    'P15.2 _gateway_queues':          r'\b_gateway_queues\b',
}.items():
    print(f'{name:40s}', bool(re.search(pat, appr)))
"
```

期望: **10/10 全 True**. 实测全过. 关键发现是 v0.16 把 `_extract_catfish_outgoing_user` 从 upstream 删了 (`X-Catfish-User` 协议 hermes
不再原生支持), 必须靠 catfish patch 重新加回 — 这正是 `8d5845aaa`
BL-CATFISH-RUNTIME-PATCH-RESTORE commit 干的事.

### Phase 5 — 重启 hermes

```bash
# ⚠️ ThrottleInterval=300 注意: hermes 死后 launchd 等 5 分钟才会拉新
# 如果 kickstart -k 后立即查 lsof 没 LISTEN, 别慌, 等几分钟. 或者用
# launchctl kickstart (不带 -k) 触发 launchd 立即拉.

launchctl kickstart gui/$(id -u)/ai.hermes.gateway

sleep 8
lsof -i :8642                                       # python LISTEN
launchctl list | grep ai.hermes.gateway             # PID + exit code
tail -50 ~/.hermes/logs/gateway.log
```

期望:
```
Starting Hermes Gateway...
✓ api_server connected
✓ weixin connected
Gateway running with 2 platform(s)
Cron ticker / kanban dispatcher OK
```

(feishu 关了, 不应该 connect)

### Phase 6 — Companion 工作台验证

发条 hello, 应该:
- ✓ LLM 回复 ("你好! 我是小鲶...")
- ✓ token usage 显示 (例如 `63.4K/1000K`)
- ✓ model picker 显示 (Gemini 3.5 Flash 等)
- ✓ tool call 测试: 发 "现在几点了" → LLM 调 `terminal(command="date")` → 返时间

### 回滚 (任何一步出问题)

```bash
cd ~/.hermes/hermes-agent
git checkout backup-v0.15.2

mv venv venv.v0.16-failed
mv venv.bak.v0.15.2 venv

launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway
sleep 8
lsof -i :8642
```

### 升级后 7 commit 的 5 个跳过原因

| commit | 升级时处理 | 原因 |
|---|---|---|
| `9c42b2f65 / b9be2464d / 7dad3a88d` | 全跳过 | 5/29 当天 加+砍 net-zero, v0.16 base 干净 |
| `13223aff0 / 92eb9a221 / 112fcdcc6` | cherry-pick (3 个) | CORS 基础设施, v0.16 也需要 |
| `a18c25b3c (brand skin)` | skip | cosmetic, 撞 banner.py / tips.py, 不影响 chat |
| `8d5845aaa` (新 commit, working tree 化) | cherry-pick + 手 resolve | catfish_outgoing_user 透传必需, v0.16 删了 upstream 自带的 `_extract_catfish_outgoing_user` |

### v0.16 真正的新东西 (对 catfish 价值评估)

v0.16 主线 "The Surface Release" 改 Electron desktop / Web admin / 远程
hermes / 简体中文 i18n / QQBot — **对 catfish 全部零价值** (Companion
自己是 GUI, 不用 hermes desktop). 唯一硬刚理由是 CVE Starlette.

实测升级后**没修** hermes 间歇 SIGTERM 循环, 仍要 ThrottleInterval=300
+ Companion banner retry 兜底. v0.16 没什么"必须升"的功能, 但也没破坏
catfish 集成 (反射目标 10/10 全在).

## 长期 TODO

| 项 | 归属 |
|---|---|
| 修 hermes upstream 内部 sys.exit 路径 (DNS 失败 → retry 不 exit) | hermes upstream, 不在 catfish 仓库 |
| 找/恢复 `catfish_feishu` Python 包源, 让 ai.catfish.feishu-monitor 真工作 | catfish 仓库 (新功能) |
| Companion retry banner 增加到 3 次 + 渐进 backoff (5s / 15s / 30s) | catfish 仓库 |
| 装机 hermes 时自动 patch plist + .env (写进 catfish init 脚本) | catfish 仓库 |
| **简化 com.catfish.gateway.plist 让 launchctl bootstrap 过** (现在 IO error 5) | catfish 仓库 |

## A2A 跨 mac agent 协作 — future, 不做 (2026-06-10 决策)

### 触发讨论

社区文章 (test-github-repo.vercel.app cross-mac-hermes-api-server-2026-06-08)
讲怎么把 hermes 8642 API server 暴露到 LAN 让另一台 mac 直接 curl
`/v1/chat/completions`. 文章作者自己也写了: ✅ 跨 Mac, ❌ 不是真 A2A 对话.

### 这条路的根本局限 (看代码 ground truth)

| 局限 | 代码层证据 |
|---|---|
| A 看不到 B 的 thinking / tool calls 过程 | `api_server.py` 的 chat completions 只返最终 response, tool progress 通过 SSE event 但只对本进程, 不跨网 |
| 同步阻塞 | B agent loop 没完成 A 卡死 |
| B 不能主动 push 给 A | hermes API server 是 server-only, 不会反向 connect |
| 无持续会话 | `X-Hermes-Session-Id` 是 client 自报, 不是真 federation session |
| 安全模型混乱 | `X-Catfish-User` header 是 client 自报, B 没法验是不是真用户 |
| LAN 0.0.0.0 暴露 | 同 WiFi 任何设备能调你 hermes |

文章 3 个真坑跟 catfish 今天 marathon 撞的事一字不差:
- 真坑 #1 (API server 默认 127.0.0.1) = catfish BL-F10 + commit ec44b0a (5 服务 HOST=0.0.0.0)
- 真坑 #2 (Telegram `***` 截断 secret) = hermes `agent/redact.py` secret 替换
- 真坑 #3 (macOS Tahoe `launchctl bootstrap` IO5) = catfish-gateway plist 装不上, 同根因

### 真 A2A 协议要的能力

- Peer 发现 (谁能联系谁)
- 双向 SSE stream (A 看 B 全过程, B 能反问 A)
- Async task 模型 (提任务 / 看进度 / 拿结果)
- 持续会话跨 peer
- 认证 (per-task token, capability scope)
- 中断 / 撤销 / 超时
- 审计 (中央 0 红线 — 谁让谁做了什么)

实施成本: 1-3 人月.

### catfish 之前做过, 5/26 砍了

`central/llm-gateway/src/catfish_gateway/app.py` 注释 verbatim:

```
A2A self_register 5/26 砍 — Plan D Federation 整套停 (0 真客户用,
详见 docs/HERMES-013-ALIGN.md A2A 段). 不再调 self_register.
```

砍掉的全部组件:
- `/a2a/ask` SSE endpoint
- `/a2a/internal/ask` 内部调用 endpoint
- `a2a_server.py` (整文件, git log --follow 能找回)
- `catfish_a2a_ask` tool-bridge tool
- `~/.catfish/a2a_notifications.jsonl` 收件
- `self_register` (agent 互相注册)

砍的真原因: **0 真客户用**. 不是技术做不到.

### 短期决策 (今天 + 这季度): 不做

理由:
1. catfish 当前主线: "客户机房私有部署 + 数据合规优先 + 员工的数字副手" — 单 agent 视角, A2A 不是主线
2. 上次 Plan D 5/26 砍, 客户场景没变, 重做大概率还是 0 客户用
3. IM 中转 (飞书/微信群 + `require_mention`) 能 cover 80% 跨 agent 需求 (Alice 让 Bob 帮跑任务 / B 完成后 push Alice / 异步)
4. catfish 现在 hermes v0.16 + Companion graceful retry 刚 ship, 稳定性观察期, 不该开新摊子

### 中期触发条件 (重启 A2A 讨论)

任一条出现:
- 客户明确说 "我想看其他 agent 跑的过程 (live thinking + tool calls)"
- 客户要 "多 agent 协同看板" (3 个 agent 同时跑, 实时进度合并视图)
- catfish 第二代产品 roadmap 决定走 multi-agent orchestration

### 如果将来真要做

**不要重新自造协议**. 用 Google **A2A standard** (https://github.com/google/A2A):
- upstream 已有规范 + reference implementation
- 跟 hermes 集成: 在 `gateway/platforms/api_server.py` 加 `/a2a/*` aiohttp middleware route handler
- 复用 hermes 现有 `_stream_q` + tool progress event 走 SSE
- 复用 hermes session DB 做 task 持久化

5/26 砍的 catfish Plan D 代码不要恢复 — 它是自造协议, 跟未来标准不兼容. git log --follow 留作"曾经做过"档案即可.

### 关联文件 (审计用)

- `central/llm-gateway/src/catfish_gateway/app.py` line 238/358/424/563 — Plan D 砍痕迹
- `docs/HERMES-013-ALIGN.md` A2A 段 — 5/26 决策真原因 (TODO: 这份 doc 我没读, 决策前要先读)
- `gateway/platforms/api_server.py` line 4326 — `_PROXY_ALLOWED_PREFIXES` 含 `/a2a/`, 当前是 dead config (B 收到 /a2a/* 透传给上游但没人接)
- models.yaml 里部分 model `recommended_for` 含 `a2a_aux` — dead config tag, 清理优先级低
