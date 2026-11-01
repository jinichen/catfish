/** 知识库「检索打分」(11/02, 借 DeepTutor 的 kb eval)。
 *
 * 写一组「问题 + 应该找到的东西」, 同一组题跑四种检索方式, 看谁找得准:
 * 知识页的关键词 / 关键词+语义+图谱 / 只用语义, 以及小鲶聊天时调的那一路。
 * 改检索 (调权重、换模型) 前后各跑一遍, 有个数字可比, 不靠感觉。
 */
import * as React from "react";

import { wikiRetrievalEval, type RetrievalEvalReport } from "../../lib/tauri_wiki";

const EXAMPLE = [
  '{"query": "季度预算谁批?", "gold": ["财务部王芳"]}',
  '{"query": "立项会几点开", "gold": ["wiki/concepts/立项会.md"]}',
].join("\n");

const pct = (v: number) => `${Math.round(v * 100)}%`;

export default function WikiRetrievalEval() {
  const [text, setText] = React.useState("");
  const [topK, setTopK] = React.useState(5);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [report, setReport] = React.useState<RetrievalEvalReport | null>(null);
  const [onlyMisses, setOnlyMisses] = React.useState(true);

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      setReport(await wikiRetrievalEval(text, topK));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const loadFile = async (f: File | undefined) => {
    if (f) setText(await f.text());
  };

  const live = report?.engines.filter((e) => e.available) ?? [];
  const rows = report?.queries.filter((q) =>
    !onlyMisses || live.some((e) => q.ranks[e.engine] == null)) ?? [];

  return (
    <div className="wiki-eval" style={{ padding: 16, display: "grid", gap: 12, maxWidth: 960 }}>
      <div>
        <h3 style={{ margin: 0 }}>检索打分</h3>
        <p style={{ margin: "4px 0 0", fontSize: 13, color: "var(--catfish-text-muted)" }}>
          写一组问题和「应该找到的东西」, 看四种检索方式各自能不能把它排在前面。
          一行一题: <code>{'{"query": "问题", "gold": ["应该找到的一段话 / 条目标题 / wiki/…md 路径"]}'}</code>,
          gold 里任一条出现在前 {topK} 条结果里就算找到。
        </p>
      </div>
      <textarea value={text} onChange={(e) => setText(e.target.value)} rows={8} placeholder={EXAMPLE}
        aria-label="评测题目" style={{ width: "100%", boxSizing: "border-box", fontFamily: "var(--font-mono, monospace)", fontSize: 12 }} />
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <label style={{ fontSize: 13 }}>
          从文件读 (.jsonl / .json){" "}
          <input type="file" accept=".jsonl,.json,.txt" onChange={(e) => void loadFile(e.target.files?.[0])} />
        </label>
        <label style={{ fontSize: 13 }}>
          看前{" "}
          <select value={topK} onChange={(e) => setTopK(Number(e.target.value))}>
            {[1, 3, 5, 10].map((k) => <option key={k} value={k}>{k}</option>)}
          </select>{" "}条
        </label>
        <button type="button" className="primary" disabled={busy || !text.trim()} onClick={() => void run()}>
          {busy ? "跑分中…" : "开始打分"}
        </button>
      </div>
      {error && <div role="alert" style={{ color: "var(--status-err)" }}>{error}</div>}

      {report && (
        <>
          <table style={{ borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr>
                {["检索方式", `前 ${report.topK} 条命中率`, "平均排名分 (MRR)", "找到"].map((h) => (
                  <th key={h} style={{ textAlign: "left", padding: "4px 12px 4px 0" }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {report.engines.map((e) => (
                <tr key={e.engine}>
                  <td style={{ padding: "4px 12px 4px 0" }}>{e.label}</td>
                  {e.available ? (
                    <>
                      <td><b>{pct(e.recall)}</b></td>
                      <td>{e.mrr.toFixed(2)}</td>
                      <td>{e.found} / {e.total}</td>
                    </>
                  ) : (
                    <td colSpan={3} style={{ color: "var(--catfish-text-muted)" }}>没跑: {e.note}</td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
          <p style={{ margin: 0, fontSize: 12, color: "var(--catfish-text-muted)" }}>
            平均排名分: 正确答案排第一得 1 分、第二 0.5、第三 0.33…… 没找到 0 分, 再取平均。越接近 1 越好。
          </p>
          <label style={{ fontSize: 13 }}>
            <input type="checkbox" checked={onlyMisses} onChange={(e) => setOnlyMisses(e.target.checked)} />
            {" "}只看有方式没找到的题 ({rows.length})
          </label>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13, display: "grid", gap: 6 }}>
            {rows.map((q, n) => (
              <li key={n}>
                <b>{q.query}</b>
                <div style={{ color: "var(--catfish-text-muted)" }}>
                  {live.map((e) => (
                    <div key={e.engine}>
                      {e.label}: {q.ranks[e.engine] != null ? `第 ${q.ranks[e.engine]} 条` : "没找到"}
                      {q.ranks[e.engine] == null && (q.tops[e.engine]?.length ?? 0) > 0 &&
                        ` (排前面的是: ${q.tops[e.engine].join("、")})`}
                    </div>
                  ))}
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
