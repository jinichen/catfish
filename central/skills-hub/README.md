# Skills Hub（组织级技能市场）

> **状态**：🟡 P1 第 4 周起
>
> **定位**：让员工自愿分享 skill、让组织层面的知识沉淀形成复利。

---

## 核心原则

1. **员工显式 publish 才上传** — 绝不自动扫描
2. **作者署名永久保留** — 贡献可见、可追溯
3. **订阅数 + 5 星评分** — 好 skill 自然扩散
4. **红线审核不做质量审核** — 用户用脚投票
5. **作者随时可下架**

---

## MVP 范围

- 内部 Git 仓作为存储后端
- `hermes skill publish` CLI 封装
- 简易 Web 浏览页（搜索 / 订阅 / 评分）
- 基础统计（订阅数、使用次数）

---

## 目录结构（开工时会建）

```
skills-hub/
├── api/                # REST API (FastAPI)
│   ├── publish.py
│   ├── search.py
│   └── subscribe.py
├── storage/            # Git backend
│   └── repo_manager.py
├── web/                # 浏览 UI
│   └── frontend/
├── cli/                # hermes skill publish 封装
│   └── publish.py
└── moderation/         # 红线审核工具
    └── scanner.py
```

---

## 激励机制（后期）

- 月度 "Skill of the Month"
- 贡献积分（对接绩效，可选）
- 内部技术分享会优先邀请作者
