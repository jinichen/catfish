/**
 * 成果区里的 Word / Excel / PPT (11/07)。后端调 scripts/parse_file_preview.py 转成 JSON,
 * 这里用 React 画 —— 文字全当纯文本, 颜色跟着 Companion 的亮 / 暗色。
 * 只是「看得清内容」的预览, 版式 (字体、页边距、动画) 不还原; 要原样看点「用默认程序打开」。
 */
import { useEffect, useState } from "react";
import { artifactOfficePreview, type ArtifactInfo, type OfficePreview } from "../../../lib/tauri_artifacts";
import { DataTable, Notice } from "./ArtifactViews";

export default function OfficeView({ info, reloadKey }: { info: ArtifactInfo; reloadKey: number }) {
  const [state, setState] = useState<{ data?: OfficePreview; error?: string }>({});
  const [sheet, setSheet] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setState({});
    artifactOfficePreview(info.path)
      .then((data) => !cancelled && setState({ data }))
      .catch((e) => !cancelled && setState({ error: String(e) }));
    return () => {
      cancelled = true;
    };
  }, [info.path, info.mtime_ms, reloadKey]);

  const { data, error } = state;
  if (error) return <Notice tone="error">{error}</Notice>;
  if (!data) return <Notice>正在转换成预览…</Notice>;
  const cut = data.truncated && <Notice>内容较多，这里只显示了一部分，完整内容用默认程序打开。</Notice>;

  if (data.kind === "sheet") {
    const current = data.sheets[Math.min(sheet, data.sheets.length - 1)];
    return (
      <div className="artifact-sheet">
        {data.sheets.length > 1 && (
          <div className="artifact-sheet__tabs" role="tablist" aria-label="工作表">
            {data.sheets.map((s, i) => (
              <button key={s.name} type="button" role="tab" aria-selected={i === sheet} onClick={() => setSheet(i)}>
                {s.name}
              </button>
            ))}
          </div>
        )}
        {cut}
        {current ? <DataTable rows={current.rows} /> : <Notice>没有工作表。</Notice>}
      </div>
    );
  }

  if (data.kind === "slides") {
    return (
      <div className="artifact-doc artifact-slides">
        {cut}
        {data.slides.map((s, i) => (
          <section key={i} className="artifact-slide" aria-label={`第 ${i + 1} 页`}>
            <div className="artifact-slide__no">{i + 1}</div>
            {s.title && <h3>{s.title}</h3>}
            {s.texts.map((t, j) => <p key={j}>{t}</p>)}
            {s.images.map((src, j) => <img key={j} src={src} alt="" />)}
            {!s.title && s.texts.length === 0 && s.images.length === 0 && <p className="artifact-muted">（这一页没有文字）</p>}
          </section>
        ))}
      </div>
    );
  }

  return (
    <div className="artifact-doc artifact-word">
      {cut}
      {data.blocks.map((b, i) => {
        if (b.t === "h") {
          const Tag = (`h${Math.min(b.level + 1, 5)}`) as "h2" | "h3" | "h4" | "h5";
          return <Tag key={i}>{b.text}</Tag>;
        }
        if (b.t === "p") return <p key={i} className={b.list ? "artifact-word__li" : undefined}>{b.text}</p>;
        if (b.t === "img") return <img key={i} src={b.src} alt="" />;
        return <DataTable key={i} rows={b.rows} />;
      })}
      {data.blocks.length === 0 && <Notice>文档里没有文字。</Notice>}
    </div>
  );
}
