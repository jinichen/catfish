# catfish-feishu-monitor

**飞书消息实时相关性过滤 + 自动草稿**。完全员工本地，领导和 IT 都看不到任何"鲶鱼"痕迹。

## 定位

鲶鱼的三大支柱第四块拼图 —— 帮员工处理来自领导/同事的飞书消息而**不被拉进飞书**。领导照常在飞书发消息，员工本地的鲶鱼：

1. **监听**员工自己的飞书 Web（在 Catfish 专用 Chrome 里）
2. **过滤**哪些跟员工真正相关（@员工、提名字、提项目关键词）
3. **实时通知 + Hermes 内追加 + 预生成回复草稿**，三管齐下让员工不错过重要信息也不被琐事打扰
4. **草稿要员工审核、员工本人发送** —— 鲶鱼永远不作为聊天身份出现

## 工作原理

```
员工 Catfish Chrome（~/.catfish/chrome-profile）
├── 飞书 Web (https://www.feishu.cn) 登录并持久化
└── Chrome DevTools Protocol（9222）
       ↓
   catfish-feishu-monitor（Python daemon，常驻）
   ├── CDP 连接 → 注入 MutationObserver JS
   ├── 监听 DOM 变化 → 新消息事件
   ├── 相关性过滤（基于 catfish memory 的员工项目/姓名/同事关键词）
   └── 分发到三个处理器
          ├── macOS 通知（osascript）
          ├── Hermes 内追加（写到员工 session 的 inbox）
          └── 草稿生成器（调 Hermes/gateway LLM 生成回复草稿）
```

**关键隐形保证**：

- 不注册飞书应用（不走开放平台）
- 不用任何 Bot 身份
- 不往飞书服务器发任何主动请求（除了员工自己的日常登录）
- 鲶鱼只"看"员工自己已经在看的页面，不越界

## 目录结构

```
edge/feishu-monitor/
├── README.md
├── pyproject.toml
├── install.sh                   # 一键装（写 launchd 常驻）
├── uninstall.sh
├── src/catfish_feishu/
│   ├── __init__.py
│   ├── cli.py                   # catfish-feishu CLI（start/stop/status/test）
│   ├── cdp_client.py            # CDP WebSocket 客户端 + JS 注入
│   ├── monitor.py               # 主循环（事件接收 + 分发）
│   ├── relevance.py             # 相关性判定（关键词 + 语义双档）
│   ├── handlers.py              # 三个处理器：通知 / Hermes / 草稿
│   ├── config.py                # ~/.catfish/feishu.yaml 配置
│   └── scripts/
│       └── feishu_dom_observer.js  # 注入到飞书 Web 的监听脚本
└── tests/
    └── test_relevance.py
```

## 关键依赖

| 外部依赖 | 用途 | 前置 |
|---------|------|------|
| Catfish Chrome 已开 + CDP 就绪 | 必须 | 跑过 `catfish-browser-attach.sh` |
| 飞书 Web 登录持久化 | 必须 | 员工在 Catfish Chrome 里登过一次 |
| `~/.catfish/memory.yaml` 里有员工姓名/项目关键词 | 推荐 | Hermes memory 自动填；员工也能手动配 |
| `websockets` Python 包 | 必须 | pip 可得 |

## 配置文件

`~/.catfish/feishu.yaml`：

```yaml
# 相关性判定关键词
relevance:
  # 直接相关（命中就通知）
  strong:
    names: ["陈鸿波", "鸿波"]
    mentions: ["@陈鸿波"]
  # 弱相关（命中记到 Hermes inbox 不弹通知）
  soft:
    projects: ["鲶鱼", "catfish", "合规平台"]
    systems: ["网关", "Hermes", "LLM"]

# 处理策略
handlers:
  desktop_notification: true     # macOS 通知中心弹窗
  hermes_inbox: true             # 写到 Hermes 的 inbox 段
  auto_draft: true               # 预生成回复草稿

# 草稿生成参数
draft:
  model: "catfish-private-main"  # 默认用内网 Qwen
  max_length: 200                # 草稿别太长
  tone: "simple-professional"    # 草稿风格：简洁职业

# 运行参数
runtime:
  cdp_url_from_hermes_config: true  # 从 ~/.hermes/config.yaml 的 browser.cdp_url 读
  log_path: "~/.catfish/feishu-monitor.log"
  debounce_ms: 500               # 短时间内多条消息合并处理
```

## 使用姿势

装完之后：

```bash
catfish-feishu start        # 启动 daemon
catfish-feishu status       # 看状态：连上 CDP 没 / 监听哪几个群
catfish-feishu stop

catfish-feishu test         # 模拟一条相关消息，验证三个处理器都工作
catfish-feishu drafts       # 查看待审核草稿列表
```

日常：员工在 Hermes 里会看到：

```
📬 Feishu inbox (3 new, 2 relevant)
  [1] 张总 在 "部门周会" 群 @你：本周合规平台对接进度怎么样？
      草稿已生成：/draft-reply 1
  [2] 李工 在 DM：鲶鱼这边的 OAuth 什么时候上？
      草稿已生成：/draft-reply 2
```

员工点 `/draft-reply 1` 看草稿，改了再发（或原样复制回飞书）。

## 路线图

- **MVP（本周）**：监听 + 关键词过滤 + 三个处理器基础功能
- **V1**：草稿质量调优（学员工自己的历史回复风格）
- **V2**：同义词扩展、拼音/英文互译（"鸿波" == "Hongbo"）
- **V3**：主动聚合（每天早上给员工一份"昨天有哪些群里提到我"的摘要）
- **V4**：跨平台（微信、钉钉、Teams）共用这套监听框架

## 合规与隐私

- **不存凭据**：不读飞书 cookie，不调飞书 API
- **不外发**：消息内容永远不出员工本机（草稿生成调员工授权的 gateway LLM，走内网 Qwen 或公共 Gemini 按员工选）
- **员工可随时关停**：`catfish-feishu stop` 即时停止，Catfish Chrome 照常用
- **日志不留原文**：log 只记 "某时刻触发了相关性判定，命中 N 条"，不落盘消息内容

## 不做的事

- **不发消息到飞书**：鲶鱼永远只"看"不"说"。所有发送动作必须员工本人完成
- **不用飞书 Bot SDK**：避免任何可能的 Bot 身份暴露
- **不走飞书开放平台**：IT 后台会看到应用，违背"完全隐形"原则
- **不监听员工不在的飞书群/DM**：只处理员工飞书 Web 已打开的页面
