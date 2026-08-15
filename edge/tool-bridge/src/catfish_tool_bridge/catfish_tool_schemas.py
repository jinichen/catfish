"""Catfish 原生 tools schemas — 从 catfish_tools.py 抽出 (5/20 拆分超 6400 行规则).

50 个 tool 的 JSON Schema (name / description / parameters), 给 LLM 看的描述.
实现在 catfish_tools.py 里 (lazy import 各子 module).

拆分历史:
- 5/20 之前: 全在 catfish_tools.py (6454 行, 严重超 check_file_sizes.sh 800 红线)
- 5/20 末第一刀: 抽 2088 行 schemas 到本文件. catfish_tools.py 减到 ~4400 行
- 后续 sprint: 按 category 拆 impl (browser / skill / today / propose ...)

红线: 加新 tool 时, schema 加这里, impl 加 catfish_tools.py 或对应子 module,
dispatch 加 catfish_tools._dispatch_native_inner. **dispatch 走原文件, 不重组.**
"""
from __future__ import annotations

from typing import Any, Dict, List


from .catfish_tool_schemas_memory import MEMORY_TOOLS
from .catfish_tool_schemas_browser import BROWSER_TOOLS
from .catfish_tool_schemas_skill import SKILL_TOOLS
from .catfish_tool_schemas_wiki import WIKI_TOOLS
from .catfish_tool_schemas_task import TASK_TOOLS
from .catfish_tool_schemas_search import SEARCH_TOOLS
from .catfish_tool_schemas_email import EMAIL_TOOLS
from .catfish_tool_schemas_expert import EXPERT_TOOLS

#: 78 个原生 tool 的 schema, 按类别拆在 catfish_tool_schemas_*.py 里, 这里拼回。
#:
#: 8/15 拆分: 原来是一整个 3362 行的列表字面量, 越过 800 红线 4 倍。
#: 消费方只用 len() 和 t["name"] / 成员判定 (adapter.py:198 `list(...)`,
#: server.py:564 计数, catfish_tools.py:389 建 NATIVE_TOOL_NAMES 集合),
#: 全库无下标访问, 所以拼接顺序只影响 LLM 看到的排列, 不影响任何判定。
#: 顺序按类别排, 比原来的历史堆叠顺序更好读。
CATFISH_NATIVE_TOOLS: List[Dict[str, Any]] = (
    MEMORY_TOOLS + BROWSER_TOOLS + SKILL_TOOLS + WIKI_TOOLS + TASK_TOOLS + SEARCH_TOOLS + EMAIL_TOOLS + EXPERT_TOOLS
)
