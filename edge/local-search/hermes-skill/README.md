# Hermes Skill：catfish-local-search

把 `catfish-search` CLI 包装成 Hermes 能理解的 skill，让小鲶在听到"找文件 / 找合同 / 找上次那份…"时**主动**调用本地搜索。

## 安装到本机 Hermes

```bash
# 1. 确保 catfish-search 在 PATH 里
which catfish-search || ln -sf ~/person_task/catfish/edge/local-search/.venv/bin/catfish-search ~/.local/bin/catfish-search

# 2. 软链 skill 到 hermes skills 目录
# Hermes 0.10 只识别预定义的一级 namespace（productivity / apple / github 等），
# 自建一级目录（如 catfish/）不会被加载。所以放 productivity 下面，文件夹名带前缀
# 标识这是 catfish 平台来的。
mkdir -p ~/.hermes/skills/productivity
ln -sf ~/person_task/catfish/edge/local-search/hermes-skill/catfish-local-search \
       ~/.hermes/skills/productivity/catfish-local-search

# 3. 验证
ls -la ~/.hermes/skills/productivity/ | grep catfish
```

## 在 Hermes 里验证 skill 被加载

```bash
hermes
# 进去后看启动 banner 的 "Available Skills" 段落
# productivity 段会列出 catfish-local-search（跟 daily-morning-brief 一组）
```

或者直接发一句测试触发：

```
帮我找一下我电脑里有没有关于鲶鱼项目的设计文档
```

预期 Hermes 会调 `bash catfish-search query --json -n 10 "鲶鱼 设计文档"`，
拿到 JSON 后整理成自然语言回答你"找到 X 份，分别在 ..."。

## 维护

- skill 描述（description 字段）是 Hermes 决定何时主动调用的关键 → 添加新触发词请改 `SKILL.md` 的 frontmatter
- 不需要 Python 脚本（CLI 已经够用），所以这个 skill 目录极简，只有 SKILL.md
- 未来如果想做 MCP server 暴露成原生工具（而不是 bash 调用），在这个目录下加 `mcp/` 子目录即可

## 分发到团队

每个员工拷一份这个目录到自己的 `~/.hermes/skills/catfish/local-search/`
就行。或者打成 .pkg 安装包统一推。
