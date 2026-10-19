"""技能 / 工作流给人看的中文名 (10/19, 鸿波「这种清单对普通办公人员只能靠猜」)。

界面原来一律显示路径 `personal/weekly-zizhi-email-digest`。中文其实早就有, 只是没用上:
SKILL.md 的标题 `# eis-checkin — 登录 EIS 后点上班打卡按钮`、description 第一句。
按这个顺序取 (取到就停):

  1. frontmatter `title` (新建技能 / 工作流时小鲶起的名字)
  2. 一级标题破折号后面那段中文 (去掉括号里的补充说明)
  3. description 第一句
  4. 实在没有才用目录名

Companion (Rust) 的技能库读同一套规则 —— 改这里要同步 `skill_names.rs`。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

MAX_TITLE = 16
_DASH = re.compile(r"\s+[—–-]{1,2}\s+")
_PAREN = re.compile(r"[（(][^）)]*[）)]")
_SENT = re.compile(r"[。！？!?；;\n]")
_CLAUSE = re.compile(r"[，,。！？!?；;：:\n]|\s+[—–]\s+")
#: 写给模型看的指令 (description 开头常见), 给人看时去掉
_DIRECTIVE = re.compile(r"⚠️?\s*MUST CALL\s*[:：]?|\*\*|立即调用[^。；;]*|\(?catfish_[a-z_]+[^)）]*[)）]?", re.I)
_TRIGGER = re.compile(r"员工说((?:\s*[\"“「][^\"”」]{1,20}[\"”」]\s*[/、，,]?)+)\s*等?(?:关键词)?时")
_QUOTED = re.compile(r"[\"“「]([^\"”」]{1,20})[\"”」]")
_HAS_CJK = re.compile(r"[一-鿿]")


def _clip(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip(" ,，、:：-—")
    return s if len(s) <= MAX_TITLE else s[:MAX_TITLE - 1] + "…"


_FM_LINE = re.compile(r"^([A-Za-z_][\w-]*):[ \t]*(.*)$")


def _lenient_fm(head: str) -> dict[str, Any]:
    """YAML 解析不了时 (常见: description 里写了「触发词: 做原型」这种冒号加空格) 逐行捞顶层的 key: value。"""
    out: dict[str, Any] = {}
    for line in head.splitlines():
        m = _FM_LINE.match(line)
        if m and m.group(2) and not m.group(2).startswith(("|", ">")):
            out.setdefault(m.group(1), m.group(2).strip().strip("'\""))
    return out


def parse_skill_md(text: str) -> tuple[dict[str, Any], str]:
    """(frontmatter, 正文)。没有 frontmatter → ({}, 全文); frontmatter 不是合法 YAML → 逐行捞 key: value。"""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            try:
                fm = yaml.safe_load(parts[1]) or {}
                return (fm if isinstance(fm, dict) else {}), parts[2]
            except yaml.YAMLError:
                return _lenient_fm(parts[1]), parts[2]
    return {}, text


def title_from(fm: dict[str, Any], body: str, fallback: str) -> str:
    t = str(fm.get("title") or "").strip()
    if t:
        return _clip(t)
    for line in body.splitlines():
        if line.startswith("# "):
            head = line[2:].strip()
            m = _DASH.split(head, maxsplit=1)
            cand = _PAREN.sub("", m[1] if len(m) == 2 else head).strip()
            # 「花叔Design · Huashu Design …」这种中英并列, 留前面带中文的那段
            first = re.split(r"\s*[·|｜]\s*", cand, maxsplit=1)[0].strip()
            if first and _HAS_CJK.search(first):
                cand = first
            # 标题本身就是英文目录名 (例 `# eis-login`) 没用, 往下找
            if cand and _HAS_CJK.search(cand):
                return _clip(cand.replace(" skill", ""))
            break
    desc = _DIRECTIVE.sub("", str(fm.get("description") or "")).strip()
    if desc and not _TRIGGER.search(desc):
        first = _CLAUSE.split(_PAREN.sub("", desc), maxsplit=1)[0].strip()
        if first and _HAS_CJK.search(first):
            return _clip(first)
    return fallback


def needs_name(fm: dict[str, Any], body: str, fallback: str) -> bool:
    """没有 title、也拆不出像样的中文名 (只能用目录名 / 描述第一句凑合) —— 该让小鲶起个名。"""
    if str(fm.get("title") or "").strip():
        return False
    for line in body.splitlines():
        if line.startswith("# "):
            m = _DASH.split(line[2:].strip(), maxsplit=1)
            if len(m) == 2 and _HAS_CJK.search(_PAREN.sub("", m[1])):
                return False
            break
    return True


def summary_from(fm: dict[str, Any], body: str) -> str:
    """一句话说明 (description 第一句, 没有就正文第一段), 最多 80 字。"""
    raw = str(fm.get("description") or "").strip()
    trig = _TRIGGER.search(raw)
    if trig:
        # 「员工说"登录 EIS"/"进 EIS"等关键词时, 立即调用…」→「说「登录 EIS」「进 EIS」时用」
        phrases = _QUOTED.findall(trig.group(1))[:3]
        return "说" + "".join(f"「{p}」" for p in phrases) + "时用"
    desc = _DIRECTIVE.sub("", raw).strip(" ,，:：")
    if not desc:
        for para in body.split("\n\n"):
            p = para.strip()
            if p and not p.startswith("#"):
                desc = p
                break
    desc = re.sub(r"\s+", " ", desc)
    return desc if len(desc) <= 80 else desc[:79] + "…"


def skill_title(skill_dir: Path) -> str:
    try:
        fm, body = parse_skill_md((skill_dir / "SKILL.md").read_text(encoding="utf-8"))
    except OSError:
        return skill_dir.name
    return title_from(fm, body, skill_dir.name)


def skill_display(skill_dir: Path) -> dict[str, str]:
    """{title, summary} 给界面用。"""
    try:
        fm, body = parse_skill_md((skill_dir / "SKILL.md").read_text(encoding="utf-8"))
    except OSError:
        return {"title": skill_dir.name, "summary": ""}
    return {"title": title_from(fm, body, skill_dir.name), "summary": summary_from(fm, body)}


# ── 改名 / 一次性补名 ────────────────────────────────────────────

_TITLE_LINE = re.compile(r"^title:.*$", re.M)


def set_title(skill_dir: Path, title: str) -> str:
    """把 `title: <中文名>` 写进 SKILL.md frontmatter (只动这一行, 别的格式原样)。"""
    title = re.sub(r"[\r\n]+", " ", str(title or "")).strip()
    if not title:
        raise ValueError("名字不能空")
    if len(title) > 30:
        raise ValueError("名字太长了 (最多 30 字)")
    md = skill_dir / "SKILL.md"
    text = md.read_text(encoding="utf-8")
    line = "title: " + json.dumps(title, ensure_ascii=False)
    if text.startswith("---"):
        head, sep, rest = text[3:].partition("\n---")
        if sep:
            if _TITLE_LINE.search(head):
                head = _TITLE_LINE.sub(line, head, count=1)
            else:
                lines = head.split("\n")
                at = next((i + 1 for i, ln in enumerate(lines) if ln.startswith("name:")), 1)
                lines.insert(at, line)
                head = "\n".join(lines)
            md.write_text("---" + head + sep + rest, encoding="utf-8")
            return title
    md.write_text(f"---\n{line}\n---\n" + text, encoding="utf-8")
    return title


_NAME_PROMPT = """下面是员工电脑上几个技能 / 工作流的说明。给每个起一个给办公人员看的中文名:
- 4 到 12 个字, 说清它做什么 (例: 登录 EIS 门户 / 每周资质邮件摘要 / 视频音频转写摘要 / 深度调研)
- 不要「技能」「工作流」「skill」这类字眼, 不要标点结尾
只交回 JSON: {"names": {"<编号>": "<中文名>", ...}}

"""


def backfill(dirs: list[Path], *, llm=None) -> dict[str, str]:
    """给没有像样中文名的技能起名并写进 title。返回 {目录: 新名字}。一次调一次模型 (最多 30 个)。"""
    from . import workflow_xiaonian  # noqa: PLC0415

    todo = []
    for d in dirs:
        try:
            fm, body = parse_skill_md((d / "SKILL.md").read_text(encoding="utf-8"))
        except OSError:
            continue
        if needs_name(fm, body, d.name):
            todo.append((d, fm, body))
    todo = todo[:30]
    if not todo:
        return {}
    items = []
    for i, (d, fm, body) in enumerate(todo, 1):
        desc = re.sub(r"\s+", " ", str(fm.get("description") or ""))[:300]
        head = next((ln[2:].strip() for ln in body.splitlines() if ln.startswith("# ")), "")
        items.append(f"[{i}] 目录名: {d.name}\n    标题: {head[:80]}\n    说明: {desc}")
    raw = (llm or workflow_xiaonian._call_llm)(_NAME_PROMPT + "\n".join(items))
    names = workflow_xiaonian.parse_json(raw).get("names") or {}
    out: dict[str, str] = {}
    for i, (d, _fm, _body) in enumerate(todo, 1):
        name = str(names.get(str(i)) or "").strip().rstrip("。.，,")
        if 2 <= len(name) <= 20:
            out[str(d)] = set_title(d, name)
    return out


# ── 给界面的入口 (server.py pim/skillname_*) ───────────────────────

def _local_root() -> Path:
    from .catfish_tools_skill_ops import _local_skills_root  # noqa: PLC0415
    return _local_skills_root()


def _own_dir(path: str) -> Path:
    """只许动员工自己 ~/.catfish/skills 下的技能 (仓库里团队审定的、装来的专家包都不改)。"""
    root = _local_root().resolve()
    d = Path(path).expanduser().resolve()
    if root not in d.parents or not (d / "SKILL.md").is_file():
        raise ValueError("只能给你自己电脑上的技能改名")
    return d


def _marker() -> Path:
    from . import wiki_files  # noqa: PLC0415
    return wiki_files._catfish_home() / "skill_names_backfill.json"


def backfill_once(*, llm=None) -> dict[str, Any]:
    """界面打开技能页时调: 只给还没试过的技能起名 (试过的记下来, 起不出也不再反复调模型)。"""
    root = _local_root()
    try:
        tried = set(json.loads(_marker().read_text(encoding="utf-8")).get("tried") or [])
    except (OSError, ValueError):
        tried = set()
    dirs = [md.parent for md in sorted(root.glob("*/*/SKILL.md")) if str(md.parent) not in tried] if root.is_dir() else []
    if not dirs:
        return {"ok": True, "named": {}}
    try:
        named = backfill(dirs, llm=llm)
    except (RuntimeError, ValueError) as e:  # 没登录 / 模型不通 —— 下次再试, 不记 tried
        return {"ok": False, "error": str(e), "named": {}}
    tried |= {str(d) for d in dirs}
    m = _marker()
    m.parent.mkdir(parents=True, exist_ok=True)
    m.write_text(json.dumps({"tried": sorted(tried)}, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "named": named}


def pim_handlers(params: dict[str, Any]) -> dict[str, Any]:
    return {
        "pim/skillname_set": lambda: {"ok": True, "title": set_title(_own_dir(str(params.get("path") or "")),
                                                                   str(params.get("title") or ""))},
        "pim/skillname_backfill": backfill_once,
    }
