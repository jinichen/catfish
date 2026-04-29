# 客户字体目录 (空)

> **客户 IT 部署时, 把公司授权字体 .ttf / .otf 放进**这个目录**.

详见 [../INSTRUCTIONS.md](../INSTRUCTIONS.md).

## 不要 commit 任何字体二进制到 git

`.gitignore` 已配置规则, 强 commit 会被拦.

## 典型内容 (举例, 实际由客户填)

```
customer/
├── PLACEHOLDER.md          ← 这个文件 (commit)
├── FZXBSJW.TTF             ← 方正小标宋简体 (不 commit, 客户填)
├── FangSong_GB2312.ttf     ← 仿宋_GB2312 (不 commit, 客户填)
└── (其他公司风格字体)
```

## 鲶鱼怎么找到这里的字体

启动时 `catfish.fonts.loader.discover()` 扫描这个目录, 把发现的字体路径登记到字体表, skill 调用 `find_font(name)` 时优先返回这里.
