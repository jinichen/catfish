/** 早安「客户跟进」(10/12, docs/SALES-FOLLOWUP-PLAN.md §3)。
 *
 * 跟进雷达 (tool-bridge customer_radar.py) 每天早上出四类卡: 客户在等你回复 / 你答应的事到期 /
 * 报价没回音 / 客户冷了。每张卡引用原话, 一键交给小鲶起草 (只起草不发送)。
 * 11/3: 只在有要跟的卡片时出; 没有时问候行里一个小标签 (CustomerChip), 带「+ 加客户」。关卡复用早安「关闭账本」。
 */
import { useState } from "react";

import { advisorClosedAppend, nextLocalMidnight } from "../../lib/advisor_closed";
import { draftPrompt } from "../../lib/customerRadar";
import { salesPromiseDone, type RadarCard } from "../../lib/tauri_sales";
import { useUIStore } from "../../store/ui";
import type { CustomerRadarState } from "./useCustomerRadar";

const COLLAPSED = 3;
const RULE_COLOR: Record<RadarCard["rule"], string> = {
  reply: "var(--status-err)",
  promise: "var(--catfish-accent, #e8864a)",
  quote: "var(--catfish-cyan)",
  cold: "var(--catfish-text-muted)",
};

/** 11/3: 只在有要跟的卡片时出; 没有时客户数和「+ 加客户」在问候行的 CustomerChip 里。数据来自 useCustomerRadar。 */
export default function CustomerFollowupStrip({ radar, onAdd }: { radar: CustomerRadarState; onAdd: () => void }) {
  const { cards, customers, error, load, setError } = radar;
  const [expanded, setExpanded] = useState(false);
  const startEmailChat = useUIStore((s) => s.startEmailChat);

  if (error) return null; // tool-bridge 没起来时不打扰早安主体; 雷达是附加信息
  if (!cards || cards.length === 0) return null;

  async function close(c: RadarCard, status: "done" | "snoozed") {
    await advisorClosedAppend({
      op: "close", status, taskUid: c.uid, title: `${c.label} · ${c.customer}`, ts: new Date().toISOString(),
      by: "button", ...(status === "snoozed" ? { until: nextLocalMidnight() } : {}),
    });
    await load();
  }

  async function fulfilled(c: RadarCard) {
    try {
      await salesPromiseDone(c.customer, c.promise_text ?? c.quote);
      await load();
    } catch (e) {
      setError(String(e));
    }
  }

  const shown = expanded ? cards : cards.slice(0, COLLAPSED);
  return (
    <div className="customer-followup" style={{ marginBottom: "var(--space-2)", display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: "var(--space-2)", fontSize: 13 }}>
        <strong>🤝 客户跟进</strong>
        <span style={{ color: "var(--catfish-text-muted)" }}>
          {`${cards.length} 件要跟 · ${customers} 个客户`}
        </span>
        <button type="button" onClick={onAdd}
          style={{ border: "none", background: "transparent", color: "var(--catfish-cyan)", cursor: "pointer", fontSize: 12, padding: 0 }}>
          + 加客户
        </button>
      </div>
      {shown.map((c) => (
        <div
          key={c.uid}
          style={{
            display: "flex", alignItems: "center", gap: "var(--space-3)", flexWrap: "wrap",
            padding: "8px 12px", borderRadius: "var(--radius-sm)", background: "var(--catfish-bg-elevated)",
            border: "1px solid var(--catfish-border)", borderLeft: `3px solid ${RULE_COLOR[c.rule]}`, fontSize: 13,
          }}
        >
          <div style={{ flex: 1, minWidth: 240 }}>
            <div>
              <span style={{ color: RULE_COLOR[c.rule], fontWeight: 600 }}>{c.label}</span>
              {" · "}<strong>{c.customer}</strong>
              {c.stage && <span style={{ color: "var(--catfish-text-muted)" }}> · {c.stage}</span>}
              <span style={{ color: "var(--catfish-text-muted)" }}> · {c.age_days} 天</span>
            </div>
            {c.quote && <div style={{ color: "var(--catfish-text-muted)", marginTop: 2 }}>“{c.quote}”</div>}
          </div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            <button type="button" className="chat-composer__primary-button" onClick={() => startEmailChat(c.uid, draftPrompt(c))}>
              让小鲶起草
            </button>
            {c.rule === "promise" && c.promise_text && (
              <button type="button" className="chat-composer__secondary-button" onClick={() => void fulfilled(c)}>已兑现</button>
            )}
            <button type="button" className="chat-composer__secondary-button" onClick={() => void close(c, "snoozed")}>今天先不看</button>
            <button type="button" className="chat-composer__secondary-button" onClick={() => void close(c, "done")}>已处理</button>
          </div>
        </div>
      ))}
      {cards.length > COLLAPSED && (
        <button
          type="button"
          onClick={() => setExpanded(!expanded)}
          style={{ alignSelf: "flex-start", border: "none", background: "transparent", color: "var(--catfish-cyan)", cursor: "pointer", fontSize: 12 }}
        >
          {expanded ? "收起" : `还有 ${cards.length - COLLAPSED} 件 ▾`}
        </button>
      )}
    </div>
  );
}
