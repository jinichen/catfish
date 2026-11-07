/**
 * 邮件详情的钓鱼红条 (P3.3.58 段 2C, 6/12) —— 11/07 从 DetailPane 抽出来, 加上员工标注。
 *
 * 鸿波 11/07: 信息安全中心发的安全预警被标成「钓鱼嫌疑」, 「这种识别错误, 能不能直接标注」。
 *   - 红条上「不是钓鱼」: 按发件地址记, 以后这个地址的来信不再报; 红条换成一行「你标过不是钓鱼 · 撤销」
 *   - 没报的信, 操作按钮那排「⚠ 标为钓鱼」(MarkPhishingButton): 以后这个地址的来信一律按钓鱼报
 *   - 标注只存在这台电脑上, 也作为参考交给模型复审; 邮箱设置里能看、能撤销
 * 点了先按标注改显示 (不等重扫), 后台按新标注重扫这个发件人在列表里的那几封。
 */
import { useEffect, useState } from "react";

import { senderAddress, withMark } from "../../../lib/phishingMarks";
import type { EmailDigestItem, PhishingScanResult } from "../../../lib/tauri";
import { usePhishingMarksStore } from "../../../store/phishingMarks";

type Msg = { id: string; sender?: string | null; subject?: string | null };

/** 详情里要显示的结果: 标完重扫回来的优先, 再按标注改 */
function useEffective(result: PhishingScanResult | null, msg: Msg) {
  const marks = usePhishingMarksStore((s) => s.marks);
  const rescanned = usePhishingMarksStore((s) => s.rescanned[msg.id]);
  const load = usePhishingMarksStore((s) => s.load);
  useEffect(() => { if (!marks) void load(); }, [marks, load]);
  return withMark(rescanned ?? result, msg.sender, marks);
}

function useMarkAction(msg: Msg, list: EmailDigestItem[]) {
  const mark = usePhishingMarksStore((s) => s.mark);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = async (verdict: "safe" | "phishing" | null) => {
    setBusy(true);
    setError(null);
    try {
      await mark(msg.sender ?? "", verdict, msg.subject ?? "", list.some((it) => it.id === msg.id) ? list : [...list, msg as EmailDigestItem]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, run };
}

const linkBtn = (color: string) => ({
  border: `1px solid ${color}`, background: "transparent", color, borderRadius: 4, padding: "3px 10px",
  fontSize: 12, cursor: "pointer", fontFamily: "inherit",
});

export default function PhishingBanner({ result, msg, list }: { result: PhishingScanResult | null; msg: Msg; list: EmailDigestItem[] }) {
  const p = useEffective(result, msg);
  const { busy, error, run } = useMarkAction(msg, list);
  const addr = senderAddress(msg.sender);
  if (!p) return null;

  if (p.userMark === "safe") {
    return (
      <div style={{ marginBottom: 10, fontSize: 12, color: "var(--catfish-text-muted)", display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <span>✓ 你标过「不是钓鱼」: 以后 {addr || "这个发件人"} 的来信不再报钓鱼</span>
        <button type="button" disabled={busy} onClick={() => void run(null)}
                style={{ border: "none", background: "transparent", color: "var(--catfish-cyan)", cursor: "pointer", fontSize: 12, padding: 0 }}>
          {busy ? "撤销中…" : "撤销"}
        </button>
        {error && <span style={{ color: "rgb(185,28,28)" }}>没改成: {error}</span>}
      </div>
    );
  }
  if (p.highestSeverity !== "high" && p.highestSeverity !== "medium") return null;

  const high = p.highestSeverity === "high";
  const color = high ? "rgb(185,28,28)" : "rgb(194,65,12)";
  const userFlagged = p.userMark === "phishing";
  const ruleFlags = p.flags.filter((f) => !f.ruleId.startsWith("USER-MARKED"));
  return (
    <div style={{
      marginBottom: 12, padding: "10px 12px", borderRadius: 4, fontSize: 12,
      background: high ? "rgba(220,38,38,0.08)" : "rgba(251,146,60,0.08)",
      border: high ? "1px solid rgba(220,38,38,0.4)" : "1px solid rgba(251,146,60,0.4)",
    }}>
      <div style={{ fontWeight: 700, color, marginBottom: 6 }}>
        {userFlagged ? "⚠ 你标过这个发件人是钓鱼" : high ? "⚠ catfish 标记此邮件有钓鱼嫌疑" : "⚠ catfish 标记此邮件可疑"}
      </div>
      {!userFlagged && ruleFlags.length > 0 && (
        <div style={{ marginBottom: 4, color: "var(--catfish-text)" }}>
          触发规则 ({ruleFlags.length}): {ruleFlags.slice(0, 4).map((f) => f.ruleId).join(", ")}
          {ruleFlags.length > 4 && ` 等 ${ruleFlags.length} 条`}
        </div>
      )}
      {!userFlagged && p.llmVerdict && (
        <div style={{ marginBottom: 4, color: "var(--catfish-text)" }}>
          LLM 复审: <strong>{p.llmVerdict}</strong>{p.llmReason ? ` — ${p.llmReason}` : ""}
        </div>
      )}
      <div style={{ marginTop: 4, fontWeight: 600, color }}>
        审慎打开链接 / 附件 / 回复, 如不确定请咨询信安部门.
      </div>
      <div style={{ marginTop: 8, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        {userFlagged ? (
          <button type="button" disabled={busy} onClick={() => void run(null)} style={linkBtn("var(--catfish-text-muted)")}>
            {busy ? "撤销中…" : "撤销标注"}
          </button>
        ) : (
          <>
            <button type="button" disabled={busy || !addr} onClick={() => void run("safe")} style={linkBtn(color)}
                    title={`以后 ${addr} 的来信不再报钓鱼; 标注只存在你这台电脑上, 也会作为参考让小鲶判得更准, 邮箱设置里能撤销`}>
              {busy ? "标记中…" : "不是钓鱼"}
            </button>
            <span style={{ color: "var(--catfish-text-muted)" }}>识别错了就点, 以后 {addr || "这个发件人"} 的来信不再报</span>
          </>
        )}
        {error && <span style={{ color }}>没改成: {error}</span>}
      </div>
    </div>
  );
}

/** 没报的信: 「⚠ 标为钓鱼」。报了 / 标过的不显示 (红条上有按钮), 只留个占位把后面的按钮推到右边。 */
export function MarkPhishingButton({ result, msg, list }: { result: PhishingScanResult | null; msg: Msg; list: EmailDigestItem[] }) {
  const p = useEffective(result, msg);
  const { busy, error, run } = useMarkAction(msg, list);
  const addr = senderAddress(msg.sender);
  const flagged = p && (p.userMark || p.highestSeverity === "high" || p.highestSeverity === "medium");
  if (flagged || !addr) return <span style={{ marginLeft: "auto" }} />;
  return (
    <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 6 }}>
      {error && <span style={{ fontSize: 12, color: "rgb(185,28,28)" }}>没标上: {error}</span>}
      <button type="button" disabled={busy} onClick={() => void run("phishing")}
              title={`觉得这封是钓鱼: 以后 ${addr} 的来信一律按钓鱼报; 标注只存在你这台电脑上, 邮箱设置里能撤销`}
              style={{ background: "var(--catfish-bg)", color: "var(--catfish-text-muted)", border: "1px solid var(--catfish-border)",
                       borderRadius: 4, padding: "8px 12px", fontSize: 13, cursor: "pointer", fontFamily: "inherit" }}>
        {busy ? "标记中…" : "⚠ 标为钓鱼"}
      </button>
    </span>
  );
}
