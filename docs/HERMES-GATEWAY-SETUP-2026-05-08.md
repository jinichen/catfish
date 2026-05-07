# Hermes Gateway 配置指南 (5/8 鸿波拿到凭证后照做)

> 鸿波 5/7 拍板 BL-D14: hermes gateway 接入飞书 + 企微 + Weixin, 5/14 demo 现场演真双向.
>
> **核弹级好消息**: 飞书 + 企微 都用 WebSocket, **不需要公网 endpoint** — 鸿波 mac 内网部署直接用.

## 验证 hermes 已 update 到 v0.12.0

```bash
hermes --version  # 应该是 v0.12.0 (2026.4.30)
```

如果不是, 先 `hermes update`. 5/7 update 已经做了, brand patch 冲突 stash 在 `c50376beb6f4c18da5708bf7e17598962d427e47` (5/8 后 BL-D14.5 修).

---

## A. 飞书 (Feishu/Lark) — 推荐先配, 央企最常用

### 1. 鸿波公司管理员申请机器人

进 [https://open.feishu.cn/](https://open.feishu.cn/) (中国版) 或 [https://open.larksuite.com/](https://open.larksuite.com/) (国际).

```
新建 → 自建应用 → "鲶鱼小副手" (或者鲶鱼内部代号)
   ↓
"凭证与基础信息" 拿:
   ✅ App ID         (例 cli_a1b2c3d4)
   ✅ App Secret     (例 xxxxxxxxxxxxxx)
   ↓
"事件订阅" 配:
   订阅事件:
     ✅ 接收消息  (im.message.receive_v1)
     ✅ @机器人  (im.message.message_read_v1)
     ✅ (可选) 群成员变化
   拿:
     ✅ Verification Token
     ✅ Encrypt Key
   ↓
"权限管理" 加:
   ✅ im:message            (发单聊/群消息)
   ✅ im:message.group_at_msg (接收 @机器人 群消息)
   ✅ contact:user.base:readonly (基本用户信息)
   ↓
发布 → 应用版本 → 提交审核 (公司管理员一键过)
```

### 2. hermes gateway setup feishu

```bash
hermes gateway setup
# 选 🪽 Feishu / Lark
# 输入 App ID
# 输入 App Secret
# 输入 Verification Token
# 输入 Encrypt Key
# 选 模式 → ★ WebSocket (推荐, 内网用)
# allow_bots: true (允许机器人消息)
# group_policy: allowlist (只你白名单的群能用)
# 保存
```

### 3. 测试

```bash
hermes gateway run    # 前台跑, 看日志
# 看到: ✓ Feishu WebSocket connected
```

打开飞书 app → 找你机器人 → 发消息 "今天有啥任务" → mac 上 hermes gateway log 应该收到.

---

## B. 企业微信 (WeCom) — 央企标配, 5/14 必演

### 1. 鸿波公司管理员申请 AI Bot

```
进 企业微信管理后台 → 应用管理 → 创建应用 → AI Bot
   ↓
拿:
   ✅ WECOM_BOT_ID    (Bot ID)
   ✅ WECOM_SECRET    (Bot Secret)
```

### 2. hermes gateway setup wecom

```bash
hermes gateway setup
# 选 💬 WeCom (Enterprise WeChat)
# 输入 WECOM_BOT_ID
# 输入 WECOM_SECRET
# WebSocket URL 默认 wss://openws.work.weixin.qq.com (别动)
# allow_bots: true
# group_policy: allowlist
# 保存
```

### 3. 测试

```bash
hermes gateway run
# 看到: ✓ WeCom WebSocket connected (heartbeat 30s)
```

企业微信 app → 找 AI Bot → 发消息 → 鲶鱼回.

---

## C. 个人微信 (Weixin) — 5/14 不演 demo, 仅作路线展示

### 限制 (hermes 文档原话)

> - "cannot be invited into ordinary WeChat groups"
> - "does not deliver ordinary WeChat group events"

也就是: 个人微信 hermes 是用 iLink Bot, 只**单聊** (DM) 可靠, **群消息不行**. 央企客户用群多, 个人微信 demo 演 ROI 低.

### 配 (如果你想 5/14 备用)

```bash
hermes gateway setup
# 选 💬 Weixin / WeChat
# 扫二维码登录 (跟微信 app)
# hermes 自动保存凭证到 ~/.hermes/weixin/accounts/
```

---

## 起 gateway 服务

```bash
# 前台 (调试):
hermes gateway run

# 后台 (生产, mac launchd):
hermes gateway install
hermes gateway start
hermes gateway status   # 应该看到 feishu / wecom / weixin connected
```

---

## 一份 chat 历史多入口验证 (Companion + 飞书 + 企微 共享)

```bash
# 1. 起 hermes gateway (含 feishu + wecom)
hermes gateway run &

# 2. 飞书发消息 "你好"
# (mac 上 hermes 收, 写 ~/.hermes/state.db)

# 3. 打开 Companion → 切到对话 tab → sidebar 应该有刚才那条消息
#    (Companion 也是读 ~/.hermes/state.db)

# 4. Companion 接着输入 "再问一句"
# (Companion 写 state.db)

# 5. 飞书 app 应该能看到 "再问一句" 这段对话历史
#    (飞书会显示之前 hermes 处理过的消息)
```

如果 4-5 通, 说明 **unified inbox** 真的工作了 — 飞书 / 企微 / Companion 三处一份历史.

---

## 5/14 demo 演法 (场景 2.8 替换 Telegram → 飞书 + 企微)

```
鸿波 (切终端):
  $ hermes gateway status
  ✓ Feishu connected (websocket)
  ✓ WeCom connected (websocket)
  ✓ State.db: 14 sessions, 192 messages

鸿波 (拿手机, 飞书 app):
  发消息: "今天 KA017 进度怎样"

[mac 终端 hermes log]
  [feishu] received message from chenhongbo: 今天 KA017 进度怎样
  [hermes] → catfish gateway (qwen-122b)
  [hermes] reply: KA017 已完成 4 步 ...

[手机飞书]
  鲶鱼: KA017 已完成 4 步, 详见 ~/Documents/KA017-status.docx (我刚生成的)

鸿波 (切回 Companion):
  打开 Companion → 看刚才飞书那段对话的完整记录

客户问: "我企微也能用吗"
鸿波: 切企微演同样.
```

---

## 注意事项

1. **brand patch 5/7 update 时跟上游冲突** — stash 在 `c50376beb6f4c18da5708bf7e17598962d427e47`. BL-D14.5 5/8 修.
2. **catfish gateway (localhost:8999) 必须先起** — hermes 处理消息会调它. `cd central/llm-gateway && python -m catfish_gateway.app`.
3. **state.db backup** — 配新 platform 前先 `cp ~/.hermes/state.db ~/.hermes/state.db.bak.5-8` 防意外.

---

## 5/8-5/13 sprint 时间线

| 日 | 工作 |
|---|---|
| 5/8 早 | 鸿波公司 IT 申请飞书 + 企微 (1-2 天审批) |
| 5/8 下午 | 修 hermes brand patch 冲突 (BL-D14.5) |
| 5/9 | 拿到飞书凭证 → setup feishu → 测通 |
| 5/10 | 拿到企微凭证 → setup wecom → 测通 |
| 5/11 | 演 demo 场景 2.8 dryrun (飞书 + 企微 + Companion 三入口) |
| 5/12 | (可选) 配 weixin 备用 + 整体 dryrun 5+4 场景 |
| 5/13 | 资料包 + 最终 dryrun |
| 5/14 | demo 当天 ★ |
