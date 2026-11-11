"""在 docx / xlsx / pptx 里埋一个「这是哪一轮修改」的记号 (docs/DOC-REVISE-MERGE-PLAN.md §3.2)。

记号放在 OOXML 的自定义文档属性 `docProps/custom.xml` 里, 名字 `catfish_rev`, 值 `r_…:v1` (只有编号, 不含姓名 / 邮箱)。
Word / WPS / Excel / PowerPoint / LibreOffice 另存都会保留自定义属性 —— 同事改完直接把文件拖进群里, 也认得出是交回稿。
发给外部的干净版用 strip() 去掉记号 (别的自定义属性原样留着)。

只动三个地方: docProps/custom.xml、[Content_Types].xml 的 Override、_rels/.rels 的关系; 其余 zip 条目原样照抄。
"""
from __future__ import annotations

import io
import re
import zipfile
from xml.etree import ElementTree as ET

NAME = "catfish_rev"
CUSTOM = "docProps/custom.xml"
_NS = "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
_VT = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"
_CT = "application/vnd.openxmlformats-officedocument.custom-properties+xml"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties"
_FMTID = "{D5CDD505-2E9C-101B-9397-08002B2CF9AE}"
_VALUE = re.compile(r"r_\d{8}_[0-9a-f]{6,16}:v\d{1,4}")
ET.register_namespace("", _NS)
ET.register_namespace("vt", _VT)


def _zip(data: bytes) -> zipfile.ZipFile | None:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return None
    return z if "[Content_Types].xml" in z.namelist() else None


def read(data: bytes) -> str | None:
    """文件里的记号 (r_…:v1), 没有 / 不是 OOXML → None。"""
    z = _zip(data)
    if not z or CUSTOM not in z.namelist():
        return None
    try:
        root = ET.fromstring(z.read(CUSTOM))
    except ET.ParseError:
        return None
    for p in root.findall(f"{{{_NS}}}property"):
        if p.get("name") == NAME:
            v = "".join(p.itertext()).strip()
            return v if _VALUE.fullmatch(v) else None
    return None


def _rewrite(data: bytes, value: str | None) -> bytes:
    z = _zip(data)
    if z is None:
        return data
    names = z.namelist()
    if CUSTOM in names:
        root = ET.fromstring(z.read(CUSTOM))
    else:
        if value is None:
            return data
        root = ET.Element(f"{{{_NS}}}Properties")
    for p in list(root.findall(f"{{{_NS}}}property")):
        if p.get("name") == NAME:
            root.remove(p)
    if value is not None:
        pids = [int(p.get("pid") or 1) for p in root.findall(f"{{{_NS}}}property")]
        p = ET.SubElement(root, f"{{{_NS}}}property", {"fmtid": _FMTID, "pid": str(max(pids + [1]) + 1), "name": NAME})
        ET.SubElement(p, f"{{{_VT}}}lpwstr").text = value
    custom = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + ET.tostring(root, encoding="utf-8")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as w:
        for info in z.infolist():
            body = z.read(info.filename)
            if info.filename == CUSTOM:
                continue
            if info.filename == "[Content_Types].xml" and _CT.encode() not in body:
                body = body.replace(b"</Types>", f'<Override PartName="/{CUSTOM}" ContentType="{_CT}"/></Types>'.encode())
            if info.filename == "_rels/.rels" and _REL.encode() not in body:
                body = body.replace(b"</Relationships>",
                                    f'<Relationship Id="rIdCatfishRev" Type="{_REL}" Target="{CUSTOM}"/></Relationships>'.encode())
            w.writestr(info, body)
        w.writestr(CUSTOM, custom)
    return out.getvalue()


def embed(data: bytes, value: str) -> bytes:
    if not _VALUE.fullmatch(value):
        raise ValueError("记号格式不对")
    return _rewrite(data, value)


def strip(data: bytes) -> bytes:
    return _rewrite(data, None) if read(data) else data
