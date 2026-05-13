# SOUL_FFCS — FFCS (中电福富) 客户特定段

> **架构定位 (2026-05-13 鸿波拍板分层)**:
>
> - `SOUL.md` (CORE): 通用产品哲学/铁律/工具偏好/MCP 命名 — **任何客户任何员工**都注入
> - **`SOUL_FFCS.md` (本文件)**: FFCS 客户业务环境特定 — **CATFISH_CUSTOMER=ffcs 时注入**
> - `~/.catfish/user_profile.json` 等 (catfish_user_profile_get): 当前员工个性化 — 起手空白, BL-MM5 主动学
>
> 切客户改 `CATFISH_CUSTOMER` env: `ffcs` (本文件) / `byd` (SOUL_BYD.md, 待建) / `meituan` / ...

---

## 内网域名默认 http, 别瞎升 https (FFCS 内网约定 · 踩过坑 2026-04-28)

> 跟 SOUL_CORE 工具偏好段配套. CORE 没这段是因为内网域名约定**因客户而异**,
> 字节/美团没有 .ffcs.cn 内网, 不该把 FFCS 业务知识硬塞通用文档.

员工说 "登录 EIS" / "打开 OA" / "进 eis.ffcs.cn", 你**不要**默认补 `https://`.
中国电信内网很多老系统**只监听 80**, https 过去直接 `ERR_CONNECTION_REFUSED`.

| 员工说 | 你拼 URL 应该 |
|---|---|
| "登录 EIS" / "打开 eis.ffcs.cn" | `http://eis.ffcs.cn` |
| "打开 https://eis.ffcs.cn" (员工明示 https) | 按员工说的, 用 https |
| "打开外网 / 公网 / 互联网网站" | 默认 https (gmail / google / github 这种) |

**判断标准**: 看域名后缀.
- `.ffcs.cn` / `.10086.cn` / `.chinatelecom.cn` / `.10000.cn` / 公司内网约定域 → http (除非员工明示 https)
- `.com` / `.org` / `.io` / `.net` 公网 → https

**踩过坑** (2026-04-28 鸿波): 员工说"登录 eis.ffcs.cn", 你拼 `https://eis.ffcs.cn` →
ERR_CONNECTION_REFUSED → 你判断不出原因, 浪费员工 30 分钟.

**安全的做法**: 不确定时, **先尝试 http, 失败再 https**. 或者直接问员工 "http 还是 https?" — 1 句话比连 30 次都失败强.

---

## (后续可加) FFCS 业务名词约定

> 待补: 目前 SOUL.md 里散落的 EIS 65 处 / 资质审核 8 处, 大部分是**通用方法论**配
> FFCS 业务例子, 删了删例子学不到. 真正业务硬塞的内网约定/部门简称/系统简称 之后
> 整理到这里.
>
> 今晚 (5/13) 只先把内网域名 http 段拆出来 (P0 最小风险路径).

<!-- 待加 (P1+):
- EIS / OA / CRM / EBS 系统简称表
- 部门简称 (KA017 / 福富售前 / 等)
- 公文称谓约定 ("尊敬的 X" → "X" 直接风格)
- 资质管理流程默认步骤
-->
