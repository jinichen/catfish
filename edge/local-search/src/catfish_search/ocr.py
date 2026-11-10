"""读图片 / 扫描版 PDF 里的字 (11/10)。全在本机跑, 不上传。

- macOS: Companion 带的 catfish-ocr (系统自带 Vision 识别, swift/catfish-ocr.swift)。Companion 起
  local-search 时把路径放进环境变量 CATFISH_OCR_BIN。
- Windows: 系统自带的 Windows.Media.Ocr, 由同目录的 ocr_windows.ps1 调。要装了中文的「光学字符识别」
  语言功能 (中文版 Windows 一般自带); 没有就报 no_zh_ocr, 不拿英文识别硬读中文。

两边都是**常驻一个进程**、从标准输入一行喂一个图片路径: macOS 每个进程加载识别模型要约 15 秒,
之后每张约 0.4 秒, 一张图起一个进程就太慢了。闲 IDLE_SEC 秒没活就关掉。

PDF 由这里先用 pypdfium2 把没字的页渲染成图片, 再交给识别。
"""
from __future__ import annotations

import json
import logging
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

logger = logging.getLogger("catfish.search.ocr")

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".heic", ".heif", ".webp", ".tif", ".tiff", ".bmp", ".gif"}
#: 一份 PDF 最多识别这么多页 (每页约 0.4 秒, 200 页 ≈ 80 秒)
MAX_PDF_OCR_PAGES = 200
#: 单张图最多等这么久
PER_IMAGE_TIMEOUT_SEC = 60.0
IDLE_SEC = 120.0
#: 渲染 PDF 页的倍数 (72dpi × 2 ≈ 144dpi, 正文识别够了, 图不至于太大)
PDF_RENDER_SCALE = 2.0

_CJK_GAP = re.compile(r"(?<=[\u3400-\u9fff\uff00-\uffef\u3000-\u303f]) +(?=[\u3400-\u9fff\uff00-\uffef\u3000-\u303f])")

_MAC_FALLBACKS = (
    "/Applications/Catfish Companion.app/Contents/Resources/catfish-ocr",
    "/Applications/鲶鱼 Companion.app/Contents/Resources/catfish-ocr",
)


class OcrUnavailable(Exception):
    """这台机器没法识别 (没有 helper / 老系统 / Windows 没装中文识别)。reason 给面板显示。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _command() -> list[str] | None:
    if sys.platform == "darwin":
        cand = [os.environ.get("CATFISH_OCR_BIN", ""), *_MAC_FALLBACKS]
        for c in cand:
            if c and os.path.isfile(c) and os.access(c, os.X_OK):
                return [c, "--stdin"]
        return None
    if os.name == "nt":
        script = Path(__file__).with_name("ocr_windows.ps1")
        if script.exists():
            return ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                    "-File", str(script)]
        return None
    return None


class _Resident:
    """常驻识别进程。线程安全: 同一时刻只送一张图。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._lines: queue.Queue = queue.Queue()
        self._last_used = 0.0
        self._dead_reason: str | None = None
        self._reaper: threading.Thread | None = None

    def _start(self) -> None:
        cmd = _command()
        if cmd is None:
            raise OcrUnavailable("no_helper")
        kw = {}
        if os.name == "nt":
            kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._proc = subprocess.Popen(  # noqa: S603
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", bufsize=1, **kw,
        )
        self._lines = queue.Queue()
        proc = self._proc

        def _pump() -> None:
            for line in proc.stdout:  # type: ignore[union-attr]
                self._lines.put(line)
            self._lines.put(None)

        threading.Thread(target=_pump, name="catfish-ocr-reader", daemon=True).start()
        if self._reaper is None or not self._reaper.is_alive():
            self._reaper = threading.Thread(target=self._reap_idle, name="catfish-ocr-idle", daemon=True)
            self._reaper.start()

    def _reap_idle(self) -> None:
        while True:
            time.sleep(10)
            with self._lock:
                if self._proc is None:
                    return
                if time.time() - self._last_used > IDLE_SEC:
                    self._stop()
                    return

    def _stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.stdin.close()  # type: ignore[union-attr]
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            proc.kill()

    def recognize(self, path: str) -> str | None:
        """识别一张图。返回文字 (可能为空串); 这张图读不了返回 None; 整机没法识别抛 OcrUnavailable。"""
        with self._lock:
            if self._dead_reason:
                raise OcrUnavailable(self._dead_reason)
            if self._proc is None or self._proc.poll() is not None:
                self._start()
            self._last_used = time.time()
            try:
                self._proc.stdin.write(path.replace("\n", " ") + "\n")  # type: ignore[union-attr]
                self._proc.stdin.flush()  # type: ignore[union-attr]
                line = self._lines.get(timeout=PER_IMAGE_TIMEOUT_SEC)
            except (OSError, queue.Empty):
                logger.warning("识字进程没响应, 重启: %s", path)
                self._stop()
                return None
            self._last_used = time.time()
            if line is None:
                self._proc = None
                return None
            try:
                d = json.loads(line)
            except ValueError:
                return None
            if d.get("ok"):
                text = d.get("text") or ""
                if os.name == "nt":
                    # Windows 识别把每个汉字当一个词, 中间夹空格 ("北 京 福 富"): 汉字之间的空格去掉
                    text = _CJK_GAP.sub("", text)
                return text
            err = str(d.get("error") or "")
            # 整机层面的「没法识别」: 记下来, 后面的图直接跳过, 不再一张张试
            if err in ("needs macOS 11", "no_zh_ocr", "no_winrt"):
                self._dead_reason = err
                self._stop()
                raise OcrUnavailable(err)
            logger.debug("识别失败 %s: %s", path, err)
            return None


_RESIDENT = _Resident()


def available() -> bool:
    return _command() is not None and not _RESIDENT._dead_reason


def ocr_image(path: Path) -> str | None:
    return _RESIDENT.recognize(str(path))


def ocr_pdf_pages(path: Path, pages: list[int]) -> dict[int, str]:
    """把 PDF 的这些页 (0 起) 渲染成图片再识别。返回 {页号: 文字}。"""
    import pypdfium2 as pdfium  # noqa: PLC0415

    out: dict[int, str] = {}
    pdf = pdfium.PdfDocument(str(path))
    try:
        with tempfile.TemporaryDirectory(prefix="catfish-ocr-") as tmp:
            for i in pages[:MAX_PDF_OCR_PAGES]:
                page = pdf[i]
                try:
                    img = Path(tmp) / f"p{i}.png"
                    page.render(scale=PDF_RENDER_SCALE).to_pil().save(img)
                finally:
                    page.close()
                text = _RESIDENT.recognize(str(img))
                img.unlink(missing_ok=True)
                if text:
                    out[i] = text
    finally:
        pdf.close()
    return out


def meaningful(text: str | None, at_least: int = 5) -> bool:
    """识别出来的东西算不算字: 中文 / 字母 / 数字够 at_least 个。"""
    if not text:
        return False
    return sum(1 for c in text if c.isalnum()) >= at_least
