"""对话卡片 (A2UI) —— 照 A2UI 上游 eval 的 `subagent_tool` 策略做 (11/15)。

# 为什么是这个结构

上游 (github.com/a2ui-project/a2ui) 的生产做法有三种: 主模型直接写 JSON / 写 Express / 交给专员 (subagent_tool)。
鲶鱼选第三种 (eval/a2ui_eval/strategies/subagent_tool.py):

- 主小鲶调 `catfish_form` 的 `card` 动作 (员工要选 / 填 / 勾 / 定时间 / 看一眼改了再办时自己决定), request 写清要什么。
  主对话每轮的提示词只多这一句说明, 不用每轮塞一大段 A2UI 规则。
  为什么不单独开一个工具: 网关常驻工具已经 49 个 (tools_sanitizer_constants.DEFAULT_MAX_TOOLS), Qwen 50+ 个工具
  实测返空 400, 注释里写着「再加先想想能不能合并」—— 「让员工本人填一张卡」跟「一起填表」是一家, 合进去。
- 这里是「专员」: 用官方 SDK (a2ui-agent-sdk) 按组件目录生成 Express 提示词, 再调一次模型写卡片;
  SDK 解析校验 (组件 / 属性 / 引用的子组件 / 整棵树), 不过就把报错发回去让它改 (上游 `_retry_message`),
  最多 2 次 (上游 restaurant_finder 示例也是试 2 次)。员工看不到失败的那次。
- 卡片存本机 `~/.catfish/cards/<编号>.json`, 只把编号还给主小鲶 (上游 "saved out-of-band"):
  Companion 按编号读出来画。主小鲶的上下文里不出现几 KB 的 JSON, 也碰不到 tool-bridge 4KB 归档。

为什么用 Express 不用 Direct JSON: 同一份裁剪过的目录, Express 提示词约 1.6 万字、Direct JSON 约 3.3 万字;
Express 嵌套组件不用自己编 id, 引用不到的子组件编译时就报 (ExpressUndefinedChildError), 11/15 直接写 JSON
时模型漏过整行, 那种错到不了员工面前。

员工点卡片按钮 → Companion 把上游标准的 `action` 消息拼进一句话发回会话 → 主小鲶按出卡时的目的接着办。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger("catfish-tool-bridge")

from .a2ui_catalog import CATALOG_ID, CATALOG_PATH, FUNCTIONS as CARD_FUNCTIONS, component_names  # noqa: E402

#: 卡片能用的组件 (鲶鱼目录: 基础组件 12 个 + 邮件卡 / 日程卡 / 待办项 / 文件卡) —— 跟 companion-app/src/lib/a2uiCard.ts
#: CARD_COMPONENTS 一致 (tests 对着查)。目录本身在 a2ui_catalog.py 生成。
CARD_COMPONENTS = component_names()
MAX_ATTEMPTS = 3
#: 一次请求的说明最多这么长 (主小鲶写的; 超长多半是把整封邮件塞进来了)
MAX_REQUEST_CHARS = 4000
#: 至少这么长才算说清了要什么 (11/15 评估里小鲶先发过一个「占位」, 专员照样画了张空洞的卡)
MIN_REQUEST_CHARS = 15
CARD_KEEP_DAYS = 90
SOURCE = "plugin:toolbridge-card"

_ROLE = (
    "你是鲶鱼 Companion 的界面专员: 把请求变成一张给员工本人在聊天里看、点、填的小卡片。\n"
    "卡片要短: 根是一个 Card, 里面一列 Column, 需要并排时用 Row。按钮按用途放:\n"
    "· 填写 / 确认类: 最后一个主按钮 (variant \"primary\");\n"
    "· 选择 / 审批类: 每个选项一个按钮 (如「同意」「驳回」「转办」), 各用自己的英文动作名, 主选项用 primary;\n"
    "· 勾选清单 / 待办: 每条一个 TodoItem (待办带截止日) 或 CheckBox, 最后一个按钮把勾选结果一起交回。\n"
    "按钮一点就把整张卡交回给小鲶 —— 只放「交」的按钮; 常用原因 / 预设值不要做成按钮, 用 ChoicePicker 或输入框的 placeholder。\n"
    "请求里给了邮件 / 日程 / 待办 / 本机文件, 就用 EmailCard / EventCard / TodoItem / FileCard 展示 (字段照请求原样)。\n"    "要员工从这些邮件 / 日程 / 文件里挑 (先回哪封、改哪个、发哪个), 卡片展示之后再加一个 ChoicePicker 把它们列成选项 (选项值用邮件编号 / 路径 / 英文键), 不然他没法选。\n"
    "界面上的字用简体中文。"
)
_ENV_NOTES = (
    "## Environment Notes\n"
    "- 你没有任何工具, 不要输出工具调用。只输出一个用 <a2ui> 和 </a2ui> 包起来的 A2UI Express 块, 块外不要写任何文字。\n"
    "- 预填值只能来自请求里给的; 请求没给的留空 (\"\" / [] / false), 不许编。\n"
    "- 可填的组件都绑到 $/f/ 下的数据路径, 按钮用 Event(\"请求里给的动作名\", {请求里给的键: $/f}) 把员工填的整份交回。\n"
    "- 单选 / 多选的选项用请求里给的; 请求没给选项就用 TextField。\n"
    # 11/15 真模型评估里最常见的四种被拒原因 (上游 eval 叫 generation_rules: 官方规则之外按场景补的几条)
    "- 数据路径和 Event 里的键名只用英文字母、数字、下划线 (如 $/f/video_link); 中文只出现在界面文字里。\n"
    "- Card 只放一个子组件 (写组件名, 不是列表); 多个就先放进一个 Column。\n"
    "- ChoicePicker 的 displayStyle 只能是 \"checkbox\" 或 \"chips\"; 不确定就不写。\n"
    "- DateTimeInput 不写 min / max。\n"
    # 不带提示的必填校验会显示英文默认的 "Required check failed", 而且员工还没动手就标红 —— 只给真必填的加, 带中文提示
    "- 只给请求里写明必填的字段加 ?required, 而且一定带中文提示, 如 ?required(\"请填写事由\")。"
    "只跟某一个按钮有关的字段 (如驳回原因) 不加 ?required —— 必填没填时卡上所有按钮都点不了。\n"
    # 11/15 第二轮评估: 可选属性按位置写会错位 (ChoicePicker 传了 8 个、Button 传了 5 个), 两次都一样被拒
    "- 必填属性按位置写; 可选属性一律用关键字写, 如 ChoicePicker(\"方向\", options=[...], value=$/f/direction)、"
    "Button(Text(\"同意\"), variant=\"primary\", action=Event(\"approve\", {f: $/f}))。\n"
    "- 同类条目超过 3 条 (一串待办 / 邮件 / 名单) 用 List(_template($/items, 行组件)), 条目数据放 $/items, 行组件里用 $字段 相对路径。\n"
    "- 打分 / 比例用 Slider(标签, 最小, 最大, $/f/字段)。\n"
    "- EmailCard / EventCard / FileCard 只用请求里给的邮件编号、日程、本机路径, 不许编; 请求没给就不用这些组件。"
)


@lru_cache(maxsize=1)
def _processor():
    """官方 SDK 的请求处理器 (Express 格式, 裁剪过的基础目录)。第一次用到才加载 SDK。"""
    from a2ui.inference_formats.express import ExpressFormatFactory  # noqa: PLC0415
    from a2ui.processor.catalog_config import CatalogConfig  # noqa: PLC0415
    from a2ui.processor.generator import A2uiGenerator  # noqa: PLC0415

    catalog = CatalogConfig.from_path(str(CATALOG_PATH))   # 鲶鱼目录本身已经是裁剪 + 自定义过的 (a2ui_catalog.py)
    catalog_id = catalog.transformed_catalog.catalog_id
    generator = A2uiGenerator([catalog], inference_format_factory=ExpressFormatFactory())
    return generator.create_processor({"1.0": {"supportedCatalogIds": [catalog_id]}})


def specialist_messages(request: str) -> list[dict[str, str]]:
    """专员这一轮的 messages: 角色 + 环境说明 + SDK 生成的 Express 规则 (上游 _specialist_system_prompt 的结构)。"""
    system = "\n\n".join([_ROLE, _ENV_NOTES, _processor().prompt_snippet])
    return [{"role": "system", "content": system}, {"role": "user", "content": request}]


def retry_message(error: Exception | str) -> str:
    """被拒的那次 → 让专员改 (上游 _retry_message)。"""
    return (f"你上一次的回复被拒了: {error}\n\n"
            "改好这个问题, 重新输出完整的、用 <a2ui> 和 </a2ui> 包起来的整张卡片。")


def compile_reply(text: str) -> list[dict[str, Any]]:
    """专员的回复 → 校验过的 A2UI v1.0 消息。没有卡片 / 编译不过 / 渲染端会拒 → ValueError (带原因)。"""
    try:
        parts = _processor().parse_response(text)
    except Exception as e:  # noqa: BLE001 —— SDK 的各种编译 / 校验错误, 原因原样给专员看
        raise ValueError(f"{type(e).__name__}: {e}") from e
    messages: list[dict[str, Any]] = []
    for part in parts:
        a2ui = getattr(part, "a2ui", None)
        if not a2ui:
            continue
        for m in a2ui:
            messages.append(m.model_dump(by_alias=True, exclude_none=True) if hasattr(m, "model_dump") else dict(m))
    if not messages:
        raise ValueError("回复里没有 <a2ui> 卡片块")
    return messages


LlmCall = Callable[[list[dict[str, str]]], Awaitable[str]]


async def generate(request: str, llm: LlmCall) -> tuple[list[dict[str, Any]], int]:
    """专员写卡片, 校验不过带着报错重写 (上游 _ask_specialist)。返回 (消息, 用了几次重试); 都不过 → ValueError。"""
    messages = specialist_messages(request)
    for attempt in range(MAX_ATTEMPTS):
        reply = await llm(messages)
        try:
            return compile_reply(reply), attempt
        except ValueError as e:
            logger.info("catfish_card: 第 %d 次没过校验: %s", attempt + 1, e)
            if attempt + 1 >= MAX_ATTEMPTS:
                raise
            messages = [*messages, {"role": "assistant", "content": reply},
                        {"role": "user", "content": retry_message(e)}]
    raise ValueError("没有生成卡片")  # pragma: no cover —— 上面的循环总会 return 或 raise


# ── 存卡片 (Companion 按编号读, commands/a2ui_card.rs) ───────────────────────

def cards_dir() -> Path:
    home = os.environ.get("CATFISH_HOME", "").strip()
    return (Path(home) if home else Path.home() / ".catfish") / "cards"


def save_card(messages: list[dict[str, Any]], request: str, *, now: float | None = None) -> str:
    """存一张卡, 返回编号。顺手清掉 CARD_KEEP_DAYS 天前的 (会话里的老卡片就画不出来了, 显示「卡片已过期」)。"""
    now = now or time.time()
    d = cards_dir()
    d.mkdir(parents=True, exist_ok=True)
    card_id = f"c_{datetime.fromtimestamp(now).strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}"
    body = {"id": card_id, "created_at": datetime.fromtimestamp(now).isoformat(timespec="seconds"),
            "request": request, "messages": messages}
    tmp = d / f".{card_id}.tmp"
    tmp.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    tmp.replace(d / f"{card_id}.json")
    cutoff = now - CARD_KEEP_DAYS * 86400
    for old in d.glob("c_*.json"):
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            pass
    return card_id


# ── 更新已经出过的卡片 (上游「画布」用法: 同一张卡跨轮次推数据, 11/15) ──────────────

#: 卡片编号格式 (save_card 生成的) —— 拼路径前必须对得上
CARD_ID_RE = __import__("re").compile(r"^c_\d{8}_\d{6}_[0-9a-f]{6}$")
MAX_UPDATE_PATHS = 50


def load_card(card_id: str) -> dict[str, Any]:
    if not CARD_ID_RE.match(card_id or ""):
        raise ValueError(f"卡片编号不对: {card_id!r}")
    path = cards_dir() / f"{card_id}.json"
    if not path.is_file():
        raise ValueError(f"找不到卡片 {card_id} (过期清掉了, 或不是这台电脑出的)")
    return json.loads(path.read_text(encoding="utf-8"))


def update_card(args: dict[str, Any]) -> dict[str, Any]:
    """catfish_form action=card_update: 改一张已经出过的卡片里的数据 (进度、状态、预填值)。

    不经过专员、不调模型 —— 上游的智能体就是直接推 updateDataModel; 主小鲶给 {数据路径: 新值},
    这里拼成标准消息, 用官方 SDK 在这张卡已有的状态上校验 (路径 / 类型不对就拒), 追加进卡片文件, revision +1。
    Companion 看到这次调用, 把新消息打到对话里原来那张卡上 (tabs/Chat/ToolCards.tsx), 卡片原地变。"""
    card_id = str(args.get("card_id") or "").strip()
    data = args.get("data")
    if not isinstance(data, dict) or not data:
        return {"ok": False, "error": "data 必填: {数据路径: 新值}, 如 {\"/progress/done\": 3}"}
    if len(data) > MAX_UPDATE_PATHS:
        return {"ok": False, "error": f"一次最多改 {MAX_UPDATE_PATHS} 个数据路径"}
    try:
        body = load_card(card_id)
        base = list(body.get("messages") or [])
        surface = next((m.get("createSurface", {}).get("surfaceId") for m in base if "createSurface" in m), None)
        if not surface:
            raise ValueError("这张卡片里没有 createSurface")
        updates = []
        for raw_path, value in data.items():
            path = str(raw_path).strip()
            if not path.startswith("/"):
                raise ValueError(f"数据路径要以 / 开头: {path!r}")
            updates.append({"version": "1.0", "updateDataModel": {"surfaceId": surface, "path": path, "value": value}})
        state = _processor().create_state()
        state.apply(base)
        state.apply(updates)
    except ValueError as e:
        return {"ok": False, "error": f"卡片没改成: {e}"}
    except Exception as e:  # noqa: BLE001 —— SDK 校验不过 (路径 / 类型不对)
        return {"ok": False, "error": f"卡片没改成 ({type(e).__name__}: {e})"}
    body["messages"] = base + updates
    body["revision"] = int(body.get("revision") or 0) + 1
    path = cards_dir() / f"{card_id}.json"
    tmp = path.with_name(f".{card_id}.tmp")
    tmp.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    logger.info("catfish_form card_update: %s → 第 %d 版 (%d 个路径)", card_id, body["revision"], len(updates))
    return {"ok": True, "card_id": card_id, "revision": body["revision"],
            "note": "对话里那张卡片已经更新了, 不用再出一张新的, 回复里也不用复述。"}


# ── 工具入口 ─────────────────────────────────────────────────────────────

def _gateway_llm() -> LlmCall:
    """专员用的模型调用: 跟验证码识别同一套 (本机登录令牌 + 本机网关), 关深度思考 (gateway_llm 默认带 X-Catfish-Thinking: off)。"""
    from .gateway_llm import call_llm  # noqa: PLC0415
    from .recognize_captcha import GATEWAY_URL, _read_id_token  # noqa: PLC0415
    token = _read_id_token()
    if not token:
        raise ValueError("没登录 (拿不到访问令牌), 登录后再出卡片")

    async def call(messages: list[dict[str, str]]) -> str:
        return await call_llm(messages, source=SOURCE, gateway_url=GATEWAY_URL, auth_token=token,
                              temperature=0.2, max_tokens=3000, timeout_s=90.0)
    return call


def make_card(args: dict[str, Any], *, llm: LlmCall | None = None) -> dict[str, Any]:
    """catfish_form action=card 的实现 (forms_tool.dispatch 转过来)。"""
    request = str(args.get("request") or "").strip()
    if not request:
        return {"ok": False, "error": "request 必填: 写清要员工做什么、有哪些字段和选项、预填值、按钮的动作名"}
    if len(request) < MIN_REQUEST_CHARS:
        return {"ok": False, "error": "request 太短, 没说清要什么: 写清要员工做什么、有哪些字段和选项、预填值、按钮的动作名"}
    if len(request) > MAX_REQUEST_CHARS:
        return {"ok": False, "error": f"request 太长 ({len(request)} 字, 上限 {MAX_REQUEST_CHARS}), 只写卡片要的字段和预填值"}
    try:
        messages, retries = asyncio.run(generate(request, llm or _gateway_llm()))
    except ValueError as e:
        return {"ok": False, "error": f"卡片没生成出来 ({e})。改用文字问员工。"}
    except Exception as e:  # noqa: BLE001 —— 网关不可达 / 超时 / SDK 没装
        if isinstance(e, ModuleNotFoundError):
            import sys  # noqa: PLC0415
            logger.warning("catfish_form card: 缺 %s (python=%s pid=%d site=%s)", e.name, sys.executable, os.getpid(),
                           [p for p in sys.path if "site-packages" in p])
        logger.warning("catfish_form card 失败: %s", e)
        return {"ok": False, "error": f"卡片没生成出来 ({type(e).__name__}: {e})。改用文字问员工。"}
    card_id = save_card(messages, request)
    logger.info("catfish_form card: %s (重试 %d 次)", card_id, retries)
    return {"ok": True, "card_id": card_id,
            "note": "卡片已经显示在员工的对话里。你的回复只用一句话说要他填什么, 不要复述卡片内容、不要写卡片代码。"}
