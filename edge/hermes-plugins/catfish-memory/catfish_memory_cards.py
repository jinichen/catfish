"""对话卡片 (A2UI) 的规范: 只在鲶鱼电脑端的对话里告诉小鲶「这里可以出卡片、怎么写」(11/15)。

# 为什么放这里

卡片是通用能力, 不是某个场景的模板: 员工要选、填、勾、定时间、确认改一下再办时, 小鲶自己判断要不要出卡、
现场用白名单组件拼。所以规范要每轮都在 (跟「此刻」块一样走 prefetch), 而不是装成一个技能等模型想起来去读。

# 只给电脑端

Companion 的对话 (渠道 api_server) 才画得出卡片。微信、定时任务、子任务都不给 —— 不知道有卡片, 就不会出。
微信那边插件还兜了一层 (catfish-xcatfish-user/plugin_weixin_zh._strip_a2ui_cards)。

渠道以 hermes 初始化记忆插件时给的 platform / agent_context 为准: 新会话第一轮 prefetch 时, 会话那一行还没写进
state.db (hermes turn_context: 先 prefetch 后 _persist_turn_start), 按会话号查不到。老版本 hermes 不给 platform 时
才回落到查 state.db; 都查不到就不给 (宁可少一张卡, 不往微信发 JSON)。

# 跟 Companion 一致

组件白名单、目录 id、提交 / 重出那两句话的开头, 跟 companion-app/src/lib/a2uiCard.ts 必须一致
(tests/test_cards_guide.py 对着那个文件查)。

评估时设 CATFISH_CARDS_FORCE=1: 命令行发起的会话 (hermes chat --source tool, 渠道 cli) 也给, 好不经 Companion 测模型;
微信 / 定时任务 / 子任务照样不给 —— 网关带着这个开关跑评估时, 员工的微信和定时任务不受影响。
"""
from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger("catfish-memory")

CARD_SOURCES = ("api_server", "companion")
CARD_COMPONENTS = ("Text", "Row", "Column", "Card", "Divider",
                   "Button", "TextField", "CheckBox", "ChoicePicker", "DateTimeInput")
CATALOG_ID = "catfish:cards/v1"
SUBMIT_HEAD = "（填好了卡片「"
RETRY_HEAD = "（卡片没画出来）"

_EXAMPLE = '[{"version":"1.0","createSurface":{"surfaceId":"meet","catalogId":"catfish:cards/v1"}},{"version":"1.0","updateComponents":{"surfaceId":"meet","components":[{"id":"root","component":"Card","child":"body"},{"id":"body","component":"Column","children":["title","slot","agenda","notify","go"]},{"id":"title","component":"Text","text":"**约周例会** · 选个时间"},{"id":"slot","component":"ChoicePicker","label":"时间","options":[{"label":"周二 10:00","value":"2026-11-17T10:00"},{"label":"周三 14:00","value":"2026-11-18T14:00"}],"value":{"@path":"/f/时间"},"displayStyle":"chips"},{"id":"agenda","component":"TextField","label":"议程 (一句话)","value":{"@path":"/f/议程"},"variant":"longText"},{"id":"notify","component":"CheckBox","label":"同时发邀请给参会人","value":{"@path":"/f/发邀请"}},{"id":"go","component":"Button","variant":"primary","child":"go_l","action":{"event":{"name":"book_meeting","context":{"meeting":{"@path":"/f"}}}}},{"id":"go_l","component":"Text","text":"就这样定"}]}},{"version":"1.0","updateDataModel":{"surfaceId":"meet","path":"/f","value":{"时间":[],"议程":"","发邀请":true}}}]'

GUIDE = f"""## 对话卡片 (只在鲶鱼电脑端对话里能用)

员工要**从几个里选、填几项、勾几个、定个时间、看一眼改一改再办**时, 可以在回复里出一张卡片, 员工点着填, 不用打字。
- 只是回答问题 / 一句话能问清 / 只有一个「要不要」→ 不出卡, 直接说。
- 一次最多一张; 卡片前先用一句话说明要员工做什么。
- 预填的值只能来自员工说过的、你查到的; 不知道就留空, **不许编**。
- 发邮件、删除、改日程这类动作: 卡片只收集员工的决定, 交回来后照常走审批, 不算已经同意。

### 格式
一个 ```a2ui 代码块, 内容是 JSON 数组, 三条消息:
1. {{"version":"1.0","createSurface":{{"surfaceId":"<英文短名>","catalogId":"{CATALOG_ID}"}}}}
2. {{"version":"1.0","updateComponents":{{"surfaceId":同上,"components":[...]}}}} —— 扁平列表, 每个组件一个唯一 id, 必须有 id 为 "root" 的; 容器用 child / children 写子组件的 id, **写到的 id 都要在列表里** (少一个整张卡画不出来)。
3. {{"version":"1.0","updateDataModel":{{"surfaceId":同上,"path":"/f","value":{{...预填值...}}}}}} —— 可填的组件 value 写 {{"@path":"/f/字段名"}}

### 能用的组件 (只有这些, 别的画不出来)
- Card {{child}} · Column {{children}} · Row {{children}} · Divider {{}}
- Text {{text, variant?: "body"|"caption"}} —— text 里可以用 **粗体**
- TextField {{label, value, variant?: "shortText"|"longText", placeholder?}} —— 数字也用 shortText (员工会写「3.2万」)
- ChoicePicker {{label, options:[{{label,value}}], value (数组), variant?: "mutuallyExclusive" (单选, 默认) | "multipleSelection", displayStyle?: "chips"}}
- CheckBox {{label, value (true/false)}}
- DateTimeInput {{label, value ("2026-11-16T15:00"), enableDate?, enableTime?}}
- Button {{child: 一个 Text 的 id, variant?: "primary", action: {{"event": {{"name": "<英文动作名>", "context": {{"<键>": {{"@path": "/f"}}}}}}}}}}
- 并排的组件可加 "weight": 1 分宽度。

### 例子 (挑会议时间、补一句议程)
```a2ui
{_EXAMPLE}
```

### 交回来之后
员工点按钮后你会收到以「{SUBMIT_HEAD}…」）」开头的消息, ```a2ui-action 里 name 是你定的动作名, context 是员工填好的内容。按出卡时的目的接着办, 员工改过的以改过的为准。
卡片画不出来时会收到「{RETRY_HEAD}原因…」: 按原因修好重出一次; 还不行就改用文字问。
"""

_SOURCE_CACHE: Dict[tuple, Optional[str]] = {}


def _session_source(session_id: str, hermes_home: Optional[Path]) -> Optional[str]:
    """state.db 里这个会话的来源 (api_server / weixin / cron …); 查不到 None。查到的才缓存。"""
    if not session_id or hermes_home is None:
        return None
    db = Path(hermes_home) / "state.db"
    ck = (str(db), session_id)
    if ck in _SOURCE_CACHE:
        return _SOURCE_CACHE[ck]
    if not db.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        try:
            row = conn.execute("SELECT source FROM sessions WHERE id = ?", (session_id,)).fetchone()
        finally:
            conn.close()
    except sqlite3.Error as e:
        logger.debug("catfish-memory 卡片规范: 查会话来源失败 (不给规范): %s", e)
        return None
    src = row[0] if row and row[0] else None
    if src is not None:
        _SOURCE_CACHE[ck] = src
    return src


#: 评估用 (CATFISH_CARDS_FORCE=1): 只放开命令行发起的会话 (hermes chat --source tool), 微信 / 定时任务照样不给
_EVAL_SOURCES = ("cli", "tool", "oneshot")


def render_cards_guide(session_id: str, hermes_home: Optional[Path], *,
                       platform: Optional[str] = None, agent_context: Optional[str] = None) -> str:
    """电脑端主对话 → 卡片规范; 其他 (微信 / 定时任务 / 子任务 / 查不到) → 空。"""
    if agent_context and agent_context != "primary":
        return ""
    source = platform or _session_source(session_id, hermes_home)
    if source in CARD_SOURCES:
        return GUIDE
    if os.environ.get("CATFISH_CARDS_FORCE") == "1" and source in _EVAL_SOURCES:
        return GUIDE
    return ""
