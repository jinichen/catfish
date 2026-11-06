/**
 * 成果区里各类文件怎么显示 (11/07)。
 *
 * 网页: iframe 走 asset 协议直接打开文件 (后端 artifact_stat 只放行了这一个文件 + 同目录),
 *   sandbox 只给 allow-scripts —— 页面里的图表脚本能跑, 但它是个「无来源」的隔离页面,
 *   碰不到 Companion 的界面和本机命令; 也不像邮件正文那样走 srcdoc (srcdoc 会继承 Companion 的 CSP,
 *   页面自己的脚本全被拦)。
 * PDF / 图片: 同样走 asset 协议。
 * Markdown / 表格 / 文本: 后端读字节, 前端判编码后用 React 画。
 */
import { useEffect, useMemo, useState } from "react";
import { Markdown } from "../../../lib/markdown";
import { parseDelimited } from "../../../lib/csv";
import { artifactReadText, artifactUrl, type ArtifactInfo, type ArtifactText } from "../../../lib/tauri_artifacts";

export function Notice({ children, tone = "muted" }: { children: React.ReactNode; tone?: "muted" | "error" }) {
  return <div className={`artifact-notice artifact-notice--${tone}`}>{children}</div>;
}

export function HtmlView({ info }: { info: ArtifactInfo }) {
  return (
    <iframe
      key={info.mtime_ms}
      className="artifact-frame artifact-frame--page"
      title={info.name}
      src={artifactUrl(info)}
      sandbox="allow-scripts"
      referrerPolicy="no-referrer"
    />
  );
}

export function PdfView({ info }: { info: ArtifactInfo }) {
  return <iframe key={info.mtime_ms} className="artifact-frame" title={info.name} src={artifactUrl(info)} />;
}

export function ImageView({ info }: { info: ArtifactInfo }) {
  const [failed, setFailed] = useState(false);
  if (failed) return <Notice tone="error">图片显示不出来，可能文件坏了，用默认程序打开看看。</Notice>;
  return (
    <div className="artifact-image">
      <img src={artifactUrl(info)} alt={info.name} onError={() => setFailed(true)} />
    </div>
  );
}

/** 读文本类文件; 文件一改 (mtime 变) 或点刷新 (reloadKey) 就重读。 */
function useText(info: ArtifactInfo, reloadKey: number) {
  const [state, setState] = useState<{ data?: ArtifactText; error?: string }>({});
  useEffect(() => {
    let cancelled = false;
    artifactReadText(info.path)
      .then((data) => !cancelled && setState({ data }))
      .catch((e) => !cancelled && setState({ error: String(e) }));
    return () => {
      cancelled = true;
    };
  }, [info.path, info.mtime_ms, reloadKey]);
  return state;
}

function TruncatedNote({ data }: { data: ArtifactText }) {
  if (!data.truncated) return null;
  return <Notice>文件有 {(data.size / 1024 / 1024).toFixed(1)} MB，这里只显示前 2 MB，完整内容用默认程序打开。</Notice>;
}

export function MarkdownView({ info, reloadKey }: { info: ArtifactInfo; reloadKey: number }) {
  const { data, error } = useText(info, reloadKey);
  if (error) return <Notice tone="error">{error}</Notice>;
  if (!data) return <Notice>读取中…</Notice>;
  return (
    <div className="artifact-doc">
      <TruncatedNote data={data} />
      <Markdown text={data.text} />
    </div>
  );
}

export function TextView({ info, reloadKey }: { info: ArtifactInfo; reloadKey: number }) {
  const { data, error } = useText(info, reloadKey);
  const text = useMemo(() => {
    if (!data || info.ext !== "json" || data.truncated) return data?.text ?? "";
    try {
      return JSON.stringify(JSON.parse(data.text), null, 2);
    } catch {
      return data.text;
    }
  }, [data, info.ext]);
  if (error) return <Notice tone="error">{error}</Notice>;
  if (!data) return <Notice>读取中…</Notice>;
  return (
    <div className="artifact-doc">
      <TruncatedNote data={data} />
      <pre className="artifact-text">{text}</pre>
    </div>
  );
}

export function DataTable({ rows, firstRowHeader = true }: { rows: string[][]; firstRowHeader?: boolean }) {
  if (rows.length === 0) return <Notice>是空的。</Notice>;
  const width = Math.max(...rows.map((r) => r.length));
  const [head, ...body] = firstRowHeader ? rows : [[], ...rows];
  const pad = (r: string[]) => Array.from({ length: width }, (_, i) => r[i] ?? "");
  return (
    <div className="artifact-table-wrap">
      <table className="artifact-table">
        {firstRowHeader && (
          <thead>
            <tr>
              <th className="artifact-table__rownum" />
              {pad(head).map((c, i) => <th key={i}>{c}</th>)}
            </tr>
          </thead>
        )}
        <tbody>
          {body.map((r, ri) => (
            <tr key={ri}>
              <td className="artifact-table__rownum">{ri + (firstRowHeader ? 2 : 1)}</td>
              {pad(r).map((c, i) => <td key={i}>{c}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function TableView({ info, reloadKey }: { info: ArtifactInfo; reloadKey: number }) {
  const { data, error } = useText(info, reloadKey);
  const parsed = useMemo(() => (data ? parseDelimited(data.text, info.ext === "tsv" ? "\t" : ",") : null), [data, info.ext]);
  if (error) return <Notice tone="error">{error}</Notice>;
  if (!data || !parsed) return <Notice>读取中…</Notice>;
  return (
    <div className="artifact-sheet">
      {(data.truncated || parsed.truncated) && <Notice>表格太大，这里只显示前 {parsed.rows.length} 行，完整内容用默认程序打开。</Notice>}
      <DataTable rows={parsed.rows} />
    </div>
  );
}
