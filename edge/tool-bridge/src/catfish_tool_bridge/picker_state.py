"""P3.5.29 Phase 8 (6/17 鸿波) — tool-bridge 真 picker_state 读 helper.

为啥: 3 个 tool-bridge LLM 调 (catfish_tools._expertise_llm_call,
install_and_ops._llm_dedupe_judge, recmode/aggregator.call_llm) 也是 为员工
服务 真 LLM 调, 应跟 chat picker. 模型默认 真 中央 roles.yaml 真
chat_default 控制 (P3.5.29 框架), picker 员工临时切覆盖, 合理 chain:

  picker_state.json > role_resolver("chat_default") > "catfish-private-main" 兜底

# 真 picker_state.json 真写**

Companion `src/store/chat.ts` setModel 每次员工切 picker 时fire-and-forget
写 ~/.catfish/picker_state.json (P3.5.2 6/16 鸿波 ship):

```json
{
  "chat_model": "catfish-private-main"
}
```

# 真复用 hermes-memory 同 logic**

hermes-memory `catfish_memory_helpers._read_picker_state_model(catfish_home)` 已
ship P3.5.2. tool-bridge 真单独 process 不能 import hermes plugin**, 抄
逻辑 进 独立 module.

# fail-silent

文件不存在 / parse 错 / chat_model 字段缺 → 返 None. caller 走 fallback
(role_resolver / 兜底). 不阻塞 LLM 调.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _catfish_home() -> Path:
    """`~/.catfish/` 或 env CATFISH_HOME 指定.

    对齐 hermes-memory `_catfish_home()` 真默认 path** + env var.
    """
    env = os.environ.get("CATFISH_HOME")
    return Path(env).expanduser() if env else Path.home() / ".catfish"


def read_picker_model() -> Optional[str]:
    """读 ~/.catfish/picker_state.json chat_model.

    Returns:
        picker model 字符串 或 None (文件不存在 / parse 错 / 字段缺 / 空字符串).

    caller chain (推荐):

    ```python
    from . import picker_state, role_resolver
    model = (
        picker_state.read_picker_model()
        or role_resolver.resolve("chat_default")
        or "catfish-private-main"
    )
    ```
    """
    path = _catfish_home() / "picker_state.json"
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        if not isinstance(data, dict):
            return None
        model = data.get("chat_model", "")
        if isinstance(model, str) and model.strip():
            return model.strip()
        return None
    except (OSError, ValueError, json.JSONDecodeError) as e:
        logger.debug(
            "tool-bridge picker_state.json 读失败 (fallback role/兜底): %s", e,
        )
        return None
