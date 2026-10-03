"""会议纪要生成 (10/1, docs/MEETING-MINUTES-PLAN.md §3).

输入在员工本机的会议目录里 (Companion 写的):
    meta.json        标题 / 日期 / 参会人数
    transcript.json  转写 (meeting_asr.py 写): segments[{spk, start, end, text}]
    speakers.json    说话人改名 {"0": "张三", ...} (可选, 员工在界面里改)

输出写回同一个目录:
    minutes.json     结构化纪要 (摘要 / 决议 / 待办 / 待定问题)
    minutes.md       给人看的版本

数据边界 (CENTRAL-EDGE-DATA-BOUNDARY): 音频和文件都不出本机; 只有转写**文字**经网关
去大模型 —— 跟聊天同一条路, 网关不落盘。

长会议: 转写超过 CHUNK_CHARS 就分段各出一份要点 (map), 再合成一份纪要 (reduce)。
一小时会议约两万字, 一次塞进去容易超上下文、而且中间部分容易被"读漏"。
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.tool_bridge.meeting_minutes")

CHUNK_CHARS = 12000
SOURCE = "plugin:toolbridge-meeting"

SCHEMA_HINT = """严格输出一个 JSON 对象, 不要任何前后文字:
{
  "summary": "3-6 句话的会议摘要",
  "decisions": ["会上明确做出的决定, 每条一句"],
  "action_items": [{"owner": "负责人 (转写里能对上的人名或说话人标签, 不确定写空串)",
                    "task": "要做的事, 动词开头",
                    "due": "YYYY-MM-DD 或空串 (只在会上明确说了时间时填, 相对日期按会议日期换算)"}],
  "open_questions": ["没定下来、需要后续确认的问题"]
}
没有的项给空数组。不要编造转写里没有的内容。"""


def load_inputs(meeting_dir: Path) -> tuple[dict, dict, dict[str, str]]:
    meta = json.loads((meeting_dir / "meta.json").read_text(encoding="utf-8"))
    tp = meeting_dir / "transcript.json"
    if not tp.exists():
        raise ValueError("这场会议还没转写, 先转写再生成纪要")
    transcript = json.loads(tp.read_text(encoding="utf-8"))
    sp = meeting_dir / "speakers.json"
    names = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {}
    return meta, transcript, {str(k): str(v) for k, v in names.items() if str(v).strip()}


def speaker_label(spk: int, names: dict[str, str]) -> str:
    return names.get(str(spk)) or f"说话人{spk + 1}"


def _ts(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60:02d}:{sec % 60:02d}"


def transcript_lines(transcript: dict, names: dict[str, str]) -> list[str]:
    """同一个人连续说的几句并成一行, 省 token 也更好读。"""
    lines: list[str] = []
    cur_spk, cur_start, buf = None, 0.0, []
    for s in transcript.get("segments") or []:
        if s["spk"] != cur_spk and buf:
            lines.append(f"[{_ts(cur_start)}] {speaker_label(cur_spk, names)}: {''.join(buf)}")
            buf = []
        if not buf:
            cur_spk, cur_start = s["spk"], float(s.get("start", 0))
        buf.append(s["text"])
    if buf:
        lines.append(f"[{_ts(cur_start)}] {speaker_label(cur_spk, names)}: {''.join(buf)}")
    return lines


def chunk_lines(lines: list[str], limit: int = CHUNK_CHARS) -> list[list[str]]:
    chunks: list[list[str]] = [[]]
    size = 0
    for line in lines:
        if size + len(line) > limit and chunks[-1]:
            chunks.append([])
            size = 0
        chunks[-1].append(line)
        size += len(line)
    return [c for c in chunks if c]


def meeting_date(meta: dict) -> str:
    return (meta.get("created_at") or "")[:10] or date.today().isoformat()


def build_messages(meta: dict, body: str, *, partial: bool, names: dict[str, str]) -> list[dict]:
    who = "、".join(sorted(set(names.values()))) or "未命名 (用说话人编号指代)"
    sys_prompt = (
        "你是会议记录员, 根据会议转写写纪要。转写由语音识别生成, 可能有错字和断句问题, "
        "按上下文理解, 但不要添加转写里没有的事实。\n" + SCHEMA_HINT
    )
    head = (
        f"会议: {meta.get('title', '')}\n日期: {meeting_date(meta)}\n"
        f"参会人数: {meta.get('attendees', '')}\n已知参会人: {who}\n"
    )
    task = "下面是会议的**一部分**转写, 只总结这一部分:\n" if partial else "下面是会议转写:\n"
    return [{"role": "system", "content": sys_prompt}, {"role": "user", "content": head + task + body}]


def build_reduce_messages(meta: dict, partials: list[dict]) -> list[dict]:
    sys_prompt = (
        "你是会议记录员。下面是同一场会议按时间顺序分段整理的要点, 合并成一份完整纪要: "
        "去重, 前后矛盾时以后面的为准, 待办合并同一件事。\n" + SCHEMA_HINT
    )
    body = "\n\n".join(f"### 第 {i + 1} 段\n{json.dumps(p, ensure_ascii=False)}" for i, p in enumerate(partials))
    head = f"会议: {meta.get('title', '')}\n日期: {meeting_date(meta)}\n"
    return [{"role": "system", "content": sys_prompt}, {"role": "user", "content": head + body}]


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_minutes(raw: str) -> dict:
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.DOTALL)
    text = m.group(1) if m else raw[raw.find("{"): raw.rfind("}") + 1]
    try:
        data = json.loads(text)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"大模型没按格式输出纪要: {raw[:200]!r}") from e

    def strs(v: Any) -> list[str]:
        return [str(x).strip() for x in (v or []) if str(x).strip()] if isinstance(v, list) else []

    items = []
    for it in data.get("action_items") or []:
        if not isinstance(it, dict) or not str(it.get("task", "")).strip():
            continue
        due = str(it.get("due") or "").strip()
        items.append({
            "owner": str(it.get("owner") or "").strip(),
            "task": str(it["task"]).strip(),
            # 只接受完整日期; "下周" 这种没换算成功的不当截止日期用
            "due": due if _DATE_RE.match(due) else "",
        })
    return {
        "summary": str(data.get("summary") or "").strip(),
        "decisions": strs(data.get("decisions")),
        "action_items": items,
        "open_questions": strs(data.get("open_questions")),
    }


def render_markdown(meta: dict, minutes: dict, names: dict[str, str]) -> str:
    out = [f"# {meta.get('title', '会议纪要')}", "", f"- 日期: {meeting_date(meta)}"]
    if names:
        out.append(f"- 参会: {'、'.join(sorted(set(names.values())))}")
    out += ["", "## 摘要", "", minutes["summary"] or "(无)", ""]
    for title, key in (("决议", "decisions"), ("待定问题", "open_questions")):
        out += [f"## {title}", ""] + ([f"- {x}" for x in minutes[key]] or ["(无)"]) + [""]
    out += ["## 待办", ""]
    if minutes["action_items"]:
        for it in minutes["action_items"]:
            extra = " · ".join(x for x in (it["owner"], it["due"] and f"截止 {it['due']}") if x)
            out.append(f"- [ ] {it['task']}" + (f" ({extra})" if extra else ""))
    else:
        out.append("(无)")
    out += ["", "---", "由小鲶根据会议录音自动生成, 转写可能有误, 请核对。"]
    return "\n".join(out) + "\n"


async def summarize(
    meeting_dir: Path, *, gateway_url: str, auth_token: str, model: str | None = None, template: dict | None = None,
) -> dict:
    """template = None 时跟原来一样; 给了 {id, name, body} 就多调一次大模型按模版写 minutes.md
    (meeting_minutes_template.py)。结构化结果 (待办等) 两种情况都有, 「加入任务库」照常用。"""
    from .recmode.aggregator import call_llm  # noqa: PLC0415 — 复用同一个网关调用 (鉴权 / 选模型 / 不走代理)

    meta, transcript, names = load_inputs(meeting_dir)
    lines = transcript_lines(transcript, names)
    if not lines:
        raise ValueError("转写是空的, 没法生成纪要 (录音里可能没有人声)")
    chunks = chunk_lines(lines)
    kw = dict(gateway_url=gateway_url, auth_token=auth_token, model=model, temperature=0.2, max_tokens=4000, source=SOURCE)
    if len(chunks) == 1:
        minutes = parse_minutes(await call_llm(build_messages(meta, "\n".join(chunks[0]), partial=False, names=names), **kw))
    else:
        logger.info("meeting %s: 转写 %d 段, 分段总结", meta.get("id"), len(chunks))
        partials = [
            parse_minutes(await call_llm(build_messages(meta, "\n".join(c), partial=True, names=names), **kw))
            for c in chunks
        ]
        minutes = parse_minutes(await call_llm(build_reduce_messages(meta, partials), **kw))

    doc: dict[str, Any] = {"version": 1, "meeting_id": meta.get("id"), "chunks": len(chunks), **minutes}
    from . import meeting_minutes_template as mt  # noqa: PLC0415
    # 材料: 短会议给转写原文; 长会议给分段要点 (原文塞不下, 跟 reduce 那步同一个取舍)
    material = "\n".join(chunks[0]) if len(chunks) == 1 else "\n\n".join(
        f"### 第 {i + 1} 段要点\n{json.dumps(p, ensure_ascii=False)}" for i, p in enumerate(partials)
    )
    head = f"会议: {meta.get('title', '')}\n日期: {meeting_date(meta)}\n"
    if template is None:
        markdown = render_markdown(meta, minutes, names)
    elif template.get("kind", "markdown") in ("docx", "xlsx"):
        # 单位定死的 Word / Excel: 认空 → 大模型给每个空出值 → 代码填进原文件另存 (格式不动)
        from . import meeting_file_template as ft  # noqa: PLC0415
        src = Path(template["path"])
        slots = ft.inspect(src)
        known = {
            "会议名称": str(meta.get("title") or ""), "日期": meeting_date(meta),
            "参会人员": "、".join(sorted(set(names.values()))), "参会人数": str(meta.get("attendees") or ""),
            "时长": mt._duration(meta, transcript),
        }
        values = mt.parse_fill(
            await call_llm(mt.build_fill_messages(head, known, ft.slots_for_prompt(slots), material, minutes), **kw),
            slots,
        )
        values = ft.apply_todos(slots, values, minutes["action_items"])
        safe = re.sub(r'[\\/:*?"<>|\s]+', " ", str(meta.get("title") or "会议")).strip()[:40] or "会议"
        out = meeting_dir / f"{safe} 会议纪要{src.suffix.lower()}"
        ft.fill(src, out, slots, values)
        markdown = render_markdown(meta, minutes, names)
        doc["template"] = {"id": template["id"], "name": template["name"], "kind": template["kind"]}
        doc["output_file"] = str(out)
    else:
        filled = mt.fill_placeholders(template["body"], meta, transcript, names, meeting_date(meta))
        body = mt.clean_markdown(await call_llm(mt.build_template_messages(head, filled, material, minutes), **kw))
        if not body:
            raise ValueError("大模型按模版没写出内容, 换个模版或重试")
        markdown = f"{body}\n\n---\n由小鲶按纪要模版「{template['name']}」根据会议录音自动生成, 转写可能有误, 请核对。\n"
        doc["template"] = {"id": template["id"], "name": template["name"]}
    (meeting_dir / "minutes.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    (meeting_dir / "minutes.md").write_text(markdown, encoding="utf-8")
    return doc


_ID_RE = re.compile(r"^mtg_[A-Za-z0-9_-]{1,36}$")


def resolve_meeting_dir(params: dict) -> Path:
    """params → 会议目录。id 只认 Companion 生成的格式, 进不了路径穿越。"""
    mid = str(params.get("meeting_id") or "").strip()
    if not _ID_RE.match(mid):
        raise ValueError(f"meeting_id 不合法: {mid!r}")
    home = str(params.get("catfish_home") or "").strip()
    root = Path(home).expanduser() if home else Path.home() / ".catfish"
    d = root / "meetings" / mid
    if not (d / "meta.json").exists():
        raise ValueError(f"会议 {mid} 不存在")
    return d


async def handle_summarize(params: dict) -> dict:
    """JSON-RPC `meeting/summarize`。Rust (commands/meeting_rpc) 注入 auth_token /
    gateway_url / catfish_home —— gateway_url 显式传, 不靠 tool-bridge 进程的环境变量。"""
    d = resolve_meeting_dir(params)
    token = str(params.get("auth_token") or "").strip()
    if not token:
        raise RuntimeError("没登录 (拿不到访问令牌), 登录后再生成纪要")
    gw = str(params.get("gateway_url") or "").strip().rstrip("/")
    if not gw:
        raise ValueError("gateway_url 必填")
    from .meeting_minutes_template import validate_template  # noqa: PLC0415
    template = validate_template(params.get("template"))
    return await summarize(d, gateway_url=gw, auth_token=token, model=params.get("model") or None, template=template)


def handle_template_inspect(params: dict) -> dict:
    """JSON-RPC `meeting/template_inspect`: 上传的 Word / Excel 模版里认出哪些要填的空,
    给界面列出来让员工核对。认不出 → ValueError (带怎么加占位符的说明)。"""
    from . import meeting_file_template as ft  # noqa: PLC0415
    path = Path(str(params.get("path") or ""))
    if not path.is_file():
        raise ValueError(f"模版文件不存在: {path}")
    return {"slots": ft.slots_for_prompt(ft.inspect(path))}
