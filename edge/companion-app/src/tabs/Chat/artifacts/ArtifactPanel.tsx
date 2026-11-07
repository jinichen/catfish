/**
 * 对话右侧栏 (11/07 鸿波「先做成果区」「浏览器两步一起做」): 两页 ——
 *   - 成果 (FilePane): 小鲶生成的网页 / 文档 / 表格 / 图片 / Word·Excel·PPT
 *   - 浏览器 (BrowserPane): 小鲶正在操作的网页, 需要时「我来操作」自己接手
 * 什么时候自动打开见 useArtifactAutoOpen; 左边缘拖动调宽, 宽度记在本机。
 */
import { useCallback, useRef, useState } from "react";
import { X } from "@phosphor-icons/react";
import { useArtifactStore, ARTIFACT_MIN_WIDTH } from "../../../store/artifacts";
import FilePane from "./FilePane";
import BrowserPane from "./BrowserPane";

/** 聊天区至少留这么宽, 右栏拖不过来 */
const CHAT_MIN_WIDTH = 400;

export default function ArtifactPanel({ paths }: { paths: string[] }) {
  const { view, width, showView, close, setWidth } = useArtifactStore();
  const [dragWidth, setDragWidth] = useState<number | null>(null);
  const asideRef = useRef<HTMLElement>(null);

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
    target.setPointerCapture?.(e.pointerId);
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

  if (!view) return null;

  return (
    <aside ref={asideRef} className="artifact-panel" style={{ width: dragWidth ?? width }} aria-label="右侧栏">
      <div
        className="artifact-panel__resize"
        role="separator"
        aria-orientation="vertical"
        aria-label="拖动调整右侧栏宽度"
        tabIndex={0}
        onPointerDown={startResize}
        onKeyDown={onResizeKey}
        data-dragging={dragWidth !== null}
      />
      <header className="artifact-panel__head">
        <div className="artifact-panel__tabs" role="tablist" aria-label="右侧栏">
          <button type="button" role="tab" aria-selected={view === "file"} onClick={() => showView("file")}>
            成果{paths.length > 0 && <span className="artifact-panel__count">{paths.length}</span>}
          </button>
          <button type="button" role="tab" aria-selected={view === "browser"} onClick={() => showView("browser")}>
            浏览器
          </button>
        </div>
        <div className="artifact-panel__actions">
          <button type="button" onClick={close} title="关闭右侧栏" aria-label="关闭右侧栏"><X size={16} /></button>
        </div>
      </header>
      {view === "file" ? <FilePane paths={paths} /> : <BrowserPane />}
    </aside>
  );
}
