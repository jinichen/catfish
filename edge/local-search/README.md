# catfish-local-search · 员工本地文件搜索

> 让小鲶帮员工找自己 Mac 上的东西。
>
> 完全本地运行，**不依赖网关、不依赖内网、不联网**。

---

## 能做什么

- 扫员工指定目录下的 md / txt / pdf / docx / xlsx / pptx 等文件
- 抽取文本到 SQLite FTS5 全文索引
- 小鲶通过 `local_search` 工具调用，返回匹配文件 + 摘要

典型对话：

```
员工：上个月那个客户合同在哪？
小鲶：[调 local_search] 找到 3 个候选：
       1. ~/Documents/2026-03/XX公司-合作合同-v2.docx
       2. ~/Documents/合同/客户/XX-NDA.pdf
       ...
```

---

## 技术栈

| 组件 | 选型 |
|---|---|
| 全文索引 | SQLite FTS5 (trigram 分词，中英文都支持) |
| 文本抽取 | markitdown (md/pdf/docx/xlsx/pptx 全覆盖) |
| 文件监听 | (Phase 2) watchdog 增量更新 |
| 语义搜索 | (Phase 3) bge-m3 via catfish-gateway |
| CLI | 纯 argparse + stdlib |

---

## 快速开始

```bash
cd catfish/edge/local-search
python3.12 -m venv venv
source venv/bin/activate
pip install -e ".[all]"

# 第一次索引（会创建 ~/.catfish/search.db）
catfish-search index

# 查询
catfish-search query "鲶鱼设计"

# 看索引状态
catfish-search status
```

---

## 配置

第一次跑 `catfish-search index` 会自动创建 `~/.catfish/search-scope.yaml`，
默认索引 `~/Documents`、`~/Desktop`、`~/Downloads`。

按需编辑：

```yaml
include:
  - ~/Documents
  - ~/Desktop
  - ~/Downloads
  - ~/notes
  - ~/work

exclude:
  - ~/Library
  - "**/node_modules"
  - "**/.git"
  - "**/*.zip"
  - "**/*.mp4"

max_file_size_mb: 10

file_types:
  - .md
  - .txt
  - .pdf
  - .docx
  - .xlsx
  - .pptx
  - .py
  - .js
  - .ts
```

---

## 数据在哪

全部在员工 Mac 上：

- 索引库：`~/.catfish/search.db`
- 配置：`~/.catfish/search-scope.yaml`
- **从不上传**

---

## 目录结构

```
local-search/
├── pyproject.toml
├── README.md
├── src/
│   └── catfish_search/
│       ├── __init__.py
│       ├── config.py      # 加载 search-scope.yaml
│       ├── extractor.py   # markitdown 包装
│       ├── indexer.py     # 扫文件 + 写 SQLite FTS5
│       ├── query.py       # 查询接口
│       └── cli.py         # catfish-search 命令入口
└── tests/
    └── test_basic.py
```

---

## 阶段路线

- **Phase 1（当前）**：批量扫描 + FTS5 关键词查询
- **Phase 2**：watchdog 实时增量更新
- **Phase 3**：bge-m3 语义向量搜索（"客户签的协议" 能找到 "三方合作合同"）
- **Phase 4**：接入 Hermes 作为 skill，小鲶自动调用
