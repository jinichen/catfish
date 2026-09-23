"""合成的微信导出 ZIP —— 结构照两份真实导出 (私聊 / 8 人群聊) 造, 内容全是假的。

真实导出只在本地做过一次端到端, 不进仓库。
"""
from __future__ import annotations

from pathlib import Path
import zipfile

MEDIA_DIR = "聊天记录内的图片、视频和文件"


def message(sender: str, when: str, body: str) -> str:
    return f"·{sender}\n{when}\n{body}\n\n"


def make_export(
    path: Path,
    messages: list[tuple[str, str, str]],
    media: dict[str, bytes] | None = None,
    transcript_name: str = "聊天记录.txt",
    encoding: str = "utf-8",
) -> Path:
    body = "".join(message(*item) for item in messages)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(transcript_name, body.encode(encoding))
        for name, payload in (media or {}).items():
            archive.writestr(f"{MEDIA_DIR}/{name}", payload)
    return path


GROUP_A = [
    ("测试甲", "2026年9月14日 15:06", "大家把材料发一下"),
    ("测试乙", "2026年9月14日 15:07", "[文件] 年审材料-盖章版.pdf"),
    ("测试丙", "2026年9月14日 15:07", "[图片] 微信图片_202609141507_1.jpg"),
    ("测试丁", "2026年9月14日 15:08", "[文件] 原始底稿.rar"),
    ("测试甲", "2026年9月14日 15:09", "[小程序] 腾讯文档"),
    ("测试乙", "2026年9月14日 15:10", "[OK]"),
    ("测试丙", "2026年9月14日 15:11", "@测试甲\u2005收到\n第二行说明"),
    ("我自己", "2026年9月14日 15:12", "[语音通话]"),
]
GROUP_A_MEDIA = {
    "年审材料-盖章版.pdf": b"%PDF-1.4 fake",
    "微信图片_202609141507_1.jpg": b"\xff\xd8fake",
}
