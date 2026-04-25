# Hermes 定制与扩展

> **状态**：🟡 部分（Browser 工具增强 Week 2 起）
>
> **定位**：**不 fork Hermes 源码**，用 config 覆盖 + 插件扩展 + 工具增强来定制。

---

## 职责

- 默认 config（指向鲶鱼网关、默认 personality 等）
- 扩展 Hermes 的 browser 工具（见 `browser-tools-enhanced/`）
- 员工启动脚本、升级脚本

---

## 目录结构

```
hermes-customizations/
├── config/
│   └── defaults.yaml          # 公司版默认配置
├── browser-tools-enhanced/    # Browser Agent 深化
│   ├── screenshot_vision.py   # 多模态截图
│   ├── chrome_profile.py      # 员工已登录 Chrome 复用
│   ├── multi_tab.py           # 多 tab 并行
│   └── form_filler.py         # 表单自动填
└── scripts/
    ├── setup.sh               # 新装 Hermes 配默认
    └── upgrade.sh             # 升级并保留公司配置
```

---

## 与 Hermes 插件的关系

- 本目录的工具增强走"tools 目录 drop-in"方式（Hermes 自动发现 `tools/*.py`）
- 策略类 / 钩子类走 `/plugins/` 下的插件
- 两者不混
