---
name: catfish-local-search
description: 在员工本地 Mac 上做全文搜索 —— 找文件、找文档、找合同、找笔记、找邮件附件、找代码、回忆"上次那份…"。基于 SQLite FTS5 trigram 索引，支持 Office (Word/Excel/PPT)、PDF、Markdown、纯文本、40+ 种代码/配置格式。完全离线、不联网，索引库只在员工本机 ~/.catfish/search.db。
version: 0.1.0
author: 鲶鱼 Catfish Platform Team
license: MIT
metadata:
  hermes:
    tags: [local-search, file-search, offline, productivity, document, fts5, catfish]
    related_skills: [file]
---

# catfish-local-search

让小鲶能调用员工本地的文件搜索引擎。

## 何时调用（重要）

**必须主动调用** 的场景关键词：

- "帮我找一下 …" / "找出 …" / "搜一下 …"
- "我电脑里 …" / "本地有没有 …" / "我的 Documents/Downloads 里 …"
- "上次那份 …" / "上周/上月那份 …" / "之前的 …"
- "合同 / 发票 / 报价单 / 简历 / 笔记 / 文档 / 设计稿 / 日报 / 周报"
- "我之前写过的代码 …" / "项目里 …"

**不要调用** 的场景：

- 网络搜索（用 web_search 工具）
- 数据库查询（用 SQL 相关工具）
- 想生成内容（直接生成）

## 怎么用

调用方式：在 bash 里跑 `catfish-search query --json`：

```bash
catfish-search query --json -n 10 "<关键词>"
```

参数：
- `--json`：返回 JSON 数组，便于解析
- `-n N`：限制返回前 N 条（默认 10）

返回结构（JSON 数组，每项）：

```json
{
  "path": "/Users/chenhongbo/Documents/合同/2025-12-某某某.docx",
  "title": "2025-12-某某某",
  "file_type": ".docx",
  "snippet": "... 双方就 [[合同]] 标的物达成 ...",
  "score": -2.34
}
```

字段说明：
- `path`：文件绝对路径
- `title`：文件名（不含扩展）
- `file_type`：扩展名（带点）
- `snippet`：关键词附近的片段，命中部分用 `[[ ]]` 包起来便于阅读
- `score`：bm25 分数，**越小越相关**（不是越大越相关，注意）

## 实战示例

用户："帮我找一下我电脑里关于鲶鱼项目的设计文档"

```bash
catfish-search query --json -n 10 "鲶鱼 设计文档"
```

如果是两字关键词（中文），会自动走 LIKE 降级（FTS5 trigram 最少 3 字）。

用户："上周那份合同在哪里？"

```bash
catfish-search query --json -n 5 "合同"
```

按 score 升序排序，挑出最相关几份。如果命中太多，可以追加更具体的关键词（"上周" 没有意义因为索引不带时间维度，但可以加合同方/项目名缩窄）。

## 后续动作建议

找到候选文件后，根据用户意图：

1. **直接打开**：`open "<path>"`（macOS）
2. **读内容回答问题**：用 `read_file` 工具读 path 的内容
3. **二次过滤**：拿到 5~10 条后让用户确认哪个是 ta 要的
4. **失败兜底**：如果完全没命中，告诉用户"本地索引里没有匹配，可能是没加进 ~/.catfish/search-scope.yaml 的 include 列表"

## 限制和注意

- 只索引员工**明确加进** `~/.catfish/search-scope.yaml` 的目录，不会扫全盘（隐私边界）
- 富文档（.pdf .docx .xlsx .pptx）需要 markitdown 支持，否则只索引纯文本
- 索引是**异步增量**的（watcher 在后台跑），刚写的文件可能要 2~3 秒才进索引
- 文件改名/删除会被 watcher 自动处理；但如果索引落后，结果里可能出现已不存在的 path，这时直接告诉用户"这个路径已失效"

## 相关命令（需要时备查）

```bash
catfish-search status          # 看索引库状态（总文件数 / 大小 / 按类型分布）
catfish-search index           # 全量重建索引
catfish-search clean           # 清理已失效条目
catfish-search config          # 编辑 ~/.catfish/search-scope.yaml
catfish-search daemon status   # 看 watcher 是否在跑
```

不要主动跑 `index` / `clean`，那是用户的运维操作。
