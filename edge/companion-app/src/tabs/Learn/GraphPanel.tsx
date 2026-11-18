/**
 * 我学过的要点 (11/18, docs/LEARNING-PLAN.md §3): 学完沉淀的、导入资料提炼的、会议纪要「记住这几条」带进来的要点和推理链,
 * 按第一次提到它的那份材料分组; 每条显示推出它的链 (A + B ⇒ 本条)、挂到的知识库条目、还有哪些材料也提到。
 * 导入资料: 文件分片传到本机暂存 (stageFiles, 跟群聊发文件同一个口子), tool-bridge 抽文字后台提炼; 删一份材料只摘它的出处。
 */
import { useCallback, useEffect, useRef, useState, type ChangeEvent, type CSSProperties } from "react";

import { GRAPH_ACCEPT, learnGraphDelete, learnGraphImport, learnGraphView, type GraphView } from "../../lib/tauri_learn";
import { cardStyle, ErrorLine, inputStyle } from "../Collab/roomLinkUi";
import { stageFiles } from "../Collab/RoomsDelivery";
import { ghostBtn, primaryBtn } from "./QuestionCard";

const muted: CSSProperties = { fontSize: 12, color: "var(--catfish-text-muted)" };
const KIND: Record<string, string> = { learn: "🎓", file: "📄", meeting: "🎙" };
const MAX_BYTES = 50 * 1024 * 1024;
const POLL_MS = 2000;

export default function GraphPanel({ onChanged }: { onChanged?: () => void }) {
  const [g, setG] = useState<GraphView | null>(null);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);
  const file = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try { setG(await learnGraphView()); } catch (e) { setError(String(e)); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const running = !!g?.running;
  useEffect(() => {
    if (!running) return;
    const t = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(t);
  }, [running, load]);
  const was = useRef(false);
  useEffect(() => { if (was.current && !running) onChanged?.(); was.current = running; }, [running, onChanged]);
  useEffect(() => {
    if (!confirm) return;
    const t = window.setTimeout(() => setConfirm(null), 3000);
    return () => window.clearTimeout(t);
  }, [confirm]);

  const pick = async (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (!f) return;
    if (f.size > MAX_BYTES) { setError(`「${f.name}」超过 50MB`); return; }
    setBusy("import");
    setError(null);
    try {
      const [staged] = await stageFiles([{ name: f.name, size: f.size, file: f }], () => undefined);
      setG(await learnGraphImport(staged.stage_id, f.name));
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(null);
    }
  };
  const remove = async (source: string) => {
    // 删除点两下: Tauri WebView 里 window.confirm() 静默返回 null
    if (confirm !== source) { setConfirm(source); return; }
    setConfirm(null);
    setBusy(source);
    try { setG(await learnGraphDelete(source)); onChanged?.(); } catch (e) { setError(String(e)); } finally { setBusy(null); }
  };

  if (!g) return <div style={cardStyle}>{error ? <ErrorLine>{error}</ErrorLine> : <span style={muted}>读取中…</span>}</div>;
  const kw = q.trim().toLowerCase();
  const sources = g.sources
    .map((s) => ({ ...s, points: kw ? s.points.filter((p) => `${p.name} ${p.statement} ${p.wiki_title}`.toLowerCase().includes(kw)) : s.points }))
    .filter((s) => s.points.length > 0);
  return (
    <div style={{ ...cardStyle, height: "auto" }} aria-label="我学过的要点">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <div style={{ fontSize: 16, fontWeight: 600 }}>📚 我学过的要点</div>
        <span style={muted}>{g.points} 条要点 · {g.chains} 条推理</span>
      </div>
      <div style={muted}>
        学完会自动把讲过的沉淀成要点; 一条要点能由哪几条推出来, 就是一条推理。小鲶摸底、出练习题、回答问题时会用到。
        同一条从几份材料来只留一条, 出处都记着。
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <input aria-label="找要点" value={q} onChange={(e) => setQ(e.target.value)} placeholder="找要点 (名字 / 内容)"
          style={{ ...inputStyle, flex: 1, minWidth: 160 }} />
        <input ref={file} type="file" accept={GRAPH_ACCEPT} style={{ display: "none" }} aria-label="选资料" onChange={(e) => void pick(e)} />
        <button type="button" style={primaryBtn(!!busy || running)} disabled={!!busy || running} onClick={() => file.current?.click()}
          title="Word / PDF / PPT / Excel / 文本; 小鲶读一遍提炼要点和推理 (内容会经网关给大模型)">
          {busy === "import" ? "上传中…" : "＋ 导入资料"}
        </button>
      </div>
      {running && <div style={muted}>🐟 小鲶在读「{g.running}」, 提炼要点和推理 (长资料要几分钟; 可以先切去干别的)…</div>}
      {g.last?.error && <ErrorLine>「{g.last.name}」没读成: {g.last.error}</ErrorLine>}
      {g.last && !g.last.error && g.last.name && (
        <div style={{ fontSize: 12, color: "var(--status-ok)" }}>
          ✓ 「{g.last.name}」: 新增 {g.last.added} 条要点, {g.last.linked} 条跟已有的是同一条 (合并了), {g.last.chains} 条推理
        </div>
      )}
      {error && <ErrorLine>{error}</ErrorLine>}
      {sources.length === 0 && (
        <div style={{ ...muted, padding: "16px 0", textAlign: "center" }}>
          {kw ? "没找到" : "还没有要点。学完一次、导入一份资料, 或者在会议纪要上点「记住这几条」, 都会沉淀到这里。"}
        </div>
      )}
      {sources.map((s) => (
        <details key={s.source} open style={{ borderTop: "1px solid var(--catfish-border)", paddingTop: 8 }}>
          <summary style={{ cursor: "pointer", display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center" }}>
            <span style={{ fontWeight: 600, fontSize: 13 }}>{KIND[s.kind] ?? "•"} {s.label} <span style={muted}>· {s.points.length} 条</span></span>
            {s.kind !== "learn" && (
              <button type="button" disabled={!!busy || running} onClick={(e) => { e.preventDefault(); void remove(s.source); }}
                style={{ ...ghostBtn, fontSize: 12, padding: "2px 8px",
                  ...(confirm === s.source ? { color: "var(--status-err)", borderColor: "var(--status-err)" } : null) }}
                title="只删这份材料的出处; 别的材料也提到的要点留着">
                {confirm === s.source ? "再点一次删除" : "删这份"}
              </button>
            )}
          </summary>
          <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 8 }}>
            {s.points.map((p) => (
              <div key={p.id} style={{ fontSize: 13, lineHeight: 1.6, paddingLeft: 8, borderLeft: "2px solid var(--catfish-border)" }}>
                <div><b>{p.name}</b> — {p.statement}</div>
                {p.chains.map((c) => <div key={c} style={muted}>⤷ 推理: {c}</div>)}
                {(p.wiki_title || p.also.length > 0) && (
                  <div style={muted}>
                    {p.wiki_title ? `知识库: ${p.wiki_title}` : ""}{p.wiki_title && p.also.length ? " · " : ""}
                    {p.also.length ? `也来自: ${p.also.join("、")}` : ""}
                  </div>
                )}
              </div>
            ))}
          </div>
        </details>
      ))}
    </div>
  );
}
