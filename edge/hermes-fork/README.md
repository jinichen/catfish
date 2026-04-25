# catfish / edge / hermes-fork

**深度品牌化 Hermes UI** —— 把 hermes 自己的 banner / "Hermes Agent" / "Nous Research" 字样 / 退出语等替换成鲶鱼。

## 这一层做什么

| 看到的 Hermes 元素 | 替换成 |
|------------------|-------|
| 大 ASCII art 蛇杖 | 鲶鱼 ASCII（保留原版作 fallback） |
| "Hermes Agent v0.10.0" 标题 | "Catfish v0.1.0  ·  rt: hermes-0.10.0" |
| "catfish-private-main · Nous Research" | "catfish-private-main · 鲶鱼平台" |
| "Welcome to Hermes Agent!" | "欢迎使用鲶鱼。直接说事。" |
| "Goodbye! ⚕" | "再见 🐟" |
| `✦ Tip: hermes sessions browse ...` 各类 hermes 提示 | "🐟 Tip: catfish ..." |
| 状态栏 ⚕ 符号 | 🐟 |

## 跟前两层（identity / branding）的关系

```
identity (SOUL.md)        → agent 自我介绍 ("我是小鲶")
branding (catfish 命令)    → 入口命令名 + status banner
hermes-fork (这层)        → 实际跑起来的 UI 视觉
```

三层都做完，员工看到的整个产品 100% 是鲶鱼，无 Hermes 字样。

## 实现策略

**不真 fork** Hermes（避免 maintenance 噩梦），而是：

1. 用 **patch 文件**记录所有要改的位置
2. `install.sh` 应用补丁（备份原文件）
3. `uninstall.sh` 还原原文件
4. `hermes update` 后需要重新 `install.sh`（脚本会处理冲突）

补丁内容只改字符串字面量，不动逻辑。最低风险。

## License 声明

Hermes (Nous Research) 是 MIT。补丁出来的衍生品保留 LICENSE 文件标注 Nous Research 原版权 + 鲶鱼修改版权。

```
Original work: Copyright (c) 2025 Nous Research
Modifications: Copyright (c) 2026 Catfish Platform Team
Both licensed under MIT.
```

## 状态

- [ ] 探 hermes 源码品牌字符串位置
- [ ] 写 patches/banner.patch
- [ ] 写 patches/strings.patch
- [ ] install.sh / uninstall.sh
- [ ] 跨 hermes 版本兼容性测试
