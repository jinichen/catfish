"""知识库里的「人 → 邮箱」(11/12 鸿波「如果现有的知识库有收件人的邮件地址, 是不是就会自动填进去」)。

给写信面板用: 会议纪要发邮件时按说话人名字自动填收件人; 收件人 / 抄送框打两个字就从这里给候选。
只读本机 ~/.catfish/wiki/entities (含装进来的部门页 wiki-shared), 不出本机。

邮箱从哪来 (实盘 11/12: 483 个条目里人物页带 email 字段的只有 3 个, 大多数写在「中电福富人员与邮箱对照」这种页的正文里):
  1. 人物页 (entity_type: person) 的 `email:` 字段 —— 名字 = 标题 + 别名; 没填字段、正文里只出现一个邮箱的也算他的。
  2. 任何页正文里「名字紧挨着完整邮箱」的写法:「迟璐 <a@b.cn>」「**肖展伟 a@b.cn**」「谢金华(集成能力中心负责人) a@b.cn」
     「| 陈秀平 | a@b.cn |」—— 名字是 2–4 个汉字, 跟邮箱之间只隔空格 / 括号说明 / 冒号 / 表格竖线。
  「ffchenrd=陈仁德」这种只有邮箱前缀、没写域名的不收 —— 域名不猜。
  同一个名字对上两个不同邮箱, 两个都给, 让界面标「有两个, 没自动填」。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .wiki_files import _catfish_home, _parse_field, _parse_list, _split_frontmatter

EMAIL = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"
_EMAIL_RE = re.compile(rf"^{EMAIL}$")
# 名字 (2–4 个汉字) + 可选 (说明) + 可选「邮箱 / 本机邮箱」+ 分隔 + 邮箱。实盘见过的写法 (11/12):
#   迟璐 <a@b>   **肖展伟 a@b**   谢金华(集成能力中心负责人;ITSS答辩分工=运维)= a@b   黄秋颖本机邮箱** a@b
#   管嘉阳邮箱 a@b   郭智祥(a@b   单位联系人李倩 a@b   | 陈秀平 | a@b |
_ROLE_BEFORE = ("联系人", "负责人", "接口人", "管理人员", "人员", "经办人", "对接人")
_PAIR = re.compile(
    r"(?:" + "|".join(f"(?<={w})" for w in _ROLE_BEFORE) + r"|(?<![\u4e00-\u9fff]))"   # 名字前面: 不是汉字, 或者是「联系人」这类
    r"([\u4e00-\u9fff]{2,4})"                                    # 名字
    r"(?:\s*[（(][^）)\n]{0,60}[）)])?"                              # (集成能力中心负责人;ITSS答辩分工=运维)
    r"(?:(?:本机|的)?(?:电子)?邮箱(?:是|为)?)?"                     # 邮箱 / 本机邮箱
    r"[\s*:：|,，、=<（(]*"                                          # 分隔
    rf"({EMAIL})"
)
# 名字位置上常见的不是人名的词
_NOT_NAME = {"邮箱", "邮件", "电话", "地址", "联系", "抄送", "收件", "发件", "接口人", "负责人", "员工", "同事", "领导",
             "部门", "公司", "确认", "告知", "签名", "回复", "电子邮箱", "邮件地址", "联系方式", "联系人", "通讯录", "本机", "原文", "附件"}
# 「发件地址」「邮箱账号」这类 —— 以这些字结尾的不是人名
_NOT_NAME_TAIL = ("地址", "账号", "帐号", "邮箱", "邮件", "电话", "号码", "信息", "方式", "栏", "人员", "部门", "公司", "中心")
MAX_PAGE_BYTES = 512 * 1024


def _people_page(fm: str) -> bool:
    return (_parse_field(fm, "entity_type") or _parse_field(fm, "subtype") or "").strip().lower() == "person"


def _add(out: dict[tuple[str, str], dict[str, Any]], name: str, email: str, source: str, aliases: list[str] | None = None) -> None:
    name, email = name.strip(), email.strip().strip(".,;，。；").lower()
    if not name or not _EMAIL_RE.match(email) or name in _NOT_NAME or name.endswith(_NOT_NAME_TAIL):
        return
    key = (name, email)
    if key in out:
        if source not in out[key]["sources"]:
            out[key]["sources"].append(source)
        return
    out[key] = {"name": name, "aliases": [a for a in (aliases or []) if a and a != name], "email": email, "sources": [source]}


def directory(home: Path | None = None) -> list[dict[str, Any]]:
    """[{name, aliases, email, sources: [页面标题]}], 按名字排。"""
    home = home or _catfish_home()
    out: dict[tuple[str, str], dict[str, Any]] = {}
    roots = [home / "wiki" / "entities", home / "wiki-shared"]
    for root in roots:
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*.md")):
            if ".history" in p.parts:
                continue
            try:
                if p.stat().st_size > MAX_PAGE_BYTES:
                    continue
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            fm, body = _split_frontmatter(text)
            title = (_parse_field(fm, "title") or p.stem).strip()
            if _people_page(fm):
                email = (_parse_field(fm, "email") or "").strip().strip('"\'')
                if not email:
                    # 人物页没填 email 字段, 正文里只出现一个邮箱 (「邮箱账号 a@b」) —— 就是这个人的
                    found = {m.lower() for m in re.findall(EMAIL, body)}
                    email = found.pop() if len(found) == 1 else ""
                if email:
                    _add(out, title, email, title, _parse_list(fm, "aliases"))
            for m in _PAIR.finditer(body):
                _add(out, m.group(1), m.group(2), title)
    return sorted(out.values(), key=lambda x: (x["name"], x["email"]))


def lookup(names: list[str], entries: list[dict[str, Any]]) -> dict[str, list[str]]:
    """名字 → 邮箱们 (名字或别名对上就算; 一个名字可能有两个邮箱)。"""
    res: dict[str, list[str]] = {}
    for n in names:
        n = n.strip()
        if not n:
            continue
        hits = [e["email"] for e in entries if n == e["name"] or n in e["aliases"]]
        res[n] = sorted(set(hits))
    return res


def handle(params: dict[str, Any]) -> dict[str, Any]:
    """JSON-RPC `wiki/people_emails`: {names?: [..]} → {people: [...], matches: {名字: [邮箱..]}}"""
    entries = directory()
    names = [str(x) for x in (params.get("names") or []) if str(x).strip()][:50]
    return {"people": entries, "matches": lookup(names, entries)}
