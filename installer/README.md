# catfish-installer（一键安装器）

> **状态**：🟡 P0.5（推广种子员工前做）
>
> **定位**：员工的入口。一条命令装好 Hermes + 配置 + Companion App + SSO 首次登录。

---

## 职责

1. 检查/安装前置依赖（Python / pip / git）
2. 用官方源装 Hermes（`pip install hermes-agent`）
3. 从公司分发源装 catfish 插件
4. 下载并安装 Companion App
5. 写入默认配置到 `~/.hermes/config.yaml`
6. 触发首次 SSO 登录
7. 订阅员工部门的 starter skill pack

---

## 员工使用

```bash
curl -fsSL https://catfish.internal.company.com/install | bash
```

---

## 目录结构

```
installer/
├── install.sh              # 对外入口脚本
├── bootstrap.py            # 交互式配置生成
├── config-template/
│   └── hermes-config.yaml
└── verify.py               # 安装后冒烟测试
```

---

## 设计注意

- 不要在脚本里嵌入任何秘密
- 代理环境友好（自动处理 HTTP_PROXY/NO_PROXY）
- 升级也走同一套（`bash <(curl ...) --upgrade`）
