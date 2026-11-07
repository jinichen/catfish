/**
 * 右侧栏「成果」页 (11/07): 小鲶这段对话里生成 / 提到的网页、文档、表格、图片。
 * 顶上切换这段对话的其他成果; 文件被小鲶改了 (每 2 秒看一次修改时间) 自动刷新。
 */
import { useEffect, useState } from "react";
import { ArrowClockwise, ArrowSquareOut, FolderOpen } from "@phosphor-icons/react";
import { useArtifactStore } from "../../../store/artifacts";
import { artifactStat, type ArtifactInfo } from "../../../lib/tauri_artifacts";
import { artifactLabel } from "../../../lib/artifactKinds";
import { basename, fileEmoji } from "../../../lib/path_detect";
import { formatBytes } from "../../../lib/format";
import { fileManagerName } from "../../../lib/platformLabels";
import { openFile, revealInFinder } from "../../../lib/tauri";
import { HtmlView, ImageView, MarkdownView, Notice, PdfView, TableView, TextView } from "./ArtifactViews";
import OfficeView from "./OfficeView";

const POLL_MS = 2000;

function timeLabel(ms: number): string {
  if (!ms) return "";
  const d = new Date(ms);
  const sameDay = d.toDateString() === new Date().toDateString();
  const hm = d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  return sameDay ? hm : `${d.getMonth() + 1}/${d.getDate()} ${hm}`;
}

export default function FilePane({ paths }: { paths: string[] }) {
  const { path, reloadKey, openArtifact, reload } = useArtifactStore();
  const [info, setInfo] = useState<ArtifactInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

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

  const act = async (fn: (p: string) => Promise<void>) => {
    if (!info) return;
    setActionError(null);
    try {
      await fn(info.path);
    } catch (e) {
      setActionError(String(e));
    }
  };

  if (!path) {
    return (
      <div className="artifact-panel__body">
        <Notice>{paths.length ? "从上面选一个成果看。" : "这段对话还没有生成文件。小鲶写出网页、文档、表格、图片后会在这里显示。"}</Notice>
        {paths.length > 0 && (
          <ul className="artifact-filelist">
            {paths.map((p) => (
              <li key={p}><button type="button" onClick={() => openArtifact(p)} title={p}>{fileEmoji(p)} {basename(p)}</button></li>
            ))}
          </ul>
        )}
      </div>
    );
  }

  const list = paths.includes(path) ? paths : [path, ...paths];
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
    <>
      <div className="artifact-panel__toolbar">
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
        </div>
      </div>
      {info && (
        <div className="artifact-panel__meta">
          {artifactLabel(info.kind, info.ext)} · {formatBytes(info.size)} · 更新于 {timeLabel(info.mtime_ms)}
          {list.length > 1 && <span> · 这段对话共 {list.length} 个成果</span>}
        </div>
      )}
      {actionError && <Notice tone="error">{actionError}</Notice>}
      <div className="artifact-panel__body">{body}</div>
    </>
  );
}
