"""鲶鱼字体加载器.

职责:
  1. 扫描 fonts/customer/ + fonts/opensource/ 发现可用字体
  2. 给 skill / 业务代码暴露 find_font(name) 接口
  3. (可选, 默认关) 把字体注册到系统 (macOS: cp 到 ~/Library/Fonts/), 让本机渲染时也能用

设计原则:
  - **永远不分发商业字体二进制** — opensource/ 只放开源字体, customer/ 由客户 IT 填
  - 字体表是惰性加载的, 不做就不耗时
  - macOS 系统注册需要用户同意 (companion-app 弹窗), 这个 module 只暴露能力, 不主动调用

公开接口:
  - discover() -> dict[str, Path]    # 扫一次, 返回 {字体名: 文件路径}
  - find_font(name) -> Path | None   # 单个查找
  - install_to_user_fonts(paths)     # macOS 拷到 ~/Library/Fonts/
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from functools import lru_cache
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

# fonts/ 目录的 root (本文件就在 fonts/loader.py)
FONTS_ROOT = Path(__file__).resolve().parent

# 字体子目录 (按搜索优先级排序)
SEARCH_DIRS = [
    FONTS_ROOT / "customer",  # 客户授权字体, 优先
    FONTS_ROOT / "opensource",  # 我们打包的开源 fallback
]

# 支持的扩展名
FONT_EXTS = {".ttf", ".otf", ".ttc", ".woff", ".woff2"}


# ── 字体名 → 文件名映射 ─────────────────────────────────────────
# 文件名不一定跟字体内置名一致 (例: FZXBSJW.TTF 是"方正小标宋简体"),
# 用户调用 find_font 时传字体名, 这里翻译成文件名 substring 模糊匹配.
#
# 客户加新字体后在 customer/ 自己改名让 substring 能命中, 或者
# 把映射追加到 ~/.catfish/font-aliases.yaml (Phase 2).

# 字体名 → 文件名 substring (lower-case 比对)
NAME_TO_SUBSTRING: dict[str, list[str]] = {
    # 商业字体 (客户在 customer/ 填)
    "方正小标宋简体": ["fzxbsj", "xiaobiaosong", "方正小标宋"],
    "仿宋_GB2312": ["fangsong_gb2312", "fangsong-gb2312", "fs-gb2312"],
    "楷体_GB2312": ["kaiti_gb2312", "kaiti-gb2312"],
    "黑体": ["simhei", "黑体"],  # 系统多半已装, 这里只是兜底
    "宋体": ["simsun", "宋体"],
    # 开源字体 (我们在 opensource/ 打包 fallback)
    "思源宋体": ["sourcehanserif", "noto serif cjk"],
    "思源黑体": ["sourcehansans", "noto sans cjk"],
    "文泉驿仿宋": ["wqy-fangsong", "wqy fangsong"],
}


# ── 扫描 ────────────────────────────────────────────────────────


def _list_font_files(d: Path) -> Iterable[Path]:
    """枚举一个目录下所有字体文件 (递归)."""
    if not d.exists() or not d.is_dir():
        return
    for p in d.rglob("*"):
        if p.is_file() and p.suffix.lower() in FONT_EXTS:
            yield p


@lru_cache(maxsize=1)
def discover() -> dict[str, Path]:
    """扫描所有 SEARCH_DIRS, 返回 {字体名: 文件 Path}.

    返回的 dict 包含两类 key:
      - 配置在 NAME_TO_SUBSTRING 里的"友好字体名" (例: "方正小标宋简体")
      - 字体文件 stem (文件名去后缀, 不带路径)

    优先级: SEARCH_DIRS 列表顺序 — customer/ 优先于 opensource/
    """
    result: dict[str, Path] = {}

    for search_dir in SEARCH_DIRS:
        for font_path in _list_font_files(search_dir):
            stem = font_path.stem
            stem_lower = stem.lower()

            # 1) 文件 stem 直接登记 (兜底, 保证至少能查得到)
            if stem not in result:
                result[stem] = font_path

            # 2) 模糊匹配登记到友好字体名
            for friendly_name, substrings in NAME_TO_SUBSTRING.items():
                if friendly_name in result:
                    continue  # 已被高优先级目录命中, 不覆盖
                if any(s.lower() in stem_lower for s in substrings):
                    result[friendly_name] = font_path

    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(
            "fonts/loader: discovered %d entries from %s",
            len(result),
            [str(d) for d in SEARCH_DIRS],
        )

    return result


def find_font(name: str) -> Path | None:
    """给定字体名, 返回字体文件路径 — 找不到返回 None.

    name 可以是友好字体名 ("方正小标宋简体") 或文件 stem ("FZXBSJW").
    """
    table = discover()
    return table.get(name)


def list_available() -> list[str]:
    """所有当前可用的字体名."""
    return sorted(discover().keys())


# ── 系统注册 (macOS / Linux) ────────────────────────────────────
# python-docx 写 .docx 时不需要这一步 — 字体名写到 XML 即可,
# Word 打开时找系统字体. 但**鲶鱼内的预览功能 / .docx → .pdf 转换**
# 需要本机有字体, 这时才调用以下函数.


def _user_fonts_dir() -> Path | None:
    """获取当前 OS 的用户级字体目录."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Fonts"
    if sys.platform.startswith("linux"):
        return Path.home() / ".fonts"
    if sys.platform == "win32":
        # Win 用户级字体目录: AppData\Local\Microsoft\Windows\Fonts
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / "Microsoft" / "Windows" / "Fonts"
    return None


def install_to_user_fonts(paths: Iterable[Path] | None = None) -> list[Path]:
    """把字体拷贝到当前用户的字体目录.

    paths: 要装的字体文件列表; 不传则装 discover() 全部.
    返回: 成功 install 的目标路径列表.

    注意:
      - macOS / Linux 不需要管理员权限 (装到 ~/Library/Fonts/ / ~/.fonts/)
      - 已存在的同名文件**跳过** (不覆盖, 防止 stomp 用户已装的)
      - **不在这个 module 里弹用户同意框** — UI 责任 in companion-app
    """
    target_dir = _user_fonts_dir()
    if target_dir is None:
        logger.warning("fonts/loader: 当前 OS 未支持字体安装, 跳过")
        return []

    target_dir.mkdir(parents=True, exist_ok=True)

    if paths is None:
        # 装 discover 出来的全部, 但去重 (一个文件可能被多个 friendly name 指向)
        paths = list(set(discover().values()))

    installed: list[Path] = []
    for src in paths:
        if not src.exists():
            continue
        dst = target_dir / src.name
        if dst.exists():
            logger.info("fonts/loader: %s 已存在, 跳过", dst.name)
            continue
        try:
            shutil.copy2(src, dst)
            installed.append(dst)
            logger.info("fonts/loader: installed %s -> %s", src.name, dst)
        except Exception as e:
            logger.error("fonts/loader: 装 %s 失败: %s", src.name, e)

    # macOS 装完后, Font Book / 应用程序需要重新启动才能识别 — 不在这里 trigger
    return installed


# ── CLI (运维 debug 用) ─────────────────────────────────────────


def _main():
    """python -m catfish.fonts.loader — 列出当前发现的字体, 调试用."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    table = discover()
    if not table:
        print("(没有发现字体. 把字体放到 fonts/customer/ 或 fonts/opensource/)")
        return
    print(f"发现 {len(table)} 个字体:")
    for name, path in sorted(table.items()):
        print(f"  {name:30s} → {path}")


if __name__ == "__main__":
    _main()
