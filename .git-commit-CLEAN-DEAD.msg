BL-MANIFESTO-CLEAN-DEAD: 删 tool_archive 6 个 dead module + 缩 archiver/test

完整 audit 后确认: 5/22 BL-CENTRAL-EDGE-TOOL-ARCHIVE Phase 6a + 6/7
BL-CATFISH-MANIFESTO clean-up 后, tool_archive/ 下大半 module 没有任何
production caller, 整套 rm.

## audit 依赖图 (production code only)

唯一 live caller:
  app.py L3039: tool_archive.image_folder.fold_history_images / is_folding_enabled
  app.py L3053: tool_archive.prepare_tool_messages

整套 dead chain:
  router.py (HTTP /api/tool-archives/*) → 6/7 app.py 已 unwire
    └ 内部依赖 db, reader
  summary_worker.py (haiku 摘要 worker) → 6/7 app.py lifespan 已 unwire
    └ 内部依赖 db, features, prompts
  archive_tool_messages (archiver.py 老入口) → 6/7 prepare 强制 truncate, 不再调
    └ 内部依赖 db, features, prompts
  reader.py / db.py / features.py / prompts.py → 上面 caller 全 dead, 跟着死

## 删 6 个 file

- central/llm-gateway/src/catfish_gateway/tool_archive/router.py (130 行)
- central/llm-gateway/src/catfish_gateway/tool_archive/summary_worker.py (209 行)
- central/llm-gateway/src/catfish_gateway/tool_archive/reader.py (126 行)
- central/llm-gateway/src/catfish_gateway/tool_archive/db.py (246 行)
- central/llm-gateway/src/catfish_gateway/tool_archive/prompts.py (148 行)
- central/llm-gateway/src/catfish_gateway/tool_archive/features.py (59 行)

## 缩 3 个 file

- archiver.py: 删 archive_tool_messages 函数 + derive_session_id + _compute_ref +
  _count_lines + _is_archive_candidate 5 个 helper + `from . import db, features,
  prompts` import. 只剩 prepare_tool_messages truncate-only wrapper. 232 行 → 49 行
- __init__.py: 删 archive_tool_messages / is_archive_enabled / THRESHOLD_BYTES
  re-export. 只剩 prepare_tool_messages. 32 行 → 38 行 (注释多了点)
- tests/test_tool_archive.py: 删 archive / prompts / reader / db / features /
  derive_session_id / skip_instructional / archive_still_works 全套 test. 只留
  prepare_tool_messages 3 个 test (跟 manifesto cleanup 同款). 473 行 → 41 行

## 不删

- image_folder.py: **LIVE**, app.py L3039 调 fold_history_images / is_folding_enabled.
  跟 archive 完全无关 (是 multimodal 历史截图折叠, 文件位置历史遗留, 后续 sprint
  refactor 搬出 tool_archive/ 目录)
- alembic 20260511_003/004_tool_archives.py: 建 PG `tool_archives` 表的 migration.
  现在没人写新 archive 进 PG (5/26 BL-BOUNDARY 砍后), 但表 schema 保留, 历史 archive
  数据可能还在 PG. 后续 BL-TOOL-ARCHIVE-DROP-TABLE 单独 migration drop 表.

## 同步更新 app.py L3045-3057 comment

老 comment 还引用 dead 概念 (BL-Q3-ARCHIVE / catfish_read_tool_archive 召回 /
features.is_archive_enabled / derive_session_id / summary_worker). 全删, 改为
反映 6/7 后现状 — "prepare_tool_messages 物理上只能 truncate, 跟 manifesto 公理 4
'API surface 物理无能' 一致".

## 净删 1602 行

- 11 file changed
- +81 / -1676 (净删 1595)

## verify

- python3 -m py_compile 全过 (整个 catfish_gateway/ 树扫一遍 OK)
- image_folder.py 独立 import 验证 fold_history_images / is_folding_enabled 都活
- grep 已删 symbol (archive_tool_messages / is_archive_enabled / derive_session_id
  / THRESHOLD_BYTES / from .router|db|reader|prompts|features|summary_worker) 在
  src/ 下**无残留** (除了 archiver.py docstring + app.py 历史 comment 提及, 不是 import)
- sandbox 无 catfish_gateway 包没法跑 pytest, 鸿波本地 verify

## 跟 catfish-central-manifesto 公理 4 关系

整套删后, gateway 中央服务**物理上**不再有任何 archive 写读 API surface. "API
物理无能" 真做到了, 不是承诺.

跟前 5 份战略 / 战术 doc 联动: 这是 manifesto + clean-up + clean-dead 三步收尾的
最后一步.
