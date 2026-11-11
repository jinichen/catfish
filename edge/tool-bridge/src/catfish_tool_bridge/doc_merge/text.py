"""md / txt / 知识库页面的合并: 按行三方合并 (跟 git merge-file 同一个原理, 验收拿它当对照)。

- 编码: utf-8 (带不带 BOM 都认) / gb18030 (Windows 记事本老文件), 写回保持原编码、原换行 (\\r\\n / \\n)、末尾有没有换行。
- 单元 = 一行 (不含行尾换行符; 换行风格不同不算改动)。
- 冲突里的「拼合建议」: 冲突那几行按字拼 (core.combine_text), 两人改的是同一行里不同的地方时能拼上。
- 没有「修订痕迹」这种东西: tracked 跟 clean 一样。
"""
from __future__ import annotations

from typing import Any

from . import Ctx, Return
from .core import Seg, Unit, combine_text, merge_seq, resolve, stats


def decode(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8"), "utf-8-sig"
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("gb18030", errors="replace"), "gb18030"


def _style(s: str) -> tuple[str, bool]:
    nl = "\r\n" if s.count("\r\n") * 2 >= s.count("\n") and "\r\n" in s else "\n"
    return nl, s.endswith(("\n", "\r"))


def units(s: str) -> list[Unit]:
    return [Unit(line, line) for line in s.splitlines()]


def _joined(us: list[Unit]) -> str:
    return "\n".join(u.text for u in us)


def _where(seg: Seg, base: list[Unit]) -> str:
    if seg.start == seg.end:
        return f"第 {seg.start} 行后面"
    return f"第 {seg.start + 1} 行" if seg.end - seg.start == 1 else f"第 {seg.start + 1}–{seg.end} 行"


def _segs(base: bytes, returns: list[Return]) -> tuple[list[Unit], list[Seg], list[str], str, str, bool]:
    text, enc = decode(base)
    nl, tail = _style(text)
    bu = units(text)
    vers = {r.who: units(decode(r.data)[0]) for r in returns}
    same = [w for w, v in vers.items() if [u.sig for u in v] == [u.sig for u in bu]]
    return bu, merge_seq(bu, vers, [r.who for r in returns]), same, enc, nl, tail


def conflict_view(seg: Seg, where: str, order: list[str]) -> dict[str, Any]:
    base_text = _joined(seg.old)
    opts = [{"key": o.key, "who": o.who, "text": _joined(o.units)} for o in seg.options]
    combined = combine_text(base_text, {o.key: _joined(o.units) for o in seg.options}, [o.key for o in seg.options])
    return {"cid": seg.cid, "where": where, "kind": "text", "base": base_text, "options": opts, "combined": combined}


def analyze(base: bytes, returns: list[Return], ctx: Ctx) -> dict[str, Any]:
    bu, segs, same, *_ = _segs(base, returns)
    order = [r.who for r in returns]
    return {"conflicts": [conflict_view(s, _where(s, bu), order) for s in segs if s.kind == "conflict"],
            "changes": stats(segs), "same": same, "warnings": []}


def render(base: bytes, returns: list[Return], decisions: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    bu, segs, same, enc, nl, tail = _segs(base, returns)
    done = resolve(segs, decisions, ctx.owner, lambda t, _s: units(t))
    lines = [u.text for s in done for u in (s.new if s.kind == "change" else s.old)]
    out = nl.join(lines) + (nl if tail and lines else "")
    data = out.encode(enc)
    return {"tracked": data, "clean": data, "changes": stats(done), "warnings": []}
