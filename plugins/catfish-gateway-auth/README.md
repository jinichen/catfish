# catfish-plugin-gateway-auth（SSO + 网关自动配置）

> **状态**：🟡 P0 剩余（手动配置可用，插件待做）
>
> **定位**：让员工不用手动 `hermes model` 填 endpoint/token，一次 SSO 登录就配好。

---

## 职责

- 首次启动引导员工走 SSO 设备码登录
- JWT 存到 OS 原生钥匙串
- 过期前自动刷新
- 覆盖 Hermes 的 provider 配置指向网关
- 注入 `Authorization` header

---

## 技术路径

- Python 包 + Hermes 插件 entry point
- 令牌存储：`keyring` 库
- SSO 流程：OIDC Device Authorization Grant (RFC 8628)

---

## 对员工的体验

```
$ hermes
[鲶鱼] 首次启动，需要登录公司身份
[鲶鱼] 请在浏览器访问：https://sso.company.com/device
[鲶鱼] 输入验证码：BXRT-9KQP
[鲶鱼] 等待登录中... ✓
[鲶鱼] 登录成功，欢迎 张三（研发部）

>>>
```

---

## 前提

- 公司 SSO 支持 OIDC device flow
- catfish-gateway 已升级到真实 SSO 验证（替代 dev token）
