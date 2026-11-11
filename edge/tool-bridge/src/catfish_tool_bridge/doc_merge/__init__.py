"""群里一起改文档的合并引擎 (docs/DOC-REVISE-MERGE-PLAN.md)。

每种格式一个模块 (word / excel / slides / table_csv / text; 不叫 docx.py, 免得跟 python-docx 撞名), 都长一个样 (这里统一分派):

    analyze(base: bytes, returns: list[Return], ctx: Ctx) -> dict
        {"conflicts": [Conflict...], "changes": {email: 合进去几处}, "same": [没改动的 email], "warnings": [str...]}
        Conflict = {"cid", "where" (给人看的位置: 「第 3 节第 2 段」/「报价 B5」/「第 4 页」),
                    "kind": "text" | "value" | "slide",
                    "base": 原文 (文字), "options": [{"key", "who": [email...], "text"}...],
                    "combined": 按字拼好的建议稿或 None (只有 text 类)}
    render(base, returns, decisions, ctx) -> dict
        {"tracked": bytes (带修订痕迹 / 批注 / 备注的那份; 没有这种就跟 clean 一样), "clean": bytes,
         "changes": {...}, "warnings": [...]}
        decisions = {cid: {"pick": 选项 key | "base" | "text", "text": 发起人写的}}; 没定完抛 core.Undecided。

同样的输入, analyze 和 render 算出的冲突 cid 必须一样 (render 是重算一遍再按 cid 套决定)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any

from .core import Undecided

__all__ = ["Ctx", "Return", "Undecided", "analyze", "fmt_of", "render"]

FORMATS = {".docx": "docx", ".xlsx": "xlsx", ".pptx": "pptx", ".md": "text", ".markdown": "text", ".txt": "text",
           ".csv": "csv"}
OLD_OFFICE = {".doc": ".docx", ".xls": ".xlsx", ".ppt": ".pptx", ".wps": ".docx", ".et": ".xlsx", ".dps": ".pptx"}
MAX_BYTES = 50 * 1024 * 1024   # 合并读进内存; 再大的只能整份选用


@dataclass
class Return:
    who: str          # 交回人邮箱
    data: bytes
    at: str = ""      # 交回时间 (ISO), Word 修订的时间就用它


@dataclass
class Ctx:
    owner: str                                       # 发起人邮箱 (他自己定的冲突算他改的)
    names: dict[str, str] = field(default_factory=dict)   # 邮箱 → 名字 (修订作者 / 批注署名)
    now: str = ""                                    # 发起人定冲突的时间 (ISO)

    def name(self, email: str) -> str:
        return self.names.get(email) or email.split("@")[0]


def fmt_of(filename: str) -> str | None:
    """能自动合并的格式 → 模块名; 其它 (pdf / 图片 / 老 Office 格式 …) None = 只能整份选用。"""
    return FORMATS.get(PurePath(filename).suffix.lower())


def _mod(fmt: str) -> Any:
    if fmt == "docx":
        from . import word as m  # noqa: PLC0415
    elif fmt == "xlsx":
        from . import excel as m  # noqa: PLC0415
    elif fmt == "pptx":
        from . import slides as m  # noqa: PLC0415
    elif fmt == "csv":
        from . import table_csv as m  # noqa: PLC0415
    elif fmt == "text":
        from . import text as m  # noqa: PLC0415
    else:
        raise ValueError(f"这种格式不能自动合并: {fmt}")
    return m


def analyze(fmt: str, base: bytes, returns: list[Return], ctx: Ctx) -> dict[str, Any]:
    return _mod(fmt).analyze(base, returns, ctx)


def render(fmt: str, base: bytes, returns: list[Return], decisions: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    return _mod(fmt).render(base, returns, decisions, ctx)
