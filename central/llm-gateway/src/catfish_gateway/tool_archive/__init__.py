"""tool_archive — 6/7 BL-MANIFESTO-CLEAN-DEAD 大幅 cleanup 后只剩 2 个 live file.

## 历史

5/11 BL-Q3-ARCHIVE v1 ship 全套 gateway PG archive (router/db/reader/prompts/
features/summary_worker/archiver), 解决 context overflow. 5/22 BL-CENTRAL-EDGE-
TOOL-ARCHIVE Phase 6a 把整套搬 edge (tool-bridge tool_archive_local.py 真接管),
gateway PG 路径 default 禁用.

5/26 BL-BOUNDARY: db.py 砍 PG, content 100% 员工本机.

6/7 BL-MANIFESTO-CLEAN-DEAD (本次): app.py 已 unwire router + summary_worker
(manifesto cleanup 一并干), 整套 dead code 整套 rm:
  - 删 router.py / summary_worker.py / reader.py / db.py / prompts.py / features.py
  - archiver.py 缩到只剩 prepare_tool_messages 这个 truncate-only wrapper

## 当前模块结构

  archiver.py        — prepare_tool_messages (app.py 调, 永远 truncate)
  image_folder.py    — multimodal 历史截图折叠 (跟 archive 无关, 文件位置
                       历史遗留. app.py L3039 调 fold_history_images /
                       is_folding_enabled. 后续 sprint refactor 搬出去)

## 跟 manifesto 公理 4 关系

整套 dead 后, gateway 中央服务**物理上**不再有任何 archive 写读 API surface.
"中央 API 物理无能" — 真做到了, 不是承诺.
"""

from __future__ import annotations

from .archiver import prepare_tool_messages

__all__ = ["prepare_tool_messages"]
