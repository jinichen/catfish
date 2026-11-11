/** 「你的文书风格」—— 你自己指定的范文 + 小鲶从范文里总结的写法特点 (11/12)。
 *
 * 鸿波 11/12「文书风格合理吗」: 老的「自动学」取搜索索引里最近 500 篇抽统计数字, 实盘 395 篇是小鲶自己的产出
 * (学的不是员工), 抽出来的是主题词和碎片 (一条待办勾选项当「样本句」), 模型 30 天一次没查过。
 * 谁写的只有员工自己知道 → 改成你挑 3–5 篇自己写得满意的放进来:
 *   · 范文只存本机 (~/.catfish/style_samples/);
 *   · 「让小鲶总结写法特点」会把范文正文发给模型 —— 按钮旁写明, 点了才发;
 *   · 特点你可以改 (一行一条);
 *   · 你让小鲶写周报 / 汇报这类正式文书时, 特点和范文片段自动带上 (catfish-memory 插件 render_style)。
 */
import { useCallback, useEffect, useRef, useState, type ChangeEvent } from "react";

import {
  STYLE_ACCEPT, styleAdd, styleRemove, styleSetTraits, styleState, styleSummarize, type StyleState,
} from "../../lib/tauri_style_samples";
import { useAgentStore } from "../../store/agent";
import { stageFiles } from "../Collab/RoomsDelivery";

const MAX_BYTES = 20 * 1024 * 1024;

const btn = (primary = false) => ({
  fontSize: 12, padding: "4px 12px", borderRadius: 4, cursor: "pointer", whiteSpace: "nowrap" as const,
  border: `1px solid ${primary ? "var(--catfish-cyan)" : "var(--catfish-border)"}`,
  background: primary ? "var(--catfish-cyan)" : "transparent",
  color: primary ? "var(--catfish-bg)" : "var(--catfish-text)",
});
const linkBtn = { border: "none", background: "transparent", cursor: "pointer", padding: 0, fontSize: 12, color: "var(--catfish-text-muted)" } as const;
const muted = { fontSize: 12, color: "var(--catfish-text-muted)", lineHeight: 1.6 } as const;

function day(ts: number): string {
  const d = new Date(ts * 1000);
  return `${d.getMonth() + 1}月${d.getDate()}日`;
}

export default function StyleFingerprintCard() {
  const agentName = useAgentStore((s) => s.name);
  const [st, setSt] = useState<StyleState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"add" | "sum" | "save" | string | null>(null);
  const [draft, setDraft] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(() => styleState().then((s) => { setSt(s); setError(null); })
    .catch((e) => setError(e instanceof Error ? e.message : String(e))), []);
  useEffect(() => { void refresh(); }, [refresh]);

  async function run(tag: string, fn: () => Promise<StyleState>) {
    setBusy(tag);
    setError(null);
    try {
      setSt(await fn());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }
  async function pick(e: ChangeEvent<HTMLInputElement>) {
    const files = [...(e.target.files ?? [])];
    e.target.value = "";
    for (const f of files) {
      if (f.size > MAX_BYTES) { setError(`「${f.name}」超过 20MB, 范文不用这么大`); continue; }
      await run("add", async () => {
        const [staged] = await stageFiles([{ name: f.name, size: f.size, file: f }], () => undefined);
        return styleAdd(staged.stage_id, f.name);
      });
    }
  }
  const saveTraits = () => run("save", async () => {
    const s = await styleSetTraits((draft ?? "").split("\n").map((x) => x.trim()).filter(Boolean));
    setDraft(null);
    return s;
  });

  const samples = st?.samples ?? [];
  const traits = st?.traits ?? [];
  const full = !!st && samples.length >= st.max_samples;
  return (
    <div style={{
      background: "var(--catfish-bg-elevated)", border: "1px solid var(--catfish-border)", borderRadius: "var(--radius-md)",
      padding: "var(--space-4)", height: "min(480px, 50vh)", boxSizing: "border-box", display: "flex", flexDirection: "column", gap: "var(--space-3)",
    }}>
      <div>
        <h3 style={{ margin: 0 }}>✍️ 你的文书风格</h3>
        <div style={{ ...muted, marginTop: 4 }}>
          挑 3–5 篇你自己写得满意的周报、汇报放进来。你让{agentName}写正式文书时, 它会照这几篇的写法来。范文只存在这台电脑上。
        </div>
      </div>

      {error && <div style={{ fontSize: 12, color: "var(--status-err)" }}>{error}</div>}

      <div style={{ flex: 1, minHeight: 0, overflowY: "auto", display: "flex", flexDirection: "column", gap: "var(--space-3)", paddingRight: 4 }}>
        <section>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
            <strong style={{ fontSize: 13 }}>我的范文{st ? ` (${samples.length}/${st.max_samples})` : ""}</strong>
            <span style={{ flex: 1 }} />
            <input ref={fileRef} type="file" multiple accept={STYLE_ACCEPT} aria-label="选范文" style={{ display: "none" }} onChange={(e) => void pick(e)} />
            <button type="button" style={btn()} disabled={!st || full || busy !== null} onClick={() => fileRef.current?.click()}
                    title={full ? "已经放满了, 先删掉一篇" : "Word / PDF / 文本"}>
              {busy === "add" ? "读取中…" : "＋ 添加范文"}
            </button>
          </div>
          {st && samples.length === 0 && <div style={muted}>还没有范文。支持 Word (.docx)、PDF、文本; 扫描件读不出字, 老的 .doc 请先另存为 .docx。</div>}
          {samples.map((s) => (
            <div key={s.id} style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 0", borderTop: "1px dashed var(--catfish-border)", fontSize: 13 }}>
              <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={s.name}>{s.name}</span>
              <span style={{ ...muted, whiteSpace: "nowrap" }}>{s.chars.toLocaleString()} 字 · {day(s.added_at)}加</span>
              <button type="button" style={linkBtn} disabled={busy !== null} onClick={() => void run(s.id, () => styleRemove(s.id))}>
                {busy === s.id ? "删除中…" : "删除"}
              </button>
            </div>
          ))}
        </section>

        {samples.length > 0 && (
          <section>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6, flexWrap: "wrap" }}>
              <strong style={{ fontSize: 13 }}>写法特点</strong>
              {st?.stale && traits.length > 0 && <span style={{ fontSize: 12, color: "var(--status-warn)" }}>范文换过了, 建议重新总结</span>}
              <span style={{ flex: 1 }} />
              {draft === null && traits.length > 0 && (
                <button type="button" style={btn()} disabled={busy !== null} onClick={() => setDraft(traits.join("\n"))}>我来改</button>
              )}
              {draft === null && (
                <button type="button" style={btn(traits.length === 0 || !!st?.stale)} disabled={busy !== null} onClick={() => void run("sum", styleSummarize)}>
                  {busy === "sum" ? `${agentName}在读范文…` : traits.length ? "重新总结" : `让${agentName}总结写法特点`}
                </button>
              )}
            </div>
            {draft === null ? (
              traits.length > 0 ? (
                <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13, lineHeight: 1.8 }}>
                  {traits.map((t) => <li key={t}>{t}</li>)}
                </ul>
              ) : (
                <div style={muted}>还没总结。没总结也会带上范文片段, 总结后{agentName}学得更准。</div>
              )
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <textarea aria-label="写法特点" value={draft} rows={6} onChange={(e) => setDraft(e.target.value)}
                          style={{ fontSize: 13, lineHeight: 1.7, padding: 8, borderRadius: 4, resize: "vertical", fontFamily: "inherit",
                                   border: "1px solid var(--catfish-border)", background: "var(--catfish-bg)", color: "var(--catfish-text)" }} />
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <span style={{ ...muted, flex: 1 }}>一行一条, 最多 8 条</span>
                  <button type="button" style={btn()} disabled={busy !== null} onClick={() => setDraft(null)}>取消</button>
                  <button type="button" style={btn(true)} disabled={busy !== null} onClick={() => void saveTraits()}>{busy === "save" ? "保存中…" : "保存"}</button>
                </div>
              </div>
            )}
            {draft === null && <div style={{ ...muted, marginTop: 6 }}>总结时, 范文正文会发给模型 (每篇最多前 5000 字), 点了才发。</div>}
          </section>
        )}
      </div>
    </div>
  );
}
