# 致 IT: 鲶鱼 AI 副手接入飞书 + 企业微信 凭证申请

> 鸿波 5/8 早上发给 IT 的请求, 申请 4 件事的凭证. 配置后**鲶鱼平台 5/14 给央企客户演示**, 内部测试用.
>
> **关键**: 我们用 hermes (开源 AI agent runtime) 的 WebSocket 模式, **不需要公网 IP / 不需要 ngrok / 不需要 SSL 证书**. mac 内网直接连飞书 / 企微 服务器.

---

## A. 飞书自建机器人

**用途**: AI 副手通过飞书机器人接收员工消息 + 自动回复. 数据全在我电脑 ~/.hermes/state.db, 不出公司.

### 申请步骤 (IT 操作)

1. 进 [https://open.feishu.cn/](https://open.feishu.cn/) 公司管理员账号登录
2. 创建 → 自建应用:
   - 名称: `鲶鱼-鸿波-内测`  (随便, 内部用)
   - logo: 我提供 (`branding/pet-mascot.svg`)
3. "凭证与基础信息" 给我:
   - **App ID**
   - **App Secret**
4. "事件订阅" 配:
   - 订阅事件:
     - ✅ `im.message.receive_v1` (接收消息)
     - ✅ `im.message.message_read_v1` (@机器人)
   - 给我:
     - **Verification Token**
     - **Encrypt Key**
5. "权限管理" 加权限:
   - ✅ `im:message` 发单聊/群消息
   - ✅ `im:message.group_at_msg` 接收 @ 群消息
   - ✅ `contact:user.base:readonly` 用户基本信息
6. 发布应用 → 提交审核 (公司管理员一键过)

**4 个凭证给我即可, 不需要给 webhook URL** (我们用 WebSocket).

---

## B. 企业微信 AI Bot

**用途**: 同上, 走企业微信群.

### 申请步骤 (IT 操作)

1. 进企业微信管理后台 ([https://work.weixin.qq.com/](https://work.weixin.qq.com/))
2. 应用管理 → 创建应用 → **AI Bot** 类型 (新)
   - 名称: `鲶鱼-鸿波-内测`
3. 拿:
   - **WECOM_BOT_ID**
   - **WECOM_SECRET**

**2 个凭证给我即可** (企微 AI Bot 用的是 hermes 内置 WebSocket, 不需要 webhook).

---

## C. (可选) 个人微信 — 5/14 不演

iLink Bot 模式, 扫码登录, 不需要 IT 申请. 我自己扫.

---

## 安全说明 (给 IT 备案)

1. **数据**: 鲶鱼接收的消息只存我 mac 的 `~/.hermes/state.db` SQLite, **不上公司服务器, 不上 OpenAI / Anthropic**. 内网闭环.
2. **网络**: hermes WebSocket 主动连飞书 / 企微 服务器, **不需要给我 mac 开公网端口**. mac 不可达, 飞书/企微推送从 IT 防火墙看是出站连接, 跟员工自己开飞书 web 一样.
3. **凭证**: App Secret / WECOM_SECRET 只我 mac 的 `~/.hermes/.env` 加密保存, chmod 600, 不进 git, 不分享.
4. **撤回**: 5/14 演示后如果不再用, IT 在飞书 / 企微 后台一键停用应用即可, 凭证立即失效.
5. **审计**: 鲶鱼所有 chat 写 `~/.hermes/.catfish_audit.jsonl` (公司 SIEM 可接), IT 想看任何时点鲶鱼跟我对话用到什么工具一目了然.

---

## 时间线

| 日期 | IT | 鸿波 |
|---|---|---|
| 5/8 早 | 收到这份申请 | 等 |
| 5/8 中 | 飞书机器人创建 + 4 凭证 | 拿到 → setup feishu |
| 5/8 晚 | 企微 AI Bot 创建 + 2 凭证 | 拿到 → setup wecom |
| 5/9 | (留 buffer 应对审批延迟) | dryrun 通双向 |
| 5/14 | 不需要继续支持 | demo 当天演真飞书 + 企微 |

---

## 联系

- 鸿波 (chenhongbo) — 鲶鱼平台
- 内部测试, 演示客户: 中央企业 (5/14 现场)
- 5/14 demo 后会出复盘报告给 IT 备案

---

## 给 IT 的常见 Q&A

**Q: 这跟微信 / 飞书官方机器人有什么不同?**
A: 鲶鱼是公司内部 AI, 用机器人 API 接入. 跟你们已有的"考勤助手机器人"是同一类技术 (机器人协议), 不同的是后端是我们自己的 LLM (qwen 122B 内网) 不是公网 SaaS.

**Q: 数据会泄露吗?**
A: 不会. 飞书/企微 server 只是消息中转 (你公司本来就用), 鲶鱼处理消息在我 mac 内网, 输出结果回飞书. **整个链路里飞书/企微 看到的数据跟员工自己用飞书时一样, 不多不少.**

**Q: 鲶鱼需要存放什么数据?**
A: chat 历史 (在我 mac `~/.hermes/state.db`) + audit log (`~/.hermes/.catfish_audit.jsonl`). 都是我个人 mac, 跟我个人浏览历史一样属于个人数据范畴.

**Q: 5/14 后还要继续给吗?**
A: 5/14 给央企客户演示后, 如果他们要 PoC, 凭证保留. 否则停用即可.
