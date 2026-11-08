/** 审批人试用的记录 (11/08 分享治理 §2.3)。技能 / 专家包 / 连接器的审核弹窗共用。
 *
 * 试用本身不在网页上做 —— 要装到审批人自己的电脑上跑, 所以入口在 Companion 的资源市场;
 * 这里只展示 hub 收到的回传: 谁试的、什么时候、成没成、每步结果、产出了哪些文件。
 */
import type { TrialRecord } from "../lib/hub";

const muted = { fontSize: 12, color: "var(--text-muted)" } as const;

/** 中央存的是 UTC (…+00:00), 显示成本地时间 */
export const when = (t?: string | number) =>
  t ? new Date(typeof t === "number" ? t * 1000 : t).toLocaleString("zh-CN", { hour12: false }).slice(0, -3) : "";

export function TrialHint({ what = "它" }: { what?: string }) {
  return (
    <div
      style={{
        ...muted,
        padding: "8px 10px",
        borderRadius: "var(--radius-sm)",
        background: "var(--bg-secondary)",
        lineHeight: 1.6,
      }}
    >
      要先试{what === "它" ? "" : `试${what}`}，在 Companion 的资源市场里点「装到我这里试用」；只装到你自己的电脑上、不给别人，试跑结果会显示在这里。
    </div>
  );
}

export function TrialResults({ trials }: { trials?: TrialRecord[] }) {
  if (!trials?.length) {
    return <div style={muted}>还没人试用过。</div>;
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {trials.map((t, i) => {
        const r = t.result;
        return (
          <div
            key={`${t.by}-${t.at}-${i}`}
            style={{ border: "1px solid var(--border-soft)", borderRadius: "var(--radius-sm)", padding: "6px 10px", fontSize: 12 }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
              <span>
                {t.by} 于 {when(t.at)} 试用
              </span>
              {r ? (
                <b style={{ color: r.ok ? "var(--status-ok)" : "var(--status-err)" }}>{r.ok ? "试跑成功" : "试跑失败"}</b>
              ) : (
                <span style={muted}>还没回传结果</span>
              )}
            </div>
            {r?.workflow && <div style={muted}>工作流：{r.workflow}</div>}
            {r?.summary && <div style={{ marginTop: 4 }}>{r.summary}</div>}
            {r?.steps?.length ? (
              <ol style={{ margin: "4px 0 0", paddingLeft: 18 }}>
                {r.steps.map((s, j) => <li key={j}>{s}</li>)}
              </ol>
            ) : null}
            {r?.files?.length ? (
              <div style={{ ...muted, marginTop: 4, fontFamily: "var(--font-mono)" }}>产出文件：{r.files.join("、")}</div>
            ) : null}
            {r?.at && <div style={muted}>回传于 {when(r.at)}</div>}
          </div>
        );
      })}
    </div>
  );
}
