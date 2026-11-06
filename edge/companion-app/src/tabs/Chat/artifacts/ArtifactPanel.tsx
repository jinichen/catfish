/**
 * 对话右侧成果区 (11/07 鸿波「先做成果区」)。
 *
 * 小鲶这段对话里生成 / 提到的网页、文档、表格、图片在聊天右边直接看:
 *   - 一轮回答结束, 这一轮里真写出来的文件自动在右边打开 (ChatTab 里判断);
 *     聊天里的文件胶囊点「预览」、正文里的文件名点一下, 也在右边打开。
 *   - 顶上能切换这段对话里的其他成果; 文件被小鲶改了 (每 2 秒看一次修改时间) 右边自动刷新。
 *   - 左边缘拖动调宽, 宽度记在本机。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowClockwise, ArrowSquareOut, FolderOpen, X } from "@phosphor-icons/react";
import { useArtifactStore, ARTIFACT_MIN_WIDTH } from "../../../store/artifacts";
import { artifactStat, type ArtifactInfo } from "../../../lib/tauri_artifacts";
import { artifactLabel } from "../../../lib/artifactKinds";
import { basename, fileEmoji } from "../../../lib/path_detect";
import { formatBytes } from "../../../lib/format";
import { fileManagerName } from "../../../lib/platformLabels";
import { openFile, revealInFinder } from "../../../lib/tauri";
import { HtmlView, ImageView, MarkdownView, Notice, PdfView, TableView, TextView } from "./ArtifactViews";
import OfficeView from "./OfficeView";

const POLL_MS = 2000;
/** 聊天区至少留这么宽, 右栏拖不过来 */
const CHAT_MIN_WIDTH = 400;

function timeLabel(ms: number): string {
  if (!ms) return "";
  const d = new Date(ms);
  const sameDay = d.toDateString() === new Date().toDateString();
  const hm = d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  return sameDay ? hm : `${d.getMonth() + 1}/${d.getDate()} ${hm}`;
}

export default function ArtifactPanel({ paths }: { paths: string[] }) {
  const { path, width, reloadKey, openArtifact, close, setWidth, reload } = useArtifactStore();
  const [info, setInfo] = useState<ArtifactInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [dragWidth, setDragWidth] = useState<number | null>(null);
  const asideRef = useRef<HTMLElement>(null);

  // 打开 / 切换文件时读一次, 之后每 2 秒看修改时间, 变了才换 info (显示区据此重读)
  useEffect(() => {
    if (!path) return;
    let cancelled = false;
    setInfo(null);
    setError(null);
    setActionError(null);
    const check = () =>
      artifactStat(path)
        .then((next) => {
          if (cancelled) return;
          setError(null);
          setInfo((prev) => (prev && prev.mtime_ms === next.mtime_ms && prev.size === next.size ? prev : next));
        })
        .catch((e) => !cancelled && setError(String(e)));
    void check();
    const t = window.setInterval(check, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(t);
    };
  }, [path]);

  const maxWidth = useCallback(() => {
    const aside = asideRef.current;
    const workspace = aside?.parentElement;
    if (!aside || !workspace) return 1200;
    const others = Array.from(workspace.children).reduce(
      (sum, el) => (el === aside || el.classList.contains("chat-workspace__main") ? sum : sum + (el as HTMLElement).offsetWidth),
      0,
    );
    return Math.max(ARTIFACT_MIN_WIDTH, workspace.clientWidth - others - CHAT_MIN_WIDTH);
  }, []);

  const startResize = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    const startX = e.clientX;
    const startWidth = asideRef.current?.offsetWidth ?? width;
    const limit = maxWidth();
    const target = e.currentTarget;
    target.setPointerCapture(e.pointerId);
    let latest = startWidth;
    const move = (ev: PointerEvent) => {
      latest = Math.min(limit, Math.max(ARTIFACT_MIN_WIDTH, startWidth + (startX - ev.clientX)));
      setDragWidth(latest);
    };
    const up = () => {
      target.removeEventListener("pointermove", move);
      target.removeEventListener("pointerup", up);
      target.removeEventListener("pointercancel", up);
      setWidth(latest);
      setDragWidth(null);
    };
    target.addEventListener("pointermove", move);
    target.addEventListener("pointerup", up);
    target.addEventListener("pointercancel", up);
  };

  const onResizeKey = (e: React.KeyboardEvent) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const step = e.key === "ArrowLeft" ? 40 : -40;
    setWidth(Math.min(maxWidth(), Math.max(ARTIFACT_MIN_WIDTH, width + step)));
  };

  const act = async (fn: (p: string) => Promise<void>) => {
    if (!info) return;
    setActionError(null);
    try {
      await fn(info.path);
    } catch (e) {
      setActionError(String(e));
    }
  };

  if (!path) return null;
  const list = paths.includes(path) ? paths : [path, ...paths];
  const shownWidth = dragWidth ?? width;

  let body: React.ReactNode;
  if (error) body = <Notice tone="error">{error}</Notice>;
  else if (!info) body = <Notice>读取中…</Notice>;
  else if (info.kind === "html") body = <HtmlView info={info} key={reloadKey} />;
  else if (info.kind === "pdf") body = <PdfView info={info} key={reloadKey} />;
  else if (info.kind === "image") body = <ImageView info={info} key={`${info.mtime_ms}-${reloadKey}`} />;
  else if (info.kind === "markdown") body = <MarkdownView info={info} reloadKey={reloadKey} />;
  else if (info.kind === "table") body = <TableView info={info} reloadKey={reloadKey} />;
  else if (info.kind === "office") body = <OfficeView info={info} reloadKey={reloadKey} />;
  else body = <TextView info={info} reloadKey={reloadKey} />;

  return (
    <aside ref={asideRef} className="artifact-panel" style={{ width: shownWidth }} aria-label="成果预览">
      <div
        className="artifact-panel__resize"
        role="separator"
        aria-orientation="vertical"
        aria-label="拖动调整预览宽度"
        tabIndex={0}
        onPointerDown={startResize}
        onKeyDown={onResizeKey}
        data-dragging={dragWidth !== null}
      />
      <header className="artifact-panel__head">
        <div className="artifact-panel__title">
          <span aria-hidden>{fileEmoji(path)}</span>
          {list.length > 1 ? (
            <select value={path} onChange={(e) => openArtifact(e.target.value)} aria-label={`这段对话的成果，共 ${list.length} 个`} title={path}>
              {list.map((p) => <option key={p} value={p}>{basename(p)}</option>)}
            </select>
          ) : (
            <strong title={path}>{basename(path)}</strong>
          )}
        </div>
        <div className="artifact-panel__actions">
          <button type="button" onClick={reload} title="刷新" aria-label="刷新"><ArrowClockwise size={16} /></button>
          <button type="button" onClick={() => void act(openFile)} title="用默认程序打开" aria-label="用默认程序打开" disabled={!info}>
            <ArrowSquareOut size={16} />
          </button>
          <button type="button" onClick={() => void act(revealInFinder)} title={`在 ${fileManagerName()} 中显示`} aria-label={`在 ${fileManagerName()} 中显示`} disabled={!info}>
            <FolderOpen size={16} />
          </button>
          <button type="button" onClick={close} title="关闭预览" aria-label="关闭预览"><X size={16} /></button>
        </div>
      </header>
      {info && (
        <div className="artifact-panel__meta">
          {artifactLabel(info.kind, info.ext)} · {formatBytes(info.size)} · 更新于 {timeLabel(info.mtime_ms)}
          {list.length > 1 && <span> · 这段对话共 {list.length} 个成果</span>}
        </div>
      )}
      {actionError && <Notice tone="error">{actionError}</Notice>}
      <div className="artifact-panel__body">{body}</div>
    </aside>
  );
}
