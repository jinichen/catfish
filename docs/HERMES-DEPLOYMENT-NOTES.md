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

## 长期 TODO

| 项 | 归属 |
|---|---|
| 修 hermes upstream 内部 sys.exit 路径 (DNS 失败 → retry 不 exit) | hermes upstream, 不在 catfish 仓库 |
| 找/恢复 `catfish_feishu` Python 包源, 让 ai.catfish.feishu-monitor 真工作 | catfish 仓库 (新功能) |
| Companion retry banner 增加到 3 次 + 渐进 backoff (5s / 15s / 30s) | catfish 仓库 |
| 装机 hermes 时自动 patch plist + .env (写进 catfish init 脚本) | catfish 仓库 |
