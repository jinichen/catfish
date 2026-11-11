"""Word 合并 · docx 包 (zip 里的部件、关系、内容类型) 和「把交回稿里的一段搬进合并版」(adopt)。

合并版以底稿为起点。交回稿里的段落 / 表格要放进来时, 它身上引用的东西都是交回稿那个包里的编号:
图片 / 图表 / 嵌入对象 / 超链接 (r:id → 交回稿的关系)、脚注尾注 (w:id → 交回稿的 footnotes.xml)、批注 (w:id → comments.xml)、
样式 (styleId)、列表编号 (numId)。adopt() 把这些一样样搬过来、换成合并版里的编号:
  · 部件 (图片等): 合并版里同一路径、同样内容的直接复用 (从底稿改出来的文件, 原有图片都在原路径); 否则复制成新名字,
    部件自己还有关系的 (图表带数据表) 一并递归搬。
  · 批注 / 脚注: 跟底稿里同编号且内容一样的 → 就是底稿那条, 不重复搬; 否则复制成新编号。
  · 样式 / 列表: 合并版里没有这个编号才搬 (连同 basedOn / next / link 链)。
"""
from __future__ import annotations

import copy
import io
import posixpath
import zipfile
from typing import Any

from lxml import etree

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
DOC = "word/document.xml"
_RT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
_CTW = "application/vnd.openxmlformats-officedocument.wordprocessingml."
NOTES = {  # 种类: (部件, 关系类型尾巴, 内容类型尾巴, 条目标签, 引用标签)
    "comment": ("word/comments.xml", "comments", "comments+xml", "comment", ("commentRangeStart", "commentRangeEnd", "commentReference")),
    "footnote": ("word/footnotes.xml", "footnotes", "footnotes+xml", "footnote", ("footnoteReference",)),
    "endnote": ("word/endnotes.xml", "endnotes", "endnotes+xml", "endnote", ("endnoteReference",)),
}
_PARSER = etree.XMLParser(remove_blank_text=False, resolve_entities=False, huge_tree=True)


def w(tag: str) -> str:
    return f"{{{W}}}{tag}"


def wa(el: Any, name: str, default: str | None = None) -> str | None:
    return el.get(f"{{{W}}}{name}", default)


def rels_name(part: str) -> str:
    d, f = posixpath.split(part)
    return posixpath.join(d, "_rels", f + ".rels")


class Pkg:
    def __init__(self, data: bytes):
        z = zipfile.ZipFile(io.BytesIO(data))
        self.order = [i.filename for i in z.infolist() if not i.is_dir()]
        self.raw: dict[str, bytes] = {n: z.read(n) for n in self.order}
        self._xml: dict[str, Any] = {}
        if DOC not in self.raw:
            raise ValueError("不是 Word 文档 (没有 word/document.xml)")
        self._adopted: dict[tuple[int, str], str] = {}   # (来源包, 来源部件) → 合并版里的部件
        self._notes: dict[tuple[int, str, str], str] = {}  # (来源包, 种类, 来源编号) → 合并版编号

    # ── 部件 ──
    def has(self, name: str) -> bool:
        return name in self.raw or name in self._xml

    def xml(self, name: str) -> Any:
        if name not in self._xml:
            self._xml[name] = etree.fromstring(self.raw[name], _PARSER)
        return self._xml[name]

    def put_xml(self, name: str, root: Any) -> None:
        self._xml[name] = root
        if name not in self.order:
            self.order.append(name)
            self.raw[name] = b""

    def put_raw(self, name: str, data: bytes) -> None:
        self._xml.pop(name, None)
        if name not in self.order:
            self.order.append(name)
        self.raw[name] = data

    def bytes_of(self, name: str) -> bytes:
        if name in self._xml:
            return etree.tostring(self._xml[name], xml_declaration=True, encoding="UTF-8", standalone=True)
        return self.raw[name]

    def save(self) -> bytes:
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for n in self.order:
                z.writestr(n, self.bytes_of(n))
        return out.getvalue()

    # ── 关系 / 内容类型 ──
    def rels(self, part: str) -> Any:
        rn = rels_name(part)
        if not self.has(rn):
            self.put_xml(rn, etree.Element(f"{{{PR}}}Relationships", nsmap={None: PR}))
        return self.xml(rn)

    def rel(self, part: str, rid: str) -> tuple[str, str, bool] | None:
        """(类型, 目标部件路径或外部地址, 是不是外部)"""
        if not self.has(rels_name(part)):
            return None
        for r in self.rels(part):
            if r.get("Id") == rid:
                ext = r.get("TargetMode") == "External"
                tgt = r.get("Target", "")
                if not ext:
                    tgt = tgt.lstrip("/") if tgt.startswith("/") else posixpath.normpath(posixpath.join(posixpath.dirname(part), tgt))
                return r.get("Type", ""), tgt, ext
        return None

    def add_rel(self, part: str, rtype: str, target: str, external: bool = False) -> str:
        root = self.rels(part)
        if not external:
            target = posixpath.relpath(target, posixpath.dirname(part) or ".")
        for r in root:   # 一模一样的关系已经有了: 复用
            if r.get("Type") == rtype and r.get("Target") == target and (r.get("TargetMode") == "External") == external:
                return r.get("Id")
        ids = {r.get("Id") for r in root}
        k = len(ids) + 1
        while f"rId{k}" in ids:
            k += 1
        el = etree.SubElement(root, f"{{{PR}}}Relationship", Id=f"rId{k}", Type=rtype, Target=target)
        if external:
            el.set("TargetMode", "External")
        return f"rId{k}"

    def rel_of_type(self, part: str, tail: str) -> str | None:
        if not self.has(rels_name(part)):
            return None
        for r in self.rels(part):
            if r.get("Type", "").endswith("/" + tail):
                return self.rel(part, r.get("Id"))[1]
        return None

    def content_type(self, name: str) -> str | None:
        ct = self.xml("[Content_Types].xml")
        for o in ct.findall(f"{{{CT}}}Override"):
            if o.get("PartName", "").lstrip("/") == name:
                return o.get("ContentType")
        ext = posixpath.splitext(name)[1].lstrip(".").lower()
        for d in ct.findall(f"{{{CT}}}Default"):
            if d.get("Extension", "").lower() == ext:
                return d.get("ContentType")
        return None

    def set_content_type(self, name: str, ctype: str | None, src: "Pkg | None" = None) -> None:
        if not ctype or self.content_type(name) == ctype:
            return
        ct = self.xml("[Content_Types].xml")
        ext = posixpath.splitext(name)[1].lstrip(".").lower()
        src_default = src is not None and any(d.get("Extension", "").lower() == ext and d.get("ContentType") == ctype
                                              for d in src.xml("[Content_Types].xml").findall(f"{{{CT}}}Default"))
        has_default = any(d.get("Extension", "").lower() == ext for d in ct.findall(f"{{{CT}}}Default"))
        if src_default and not has_default:
            ct.insert(0, etree.Element(f"{{{CT}}}Default", Extension=ext, ContentType=ctype))
        else:
            etree.SubElement(ct, f"{{{CT}}}Override", PartName="/" + name, ContentType=ctype)

    def _fresh(self, name: str) -> str:
        if not self.has(name):
            return name
        stem, ext = posixpath.splitext(name)
        k = 1
        while self.has(f"{stem}_m{k}{ext}"):
            k += 1
        return f"{stem}_m{k}{ext}"

    # ── 从别的包搬部件 ──
    def import_part(self, src: "Pkg", name: str) -> str:
        key = (id(src), name)
        if key in self._adopted:
            return self._adopted[key]
        data = src.bytes_of(name)
        if self.has(name) and self.bytes_of(name) == data and src.has(rels_name(name)) == self.has(rels_name(name)):
            self._adopted[key] = name
            return name
        new = self._fresh(name)
        self._adopted[key] = new
        self.put_raw(new, data)
        self.set_content_type(new, src.content_type(name), src)
        if src.has(rels_name(name)):
            rels = copy.deepcopy(src.rels(name))
            for r in rels:
                if r.get("TargetMode") == "External":
                    continue
                _t, tgt, _e = src.rel(name, r.get("Id"))
                if src.has(tgt):
                    r.set("Target", posixpath.relpath(self.import_part(src, tgt), posixpath.dirname(new) or "."))
            self.put_xml(rels_name(new), rels)
        return new

    def _note_part(self, kind: str, src: "Pkg") -> Any:
        part, rtail, cttail, tag, _refs = NOTES[kind]
        if not self.has(part):
            root = etree.Element(w(tag + "s"), nsmap=self.xml(DOC).nsmap)
            if kind != "comment":   # 脚注 / 尾注部件先要有分隔线那两条
                sp = src.rel_of_type(DOC, rtail)
                if sp and src.has(sp):
                    for n in src.xml(sp):
                        if wa(n, "type") in ("separator", "continuationSeparator", "continuationNotice"):
                            root.append(copy.deepcopy(n))
            self.put_xml(part, root)
            self.add_rel(DOC, _RT + rtail, part)
            self.set_content_type(part, _CTW + cttail)
        return self.xml(part)

    def import_note(self, src: "Pkg", kind: str, sid: str) -> str | None:
        key = (id(src), kind, sid)
        if key in self._notes:
            return self._notes[key]
        part_s = src.rel_of_type(DOC, NOTES[kind][1])
        tag = NOTES[kind][3]
        if not part_s or not src.has(part_s):
            return None
        item = next((n for n in src.xml(part_s) if n.tag == w(tag) and wa(n, "id") == sid), None)
        if item is None:
            return None
        root = self._note_part(kind, src)
        mine = next((n for n in root if n.tag == w(tag) and wa(n, "id") == sid), None)
        if mine is not None and _same_note(mine, item):
            self._notes[key] = sid
            return sid
        ids = [int(wa(n, "id") or 0) for n in root if (wa(n, "id") or "").lstrip("-").isdigit()]
        new_id = str(max(ids + [0]) + 1)
        self._notes[key] = new_id
        el = self.adopt(item, src, part_s, NOTES[kind][0])
        el.set(w("id"), new_id)
        root.append(el)
        return new_id

    def _ensure_style(self, src: "Pkg", sid: str, depth: int = 0) -> None:
        if depth > 10 or not src.has("word/styles.xml"):
            return
        if not self.has("word/styles.xml"):
            self.put_raw("word/styles.xml", src.bytes_of("word/styles.xml"))
            self.add_rel(DOC, _RT + "styles", "word/styles.xml")
            self.set_content_type("word/styles.xml", _CTW + "styles+xml")
            return
        mine = self.xml("word/styles.xml")
        if any(wa(s, "styleId") == sid for s in mine.findall(w("style"))):
            return
        st = next((s for s in src.xml("word/styles.xml").findall(w("style")) if wa(s, "styleId") == sid), None)
        if st is None:
            return
        mine.append(copy.deepcopy(st))
        for t in ("basedOn", "next", "link"):
            e = st.find(w(t))
            if e is not None and wa(e, "val"):
                self._ensure_style(src, wa(e, "val"), depth + 1)

    def _ensure_num(self, src: "Pkg", num_id: str) -> str:
        sp = "word/numbering.xml"
        if not src.has(sp) or num_id == "0":
            return num_id
        snum = next((n for n in src.xml(sp).findall(w("num")) if wa(n, "numId") == num_id), None)
        if snum is None:
            return num_id
        if not self.has(sp):
            self.put_raw(sp, src.bytes_of(sp))
            self.add_rel(DOC, _RT + "numbering", sp)
            self.set_content_type(sp, _CTW + "numbering+xml")
            return num_id
        mine = self.xml(sp)
        if any(wa(n, "numId") == num_id for n in mine.findall(w("num"))):
            return num_id
        key = (id(src), "num", num_id)
        if key in self._notes:
            return self._notes[key]
        abs_ref = snum.find(w("abstractNumId"))
        sabs = next((a for a in src.xml(sp).findall(w("abstractNum"))
                     if abs_ref is not None and wa(a, "abstractNumId") == wa(abs_ref, "val")), None)
        new_num = str(max([int(wa(n, "numId") or 0) for n in mine.findall(w("num"))] + [0]) + 1)
        num = copy.deepcopy(snum)
        num.set(w("numId"), new_num)
        if sabs is not None:
            new_abs = str(max([int(wa(a, "abstractNumId") or 0) for a in mine.findall(w("abstractNum"))] + [-1]) + 1)
            ab = copy.deepcopy(sabs)
            ab.set(w("abstractNumId"), new_abs)
            first_num = mine.find(w("num"))
            (first_num.addprevious(ab) if first_num is not None else mine.append(ab))
            num.find(w("abstractNumId")).set(w("val"), new_abs)
        mine.append(num)
        self._notes[key] = new_num
        return new_num

    def adopt(self, el: Any, src: "Pkg", src_part: str = DOC, dst_part: str = DOC) -> Any:
        """交回稿里的一个元素 (段落 / 表格 / 行 / 批注内容) → 合并版里能用的副本。src 是底稿本身时原样复制。"""
        out = copy.deepcopy(el)
        if src is self:
            return out
        for node in out.iter():
            if not isinstance(node.tag, str):
                continue
            for k, v in list(node.attrib.items()):
                if k.startswith(f"{{{R}}}"):
                    got = src.rel(src_part, v)
                    if not got:
                        continue
                    rtype, tgt, ext = got
                    if ext:
                        node.set(k, self.add_rel(dst_part, rtype, tgt, external=True))
                    elif src.has(tgt):
                        node.set(k, self.add_rel(dst_part, rtype, self.import_part(src, tgt)))
            local = etree.QName(node).localname if node.tag.startswith(f"{{{W}}}") else ""
            for kind, (_p, _r, _c, _t, refs) in NOTES.items():
                if local in refs and wa(node, "id") is not None:
                    nid = self.import_note(src, kind, wa(node, "id"))
                    if nid is not None:
                        node.set(w("id"), nid)
            if local in ("pStyle", "rStyle", "tblStyle") and wa(node, "val"):
                self._ensure_style(src, wa(node, "val"))
            if local == "numId" and wa(node, "val"):
                node.set(w("val"), self._ensure_num(src, wa(node, "val")))
        return out


def _note_text(n: Any) -> str:
    return "".join(n.itertext())


def _same_note(a: Any, b: Any) -> bool:
    return (_note_text(a) == _note_text(b) and wa(a, "author") == wa(b, "author") and wa(a, "date") == wa(b, "date"))
