# 极简 Web UI

> **状态**：🔵 P2（第 2 月以后，可选）
>
> **定位**：给不想装 Companion App 的员工一个纯浏览器的访问入口。

---

## 优先级

- 不是核心差异化（聊天 UI 全世界都有）
- 只在 Companion App 稳定后，作为"访客模式"补充
- **MVP 阶段不做**

---

## 范围

- 单页应用，走公司 SSO
- 聊天界面 + Skills Hub 浏览入口
- 后端走 catfish-gateway（OpenAI 协议）
- 不实现 Companion 的高级能力（快捷键、OS 读取、SMTP）

---

## 技术选型（暂定）

- Vite + React / Vue
- TailwindCSS
- 部署：内网 Nginx 或 S3+CloudFront
