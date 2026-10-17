"""catfish_browser_download —— 点一下, 等文件下完, 交回路径 (10/17, docs/WORKFLOW-SKILL-PLAN.md §6)。

以前只能 catfish_browser_click 点「下载」, 文件落到 Chrome 默认目录, 谁也不知道叫什么、下完没有;
凝固出来的「在网页上下文件」skill 只会点按钮, 后面的步骤拿不到文件。

现在: Playwright `page.expect_download()` 包住这一下点击 → 等下载完成 → 存到
`~/.catfish/downloads/<日期>/<原文件名>` (重名加序号) → 返回 {path, filename, size}。
run_skill 会把 path 收进 files, 工作流后面的步骤用 `${steps.<id>.path}` 取。
跟 click 一样走员工的 Catfish Chrome (connect_over_cdp), 一样有硬超时兜底。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from . import wiki_files


def downloads_dir() -> Path:
    d = wiki_files._catfish_home() / "downloads" / datetime.now().strftime("%Y-%m-%d")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _unique(dest_dir: Path, name: str) -> Path:
    safe = "".join(c for c in (name or "download") if c not in '\\/:*?"<>|').strip() or "download"
    p = dest_dir / safe
    stem, suf, n = p.stem, p.suffix, 2
    while p.exists():
        p = dest_dir / f"{stem}-{n}{suf}"
        n += 1
    return p


def browser_download(args: Dict[str, Any]) -> Dict[str, Any]:
    from .catfish_tools_browser import _run_with_hard_timeout  # noqa: PLC0415 —— 打桩打的是那份 (见 actions 文件头)
    return _run_with_hard_timeout(_browser_download_impl, args)


def _browser_download_impl(args: Dict[str, Any]) -> Dict[str, Any]:
    from .catfish_tools_browser import _connect_playwright_browser, _import_playwright  # noqa: PLC0415

    selector = (args.get("selector") or "").strip()
    coordinates = args.get("coordinates")
    timeout_ms = max(5_000, min(int(float(args.get("timeout_seconds") or 60.0) * 1000), 300_000))
    if not selector and not coordinates:
        return {"type": "error", "ok": False, "error": "selector 跟 coordinates 至少传一个 (要点的是「下载」按钮 / 链接)"}
    xy = None
    if coordinates:
        try:
            xy = (int(coordinates[0]), int(coordinates[1])) if isinstance(coordinates, (list, tuple)) else \
                (int(coordinates["x"]), int(coordinates["y"]))
        except (KeyError, TypeError, ValueError, IndexError):
            return {"type": "error", "ok": False, "error": "coordinates 要 [x, y] 像素整数"}
    try:
        sync_playwright = _import_playwright()
    except RuntimeError as e:
        return {"type": "error", "ok": False, "error": str(e)}
    try:
        with sync_playwright() as p:
            try:
                _browser, _context, page = _connect_playwright_browser(p)
            except RuntimeError as e:
                return {"type": "error", "ok": False, "error": str(e)}
            try:
                with page.expect_download(timeout=timeout_ms) as info:
                    if selector:
                        page.click(selector, timeout=min(timeout_ms, 30_000))
                    else:
                        page.mouse.click(*xy)
                dl = info.value
                failure = dl.failure()
                if failure:
                    return {"type": "error", "ok": False, "error": f"下载没完成: {failure}"}
                dest = _unique(downloads_dir(), dl.suggested_filename)
                dl.save_as(str(dest))
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                if "Timeout" in msg or "timeout" in msg:
                    return {"type": "error", "ok": False,
                            "error": (f"点了之后 {timeout_ms // 1000} 秒内没有开始下载。点的是不是下载按钮? "
                                      "有的系统点一下先弹窗 / 新开页面, 那就先 click 到真正的下载链接再用这个。")}
                return {"type": "error", "ok": False, "error": f"下载失败: {type(e).__name__}: {e}"}
            size = dest.stat().st_size
            return {"type": "ok", "ok": True, "path": str(dest), "filename": dest.name, "size": size,
                    "current_url": page.url,
                    "summary": f"✓ 下载好了: {dest.name} ({size // 1024} KB), 存在 {dest.parent}"}
    except Exception as e:  # noqa: BLE001
        return {"type": "error", "ok": False, "error": f"playwright 下载异常: {type(e).__name__}: {e}"}


def dispatch(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    return browser_download(args)
