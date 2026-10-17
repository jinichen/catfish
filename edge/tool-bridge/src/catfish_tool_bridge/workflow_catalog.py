"""工作流的能力积木目录 (docs/WORKFLOW-SKILL-PLAN.md §2.1)。

不是 89 个工具全放进来: 每个进目录的能力要定三件事 ——
  level   副作用级别: read 只读 / local 只动员工自己电脑上的东西 / outward 对外 (跑到它之前停下给员工看)
  group   界面上分组: 查 / 写 / 发
  outputs 结果里主要字段 (后面的步骤从这里取值; 界面下拉只列这些)

新能力上线时顺手决定进不进、算哪一级。教学 (trace_recorder) 也只录目录里的能力。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

READ, LOCAL, OUTWARD = "read", "local", "outward"


@dataclass(frozen=True)
class Action:
    name: str
    label: str
    level: str
    group: str
    params: tuple[str, ...]
    outputs: tuple[str, ...] = ()
    required: tuple[str, ...] = ()
    run: Callable[[dict[str, Any]], dict[str, Any]] | None = field(default=None, compare=False)


def _native(tool: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def call(args: dict[str, Any]) -> dict[str, Any]:
        from .catfish_tools import dispatch_native  # noqa: PLC0415
        return dispatch_native(tool, args)
    return call


def _form(action: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def call(args: dict[str, Any]) -> dict[str, Any]:
        from . import forms_tool  # noqa: PLC0415
        # 工作流里的对外步骤由运行器负责停下确认, 到这里已经确认过了
        return forms_tool.dispatch("catfish_form", {**args, "action": action, "confirmed": True})
    return call


def _wechat_push(args: dict[str, Any]) -> dict[str, Any]:
    from . import wechat_push  # noqa: PLC0415
    text = str(args.get("text") or "").strip()
    if not text:
        return {"ok": False, "error": "推送内容是空的"}
    if wechat_push.target() is None:
        return {"ok": False, "error": "还没绑定微信 (小鲶设置里先扫码绑定个人微信)"}
    ok = wechat_push.push(text[:2000])
    st = wechat_push.last_status()
    return {"ok": bool(ok), "error": None if ok else (st.get("error") or "推送失败")}


_A = Action
ACTIONS: dict[str, Action] = {a.name: a for a in [
    # ── 查 (只读) ──
    _A("email_search", "搜邮件", READ, "查", ("query", "folder", "account", "limit"),
       ("matches", "count"), ("query",), _native("catfish_email_search")),
    _A("email_read", "读一封邮件", READ, "查", ("email_id",), ("subject", "sender", "date", "body_text", "attachments"),
       ("email_id",), _native("catfish_email_read")),
    _A("email_attachment", "取邮件附件", READ, "查", ("email_id", "filename"), ("path", "filename"),
       ("email_id", "filename"), _native("catfish_email_attachment")),
    _A("wiki_search", "搜知识库", READ, "查", ("query", "top_k"), ("matches", "count"), ("query",),
       _native("catfish_wiki_search")),
    _A("wiki_read", "读知识库页面", READ, "查", ("rel_path",), ("content", "body"), ("rel_path",), _native("catfish_wiki_read")),
    _A("search_files", "搜本机文件", READ, "查", ("query", "limit", "file_types"), ("matches", "count"), ("query",),
       _native("catfish_search_files")),
    _A("search_docs", "搜文档内容", READ, "查", ("query", "top_k"), ("matches", "count"), ("query",),
       _native("catfish_search_docs")),
    _A("list_tasks", "看任务", READ, "查", ("scope", "include_completed", "list_name", "limit"), ("tasks", "count"), (),
       _native("catfish_list_tasks")),
    _A("list_reminders", "看提醒", READ, "查", ("scope", "include_completed", "list_name", "limit"), ("reminders", "count"),
       (), _native("catfish_list_reminders")),
    _A("list_calendars", "看日历", READ, "查", (), ("calendar_names", "count"), (), _native("catfish_list_calendars")),
    _A("wechat_search", "搜微信记录", READ, "查", ("query", "session_id", "start_time", "end_time", "limit"),
       ("items", "count"), ("query", "start_time", "end_time"), _native("catfish_wechat_search")),
    _A("wechat_history", "看微信聊天", READ, "查", ("session_id", "start_time", "end_time", "limit"), ("items", "count"),
       ("session_id", "start_time", "end_time"), _native("catfish_wechat_history")),
    _A("today_summary", "今天做了什么", READ, "查", (), ("summary",), (), _native("catfish_today_summary")),
    _A("list_my_outputs", "我最近生成的文件", READ, "查", ("hours_back", "limit", "ext_filter"), ("items", "count"), (),
       _native("catfish_list_my_outputs")),
    # ── 写 (本机) ──
    _A("create_task", "建任务", LOCAL, "写", ("title", "due_date_iso", "body", "priority", "list_name"), ("task",),
       ("title",), _native("catfish_create_task")),
    _A("create_reminder", "建提醒", LOCAL, "写", ("title", "body", "due_date_iso", "list_name", "priority"),
       ("summary",), ("title",), _native("catfish_create_reminder")),
    _A("create_calendar_event", "建日程", LOCAL, "写",
       ("title", "start_iso", "end_iso", "location", "description", "calendar_name", "alarm_minutes_before"),
       ("event_summary", "start_iso"), ("title", "start_iso"), _native("catfish_create_calendar_event")),
    _A("email_create_draft", "存邮件草稿 (不发)", LOCAL, "写", ("to", "subject", "body", "cc", "account"),
       ("draft_id",), ("to", "subject", "body"), _native("catfish_email_create_draft")),
    _A("wiki_create", "建知识库页面", LOCAL, "写", ("kind", "title", "body", "subtype", "tags"), ("rel_path",),
       ("kind", "title", "body", "subtype"), _native("catfish_wiki_create")),
    _A("wiki_update", "改知识库页面", LOCAL, "写", ("rel_path", "content"), ("rel_path",), ("rel_path", "content"),
       _native("catfish_wiki_update")),
    _A("form_create", "建一张收集表", LOCAL, "写", ("title", "fields", "deadline", "note", "recipients", "template_path"),
       ("form",), ("title", "fields"), _form("create")),
    _A("form_export", "导出收集表汇总", LOCAL, "写", ("form_id",), ("path", "rows"), ("form_id",), _form("export")),
    # ── 发 (对外, 跑到之前停下给员工看) ──
    _A("form_send", "发收集表给同事", OUTWARD, "发", ("form_id",), ("sent", "failed"), ("form_id",), _form("send")),
    _A("form_remind", "催同事填表", OUTWARD, "发", ("form_id", "text"), ("reminded",), ("form_id",), _form("remind")),
    _A("form_close", "结束收集", OUTWARD, "发", ("form_id",), (), ("form_id",), _form("close")),
    _A("wiki_publish", "发布到部门知识库", OUTWARD, "发", ("wiki_rel_path",), ("file_id",), ("wiki_rel_path",),
       _native("catfish_wiki_publish")),
    _A("wechat_push", "推送到我的微信", OUTWARD, "发", ("text",), (), ("text",), _wechat_push),
]}

#: 教学时要录的原生工具名 (catfish_ 前缀) → 积木名。trace_recorder 用。
NATIVE_TO_ACTION: dict[str, str] = {
    f"catfish_{a.name}": a.name for a in ACTIONS.values() if not a.name.startswith(("form_", "wechat_push"))
}


def get(name: str) -> Action | None:
    return ACTIONS.get(name)


def describe() -> list[dict[str, Any]]:
    """给小鲶 / 界面看的目录。"""
    return [{"name": a.name, "label": a.label, "level": a.level, "group": a.group, "params": list(a.params),
             "required": list(a.required), "outputs": list(a.outputs)} for a in ACTIONS.values()]
