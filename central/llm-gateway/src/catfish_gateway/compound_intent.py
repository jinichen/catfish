"""BL-COMPOUND-PLAN-EXECUTE (5/15 鸿波 '复合任务 agent 撑不住'):
复合任务检测 + plan-execute prompt 块注入.

# 问题

员工说"分析两份 CSV, 然后用归藏生成 PPT" — agent 一轮输出 935 token 思考
+ markdown code block, 但**不真发 tool_call** finish_reason=stop. 多复合任
务全卡这步.

Qwen 122B 在 ReAct (Reasoning + Acting) 上的失败模式: thought 写太长, 写完
think 该 emit Action 时 stop. 单纯调大 max_tokens / 改 prompt 措辞不够.

# 解决方案 (prompt-only, 不动 tool_choice)

1. 检测**复合任务**: user message 含连接词 ("并/和/然后/接着/再/最后") +
   ≥ 2 个动作动词 (分析/统计/生成/创建/写/做/调/读) 或 skill 意图 + 副任务
2. 注入 plan-execute prompt:
   - 第一轮: 输出 plan JSON 列 N 步骤, **不调 tool**, stop
   - 后续轮: 看 plan, 执行下一个未完成 step, **每轮只发 1 个 tool_call**
3. gateway 不解析 plan / 不存 state, agent 看历史 assistant content 自己推断
   当前 step. 失败也只是退化成正常 ReAct, 不撞 400.

# 跟 skill_guard 的关系

skill_guard:        触发铁律强调用 catfish_run_skill (单 skill 意图)
compound_intent:    触发分步 plan-execute (多步任务, 含 skill 或不含)
                    两者可同时注入 — skill_guard 解决"用哪个 skill", 复合
                    解决"先做啥再做啥".
"""

from __future__ import annotations

import logging
import re
from copy import deepcopy
from typing import Any

logger = logging.getLogger("catfish.gateway.compound_intent")


# ─── 复合任务检测 ───────────────────────────────────────────


#: 复合连接词 — 表示员工要执行 ≥ 2 步.
#: 中文为主, 英文兜底.
_CONNECTORS = re.compile(
    r"(?:并(?:且|且后)?|和|然后|接着|再|最后|之后|完成后|先.{1,15}然后|先.{1,15}后|先.{1,15}再"
    r"|分两步|分.{1,3}步|两步|多步|拆分|拆开"
    r"|then\b|and then\b|after that\b)",
    re.IGNORECASE,
)

#: 动作动词 — 命中表示这是一个"要做事"的子任务.
#: 不含太泛的 "用/做/搞", 避免误判.
#: 单字 "读" "写" 后跟名词 (空格 / 文件类型) 也认 — "读 CSV" "写 HTML" 是典型表达.
_ACTION_VERBS = re.compile(
    r"(?:分析|统计|计算|汇总|清洗|处理|抽取|提取|读取|查询|检索"
    r"|生成|创建|写入|写出|写一份|写一个|输出|制作|做出|出一份|起草|起一份"
    r"|发送|提交|上报|呈报"
    r"|登录|签到|签退|打卡"
    r"|读\s+[A-Za-z一-鿿]|写\s+[A-Za-z一-鿿]"  # "读 CSV" / "写 HTML"
    r"|analyze|compute|generate|create|write|read|extract|fetch|process)",
    re.IGNORECASE,
)


def has_compound_intent(messages: list[dict[str, Any]]) -> bool:
    """检测最近 user message 是否是复合任务.

    判定: 连接词命中 + 动作动词 ≥ 2.
    单步任务 (例: '做一份 PPT') 没连接词, 不触发.
    """
    user_text = _last_user_text(messages)
    if not user_text:
        return False

    if not _CONNECTORS.search(user_text):
        return False

    verb_count = len(_ACTION_VERBS.findall(user_text))
    return verb_count >= 2


# ─── plan-execute prompt 块 ──────────────────────────────


_PLAN_EXECUTE_MARKER = "## 复合任务 — 分步执行 (gateway plan-execute 注入)"


_PLAN_EXECUTE_BLOCK = """

## 复合任务 — 分步执行 (gateway plan-execute 注入)

员工请求是多步任务 (含连接词 + ≥ 2 个动作动词). 你**必须**按 plan-execute 模式干, 不要"想很多然后 stop".

### 第一轮 (history 里没看到你之前输出的 plan)

1. **只输出 plan, 不调任何 tool**.
2. plan 格式 — 一个 json code block (避免 LLM 自由发挥写散文):

```json
{
  "plan": [
    {"step": 1, "action": "<tool 名>", "what": "<这一步要干啥>"},
    {"step": 2, "action": "<tool 名>", "what": "<这一步要干啥>"},
    ...
  ]
}
```

3. plan 列完后 stop, **不要 thought**, 不要在 plan 后面追加 markdown code block 假装"我现在就执行".
4. 等下一轮 prompt 再 execute. 员工或客户端会发"继续执行 step 1".

### 后续轮 (history 里能看到你之前输出过 plan)

1. 看 plan 数组 + 历史 assistant.tool_calls, 找出**下一个未完成 step**.
2. **本轮只发 1 个 tool_call** 执行下一步, **绝对不要**:
   - 在文字里写 ```python ... ``` 假装是 tool_call
   - thought 超过 100 字
   - 同一轮里塞 ≥ 2 个 tool_call
3. 完成本轮 tool_call 自然 stop, 等下一轮再发下一步.

### 反 plan-execute 反模式 (踩过坑, 严禁)

- ❌ 第一轮就在 plan 里写代码或写 HTML
- ❌ 输出 plan 后追"我现在执行第 1 步" 又开始写代码 stop
- ❌ 同一轮 emit 多个 tool_call
- ❌ 不输出 plan 直接开干

### 完成后

最后一步 tool 跑完, 输出一句**确认信息**给员工 (例: "✅ 已生成 PPT: <path>, 双击在浏览器打开"). 不要总结整个 plan, 不要在结尾重写 plan json.

"""


def inject_compound_plan_execute(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """复合任务命中时, 在最后 system message 末尾追加 plan-execute 铁律.

    幂等. 不动 messages 顺序 (不在中间插 system, Qwen Go gRPC adapter 严格校验).
    """
    if not has_compound_intent(messages):
        return messages
    if not messages:
        return messages

    # 找最后 system 段 (跟 skill_guard 同套路)
    last_system_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "system":
            last_system_idx = i
            break
    if last_system_idx < 0:
        return messages

    sys_msg = messages[last_system_idx]
    cur = sys_msg.get("content", "")
    if isinstance(cur, str) and _PLAN_EXECUTE_MARKER in cur:
        # 幂等
        return messages

    out = deepcopy(messages)
    sys_msg = out[last_system_idx]
    if isinstance(sys_msg.get("content"), str):
        sys_msg["content"] = sys_msg["content"].rstrip() + _PLAN_EXECUTE_BLOCK
        logger.info("compound_intent: 注入 plan-execute 铁律")
    return out


# ─── helpers ─────────────────────────────────────────────


def _last_user_text(messages: list[dict[str, Any]]) -> str:
    """取最后一条 role=user 的 text content. multimodal 拼合 text parts."""
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict)
            )
        if isinstance(content, str):
            return content
        return ""
    return ""


__all__ = [
    "has_compound_intent",
    "inject_compound_plan_execute",
]
