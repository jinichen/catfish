# catfish-web · 中央门户 (BL-ARCH1)

> **状态**: 🔵 开发中 (5/10 起做, 3 周计划)
>
> **定位**: 客户端 = "我"的体验, web = "组织 / 管理 / 探索"的体验.
> 跟 Companion 桌面 app 互补, 不是替代.

---

## 职责拆分

```
留 Companion (桌面, "我"的视角):
  Identity / 今日 quota (单数字) / 我装的 skill / mcp
  画像 (UserProfile / Relation / MemoryHistory / StyleFingerprint)
  Learning / SkillRevision / Tasks / Curator / Proactive
  桌宠 / 全局快捷键 / 语音 / 浏览器集成 / 对话

搬 catfish-web (本项目, 中央, 跨员工 / 管理 / 探索):
  /me        我的概览 (浏览器版, 比 Companion 更宽屏)
  /skills    Skills Hub 全公司广场 + publish UI + skill 详情
  /mcp       MCP 连接器市场 + IT 配 OAuth credentials (admin)
  /manager   部门视图 (manager+)
  /admin     用户管理 / 配额规则 / billing (admin)
  /audit     历史审计大查询
```

---

## 技术栈

| 选 | 原因 |
|---|---|
| **Vite + React + TS** | 跟 Companion 同栈, lib/me.ts 等组件可复用零改动 |
| **react-router-dom v6** | 标准, BrowserRouter 配合 nginx try_files |
| **oidc-client-ts** | 浏览器 PKCE flow 最成熟, Auth0 推荐, 30KB gzipped |
| **zustand** | 跟 Companion 同 store 库, 学习成本 0 |
| **不引 UI 库** | 卡片样式跟 Companion 完全一致, 自己 100 行 CSS 搞定 |
| **不用 Next.js** | SSR / API routes 用不上 (后端是 gateway), 静态 dist 部署最简单 |

---

## 开发

```bash
cd central/web
npm install
npm run dev   # vite :5173, 走 proxy 转 gateway :8999
```

浏览器开 `http://localhost:5173`, vite 自动跳 OIDC issuer (默认 :8998 catfish-identity)
登录, 完成后回 `/auth/callback` 拿 token + fetchMe → 进首页.

---

## 构建 + 部署

### dev (sidecar 跟 gateway 同台)

```bash
npm run build           # 出 dist/
sudo cp -r dist /var/www/catfish-web/
sudo cp nginx.conf.example /etc/nginx/sites-enabled/catfish.conf
sudo nginx -s reload
```

### prod (Docker)

```bash
docker build -t catfish-web:0.1.0 .
docker run -d -p 80:80 --name catfish-web catfish-web:0.1.0
# 跟 gateway / identity / mcp-registry / skills-hub / secret-broker 一起 docker-compose
```

### env vars (build-time, vite 把 VITE_* 编进 dist)

| var | default | 说明 |
|---|---|---|
| `VITE_OIDC_ISSUER` | `http://127.0.0.1:8998` | catfish-identity 或客户自建 SSO URL |
| `VITE_OIDC_CLIENT_ID` | `catfish-companion` | 跟 Companion 同 client_id, gateway 验 aud 通 |
| `VITE_OIDC_SCOPE` | `openid email profile` | OAuth scope |

例:

```bash
VITE_OIDC_ISSUER=https://sso.client.com VITE_OIDC_CLIENT_ID=catfish-prod npm run build
```

---

## 跟 Companion 的一致性

| 项 | Companion | catfish-web | 一致? |
|---|---|---|---|
| OIDC issuer | `~/.catfish/companion.yaml` | env `VITE_OIDC_ISSUER` | ✓ 同 IdP |
| client_id | `catfish-companion` | `catfish-companion` | ✓ |
| audience | `catfish-companion` | gateway 验 aud claim | ✓ |
| token 类型 | id_token (BL-FIX31 设计) | id_token (同) | ✓ |
| token 存储 | `~/.catfish/oauth/` 文件 (BL-FIX32) | sessionStorage | 不同 (web 关 tab 丢) |
| API 鉴权 | Bearer Authorization | Bearer Authorization | ✓ |
| API 端点 | gateway :8999/api/* /v1/* | nginx /api/* /v1/* | ✓ |

---

## 路由

```
/                    HomePage (按 role 推荐入口)
/me                  我的概览
/skills              Skills Hub list
/skills/publish      publish UI
/skills/:ns/:name    skill 详情 + admin delete
/mcp                 MCP 连接器市场
/mcp/:id             连接器详情 + 订阅
/manager             部门列表 (manager+)
/manager/:dept       部门详情 + 改 quota
/admin               admin 后台首页 (admin)
/admin/users         用户管理 (P0 read-only)
/admin/quota         配额规则 (P0 read-only)
/admin/billing       billing 月报 (P1)
/audit               审计大查询 (manager+)
/auth/callback       OIDC PKCE callback (内部, 不给员工点)
```

---

## 状态 / 已 ship

✅ **5/10 凌晨 (BL-ARCH1 P0 一夜 ship)**:
- 项目骨架 (vite + react + ts + 路由 + zustand)
- OIDC PKCE 浏览器登录 (lib/auth.ts)
- API client (lib/api.ts, 自动 Bearer + 401 跳登录)
- 全部 P0 路由 (Home / Me / Skills / Mcp / Manager / Admin / Audit) + 子页面
- nginx.conf.example + Dockerfile

⬜ **5/11+ P1**:
- /admin/users 加编辑 (改部门 / quota override / 锁账号), users.yaml web 编辑器
- /admin/quota 加编辑 (改 quotas.yaml)
- /admin/billing 月报 (按月 / 部门 / 模型聚合, 导出 PDF/Excel)
- /skills 加评分 + 订阅数排行 (PG schema 已留位 subscribe_count / rating_avg)
- /mcp/admin IT 配 OAuth credentials (Jira / GitLab client_id/secret)
- 单测 (vitest)
- i18n (英文版给海外客户)

---

## BL-ARCH2 配套 (Companion 瘦身)

5/10 凌晨架构反思决定: 5/15 起 Companion 砍 5 个"管理类"卡:

```
DepartmentQuotaCard  → /manager
AuditCard            → /audit
SkillAuditCard       → /audit
SkillsHubCard 浏览部分 → /skills (Companion 留 "我已订阅 + 链接")
McpRegistryCard 浏览部分 → /mcp (Companion 留 "我已订阅 + 链接")
```

每个卡上加 "去 web 看 →" 锚点链接.
