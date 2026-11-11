"""pptx 合并的包 (OPC) 层: 页内容签名 + 把交回稿的页搬进底稿 (slides.py 用, docs/DOC-REVISE-MERGE-PLAN.md §5)。

· 指纹: 页引用的部件 (图片 / 媒体按内容哈希, 外链按地址, 别的页按页编号, 其它 XML 部件按去掉噪音后的 XML)。
  页本身的签名按语义算, 见 slides_sig.py。
· 搬页: 在底稿的包上新建部件 (名字不跟已有的撞), rels 保留原来的 rId (页 XML 不用改); 图片 / 媒体内容相同的
  复用底稿里已有的那份; 版式按名字映射到底稿同名版式, 底稿没有就把交回稿里那个版式所在的母版整个带过来;
  备注页挂到底稿的备注母版上。[Content_Types].xml 由 python-pptx 保存时按部件重新生成。
"""
from __future__ import annotations

import copy
import hashlib
import re
import uuid
from typing import Any, Callable

from lxml import etree
from pptx.opc.constants import RELATIONSHIP_TARGET_MODE as RTM
from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from pptx.opc.package import PartFactory, XmlPart, _Relationship
from pptx.opc.packuri import PackURI
from pptx.oxml.ns import qn
from pptx.oxml.xmlchemy import OxmlElement

R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
_A_T = qn("a:t")
P14_NS = "http://schemas.microsoft.com/office/powerpoint/2010/main"
_SECTION_URI = "{521415D9-36F7-43E2-AB2F-B90AF26B5E84}"
# 只影响拼写检查 / 输入法 / 内部编号的属性和元素, 不算内容
_NOISE_ATTRS = {"dirty", "err", "smtClean", "smtId", "noProof", "lang", "altLang", "bmk"}
_NOISE_TAGS = {"creationId", "modId"}
_EXT = {qn("p:ext"), qn("a:ext")}
_EMPTY_OK = {qn("a:rPr"), qn("a:endParaRPr"), qn("p:extLst"), qn("a:extLst")} | _EXT
# 批注 (老式 / 新式) 不参与合并
COMMENT_RTS = {RT.COMMENTS, "http://schemas.microsoft.com/office/2018/10/relationships/comments"}
_MEDIA = ("image", "video", "audio")


def sha(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


# ── 内容签名 ────────────────────────────────────────────────────────────────

def canon(el: Any, ref: Callable[[str], str]) -> bytes:
    """去掉不算内容的东西后的规范化 XML; r:id 这类引用经 ref() 换成目标的指纹。"""
    el = copy.deepcopy(el)
    drop = []
    for e in el.iter():
        if not isinstance(e.tag, str):          # 注释 / 处理指令
            drop.append(e)
            continue
        if etree.QName(e).localname in _NOISE_TAGS:
            drop.append(e)
            continue
        for k in list(e.attrib):
            q = etree.QName(k)
            if (q.namespace is None and q.localname in _NOISE_ATTRS) or (q.namespace == _MC_NS
                                                                          and q.localname == "Ignorable"):
                del e.attrib[k]
            elif q.namespace == R_NS:
                e.set(k, ref(e.get(k) or ""))
        if e.tag != _A_T and e.text is not None and not e.text.strip():
            e.text = None
        if e.tail is not None and not e.tail.strip():
            e.tail = None
    for e in drop:
        _remove(e)
    # 去掉上面清空后剩下的空壳 (<a:rPr/>、只装过 creationId 的 extLst); 倒序遍历 = 先子后父
    for e in reversed(list(el.iter())):
        if e is el or not isinstance(e.tag, str) or e.tag not in _EMPTY_OK or len(e) or (e.text or "").strip():
            continue
        if e.tag in _EXT or not e.attrib:     # p:ext 只剩 uri 也是空壳
            _remove(e)
    return etree.tostring(el, method="c14n", exclusive=True)


def _remove(e: Any) -> None:
    p = e.getparent()
    if p is not None:
        if e.tail and e.tail.strip():
            prev = e.getprevious()
            if prev is not None:
                prev.tail = (prev.tail or "") + e.tail
            else:
                p.text = (p.text or "") + e.tail
        p.remove(e)


class Fingerprint:
    """一份 pptx 里各部件的指纹。sid_of: id(页部件) → 页编号 (页里链到别的页时, 按页编号比)。"""

    def __init__(self, sid_of: dict[int, int]):
        self.sid_of = sid_of
        self.memo: dict[int, str] = {}

    def ref_fn(self, part: Any) -> Callable[[str], str]:
        def ref(rid: str) -> str:
            rel = part.rels.get(rid)
            if rel is None:
                return "?"
            if rel.is_external:
                return "ext:" + rel.target_ref
            if rel.reltype == RT.SLIDE:
                return "slide:%s" % self.sid_of.get(id(rel.target_part), "?")
            if rel.reltype == RT.SLIDE_LAYOUT:
                return "layout:" + layout_name(rel.target_part)
            if rel.reltype in (RT.NOTES_SLIDE, RT.NOTES_MASTER) or rel.reltype in COMMENT_RTS:
                return rel.reltype.rsplit("/", 1)[-1]
            return self.of(rel.target_part)
        return ref

    def of(self, part: Any) -> str:
        k = id(part)
        if k in self.memo:
            return self.memo[k]
        self.memo[k] = "cycle"        # 母版 ↔ 版式互相引用
        if isinstance(part, XmlPart):
            el = etree.fromstring(part.blob)
            v = sha(canon(el, self.ref_fn(part)))
        else:
            v = sha(part.blob)
        self.memo[k] = v
        return v


def layout_name(layout_part: Any) -> str:
    try:
        return layout_part.slide_layout.name or ""
    except AttributeError:
        return ""


def comments_fp(slide_part: Any) -> str:
    blobs = [r.target_part.blob for r in slide_part.rels.values() if not r.is_external and r.reltype in COMMENT_RTS]
    return sha(b"".join(blobs)) if blobs else ""


def masters(prs_part: Any) -> list[Any]:
    out = []
    lst = prs_part._element.find(qn("p:sldMasterIdLst"))
    for m in (lst if lst is not None else []):
        rid = m.get(qn("r:id"))
        if rid in prs_part.rels:
            out.append(prs_part.related_part(rid))
    return out


def layouts_of(master_part: Any) -> list[Any]:
    out = []
    lst = master_part._element.find(qn("p:sldLayoutIdLst"))
    for e in (lst if lst is not None else []):
        rid = e.get(qn("r:id"))
        if rid in master_part.rels:
            out.append(master_part.related_part(rid))
    return out


def sections(prs_part: Any) -> list[tuple[str, str, list[int]]]:
    """[(节名, 节 id, [页编号...])]; 没分节返 []。"""
    lst = prs_part._element.find(f".//{{{P14_NS}}}sectionLst")
    if lst is None:
        return []
    out = []
    for sec in lst.findall(f"{{{P14_NS}}}section"):
        ids = [int(e.get("id")) for e in sec.iter(f"{{{P14_NS}}}sldId")]
        out.append((sec.get("name") or "", sec.get("id") or "", ids))
    return out


# ── 搬页 ───────────────────────────────────────────────────────────────────

def set_rel(part: Any, rid: str, reltype: str, target: Any, external: bool = False) -> None:
    """按指定的 rId 加 / 改一条关系 (页 XML 里引用的 rId 不用改)。"""
    part.rels._rels[rid] = _Relationship(part.partname.baseURI, rid, reltype,
                                         RTM.EXTERNAL if external else RTM.INTERNAL, target)


def drop_refs(el: Any, rids: set[str]) -> int:
    """去掉 XML 里引用这些 rId 的元素 (超链接 / 新式批注的 commentRel); 返去掉了几个。"""
    hit = [e for e in el.iter() if isinstance(e.tag, str)
           and any(etree.QName(k).namespace == R_NS and v in rids for k, v in e.attrib.items())]
    for e in hit:
        parent = e.getparent()
        _remove(e)
        if parent is not None and parent.tag in _EXT and not len(parent):
            _remove(parent)
    return len(hit)


class Dest:
    """合并版 (底稿的包) 上的公共状态: 用过的部件名、已有的图片 / 媒体、版式名 → 版式、母版 / 版式编号。"""

    def __init__(self, prs: Any):
        self.prs = prs
        self.part = prs.part
        self.pkg = prs.part.package
        self.used: set[str] = set()
        self.media: dict[tuple[str, str], Any] = {}
        self.refresh()
        self.layouts: dict[str, Any] = {}
        for m in masters(self.part):
            for lay in layouts_of(m):
                self.layouts.setdefault(layout_name(lay), lay)
        ids = [int(e.get("id")) for e in self.part._element.iter(qn("p:sldMasterId"))]
        for m in masters(self.part):
            ids += [int(e.get("id")) for e in m._element.iter(qn("p:sldLayoutId"))]
        self._lid = max(ids + [2147483647])

    def refresh(self) -> None:
        for p in self.pkg.iter_parts():
            self.used.add(str(p.partname))
            if not isinstance(p, XmlPart) and p.content_type.split("/")[0] in _MEDIA:
                self.media.setdefault((p.content_type, sha(p.blob)), p)

    def fresh(self, partname: str) -> PackURI:
        m = re.match(r"^(.*?)(\d*)(\.[^./]*)?$", partname)
        stem, ext = (m.group(1), m.group(3) or "") if m else (partname, "")
        n = 1
        while f"{stem}{n}{ext}" in self.used:
            n += 1
        name = f"{stem}{n}{ext}"
        self.used.add(name)
        return PackURI(name)

    def notes_master(self) -> Any:
        nm = self.part.notes_master_part
        self.refresh()
        return nm

    def add_master(self, master: Any) -> None:
        """带过来的母版挂进 presentation.xml 的 sldMasterIdLst; 母版和版式编号换成不重复的新号。"""
        rid = self.part.relate_to(master, RT.SLIDE_MASTER)
        el = OxmlElement("p:sldMasterId")
        self._lid += 1
        el.set("id", str(self._lid))
        el.set(qn("r:id"), rid)
        self.part._element.find(qn("p:sldMasterIdLst")).append(el)
        for e in master._element.iter(qn("p:sldLayoutId")):
            self._lid += 1
            e.set("id", str(self._lid))
        for lay in layouts_of(master):
            self.layouts.setdefault(layout_name(lay), lay)


class Importer:
    """把一份交回稿里的页 (连同它引用的部件) 搬进 Dest。同一份交回稿共用一个 (同一张图只搬一次)。"""

    def __init__(self, dest: Dest):
        self.d = dest
        self.memo: dict[int, Any] = {}

    def _new(self, src: Any) -> Any:
        return PartFactory(self.d.fresh(str(src.partname)), src.content_type, self.d.pkg, src.blob)

    def slide(self, src: Any) -> Any:
        new = self._new(src)
        dropped: set[str] = set()
        for rid, rel in src.rels.items():
            if rel.is_external:
                set_rel(new, rid, rel.reltype, rel.target_ref, True)
            elif rel.reltype == RT.SLIDE_LAYOUT:
                set_rel(new, rid, rel.reltype, self.layout(rel.target_part))
            elif rel.reltype == RT.NOTES_SLIDE:
                set_rel(new, rid, rel.reltype, self.notes(rel.target_part, new))
            elif rel.reltype == RT.SLIDE:
                set_rel(new, rid, rel.reltype, rel.target_part)   # 链到别的页: 全部搬完后 relink() 再改指向
            elif rel.reltype in COMMENT_RTS:
                dropped.add(rid)
            else:
                set_rel(new, rid, rel.reltype, self.copy(rel.target_part))
        if dropped:
            drop_refs(new._element, dropped)
        return new

    def notes(self, src: Any, slide_part: Any) -> Any:
        new = self._new(src)
        for rid, rel in src.rels.items():
            if rel.is_external:
                set_rel(new, rid, rel.reltype, rel.target_ref, True)
            elif rel.reltype == RT.NOTES_MASTER:
                set_rel(new, rid, rel.reltype, self.d.notes_master())
            elif rel.reltype == RT.SLIDE:
                set_rel(new, rid, rel.reltype, slide_part)
            else:
                set_rel(new, rid, rel.reltype, self.copy(rel.target_part))
        return new

    def layout(self, src: Any) -> Any:
        hit = self.d.layouts.get(layout_name(src))
        if hit is not None:
            return hit
        if id(src) not in self.memo:
            master = src.part_related_by(RT.SLIDE_MASTER)
            self.d.add_master(self.copy(master))
        return self.memo.get(id(src)) or self.copy(src)

    def adopt(self, el: Any, src: Any, dst: Any) -> Any:
        """el 是从 src 那页拷出来的一个形状 / 一段: 它引用的图片 / 图表 / 链接搬进合并版, rId 改写成 dst 这页的。"""
        for e in el.iter():
            if not isinstance(e.tag, str):
                continue
            for k, v in list(e.attrib.items()):
                if etree.QName(k).namespace != R_NS:
                    continue
                rel = src.rels.get(v)
                if rel is None:
                    continue
                if rel.is_external:
                    e.set(k, dst.rels.get_or_add_ext_rel(rel.reltype, rel.target_ref))
                elif rel.reltype == RT.SLIDE:
                    e.set(k, dst.relate_to(rel.target_part, RT.SLIDE))      # relink() 再改指向
                else:
                    e.set(k, dst.relate_to(self.copy(rel.target_part), rel.reltype))
        return el

    def copy(self, src: Any) -> Any:
        hit = self.memo.get(id(src))
        if hit is not None:
            return hit
        key = None
        if not isinstance(src, XmlPart) and src.content_type.split("/")[0] in _MEDIA:
            key = (src.content_type, sha(src.blob))
            if key in self.d.media:
                self.memo[id(src)] = self.d.media[key]
                return self.d.media[key]
        new = self._new(src)
        self.memo[id(src)] = new
        if key is not None:
            self.d.media[key] = new
        for rid, rel in src.rels.items():
            if rel.is_external:
                set_rel(new, rid, rel.reltype, rel.target_ref, True)
            elif rel.reltype not in (RT.SLIDE, RT.NOTES_SLIDE, RT.OFFICE_DOCUMENT):   # 不顺着别的页走
                set_rel(new, rid, rel.reltype, self.copy(rel.target_part))
        return new


_CONTENT_RT = ("/image", "/chart", "/hyperlink", "/media", "/video", "/audio", "/slide", "/oleObject", "/package")


def prune_rels(slide_part: Any) -> None:
    """页内合并换掉形状后, 页 XML 里不再引用的图片 / 图表 / 链接关系摘掉 (不然旧图还留在包里)。"""
    used = {v for e in slide_part._element.iter() if isinstance(e.tag, str)
            for k, v in e.attrib.items() if etree.QName(k).namespace == R_NS}
    for rid, rel in list(slide_part.rels.items()):
        if rid not in used and rel.reltype.endswith(_CONTENT_RT):
            slide_part.rels.pop(rid)


def relink(slide_part: Any, resolve: Callable[[Any], Any]) -> int:
    """页里链到别的页的超链接改指到合并版里那一页; 那页不在了就去掉链接。返去掉了几处。"""
    gone: set[str] = set()
    for rid, rel in list(slide_part.rels.items()):
        if rel.is_external or rel.reltype != RT.SLIDE:
            continue
        tgt = resolve(rel.target_part)
        if tgt is None:
            slide_part.rels.pop(rid)
            gone.add(rid)
        elif tgt is not rel.target_part:
            set_rel(slide_part, rid, RT.SLIDE, tgt)
    return drop_refs(slide_part._element, gone) if gone else 0


def set_slide_list(dest: Dest, entries: list[tuple[int, Any]]) -> None:
    """按 [(页编号, 页部件)] 重写 sldIdLst; 不再用的旧页从 presentation 的 rels 里摘掉 (保存时就不写了),
    自定义放映里引用旧页的改指新页 / 去掉。"""
    part, el = dest.part, dest.part._element
    lst = el.get_or_add_sldIdLst()
    old = {int(e.get("id")): e.get(qn("r:id")) for e in list(lst)}
    rid_of_part = {id(r.target_part): rid for rid, r in part.rels.items() if r.reltype == RT.SLIDE}
    for e in list(lst):
        lst.remove(e)
    keep: dict[str, str] = {}   # 旧 rId → 新 rId
    new_rids = []
    for sid, sp in entries:
        rid = rid_of_part.get(id(sp)) or part.relate_to(sp, RT.SLIDE)
        new_rids.append(rid)
        e = OxmlElement("p:sldId")
        e.set("id", str(sid))
        e.set(qn("r:id"), rid)
        lst.append(e)
        if sid in old:
            keep[old[sid]] = rid
    used = set(new_rids)
    for e in list(el.iter(qn("p:sld"))):        # 自定义放映 (custShowLst)
        rid = e.get(qn("r:id"))
        if rid in used:
            continue
        if keep.get(rid):
            e.set(qn("r:id"), keep[rid])
        else:
            _remove(e)
    for rid in [r for r, rel in part.rels.items() if rel.reltype == RT.SLIDE and r not in used]:
        part.rels.pop(rid)
    part.rename_slide_parts(new_rids)


def set_sections(dest: Dest, base_secs: list[tuple[str, str, list[int]]], order: list[int],
                 new_ids: set[int]) -> None:
    """底稿分过节的, 按合并后的页序重写分节: 原有的页留在原来的节, 新页跟它前一页同节;
    挪页后一个节被拆开的, 拆出来的那段另起一个同名节 (分节必须是连续的一段)。没页的空节放回原来的前后位置。"""
    lst = dest.part._element.find(f".//{{{P14_NS}}}sectionLst")
    if lst is None or not base_secs:
        return
    sec_of = {sid: i for i, (_, _, ids) in enumerate(base_secs) for sid in ids}
    runs: list[tuple[int, list[int]]] = []
    for sid in order:
        i = None if sid in new_ids else sec_of.get(sid)
        if i is None:
            i = runs[-1][0] if runs else 0
        if runs and runs[-1][0] == i:
            runs[-1][1].append(sid)
        else:
            runs.append((i, [sid]))
    for i, (_, _, ids) in enumerate(base_secs):
        if ids:
            continue
        at = max((k + 1 for k, (j, _) in enumerate(runs) if j < i), default=0)
        runs.insert(at, (i, []))
    for sec in list(lst):
        lst.remove(sec)
    seen: set[int] = set()
    for i, ids in runs:
        name, sec_id = base_secs[i][0], base_secs[i][1]
        if i in seen or not sec_id:
            sec_id = "{%s}" % str(uuid.uuid4()).upper()
        seen.add(i)
        sec = etree.SubElement(lst, f"{{{P14_NS}}}section", name=name, id=sec_id)
        ids_el = etree.SubElement(sec, f"{{{P14_NS}}}sldIdLst")
        for sid in ids:
            etree.SubElement(ids_el, f"{{{P14_NS}}}sldId", id=str(sid))
