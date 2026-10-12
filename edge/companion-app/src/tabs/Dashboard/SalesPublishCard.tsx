/** 发布给主管 (销售跟进阶段 3, docs/SALES-FOLLOWUP-PLAN.md §11)。
 *
 * 客户档案只在本机; 点「发布」才把下面这张表 (阶段 / 金额 / 下一步 / 提醒, 不含聊天原话和联系方式)
 * 发到本部门知识库。同部门同事看不到, 只有部门主管和管理员在中央「部门」页看全组漏斗。
 */
import { useEffect, useState } from "react";

import { salesPublish, salesPublishPreview, salesPublishWithdraw, type PublishPreview } from "../../lib/tauri_sales";

const card: React.CSSProperties = {
  background: "var(--catfish-bg-elevated)", border: "1px solid var(--catfish-border)",
  borderRadius: "var(--radius-md)", padding: "var(--space-4)", display: "flex", flexDirection: "column", gap: "var(--space-3)",
};
const muted: React.CSSProperties = { fontSize: 13, color: "var(--catfish-text-muted)" };
const cell: React.CSSProperties = { padding: "4px 8px", borderBottom: "1px solid var(--catfish-border)", textAlign: "left", verticalAlign: "top" };

const when = (iso: string | null) => (iso ? iso.replace("T", " ").slice(0, 16) : "");

export default function SalesPublishCard() {
  const [pv, setPv] = useState<PublishPreview | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const load = () => salesPublishPreview().then(setPv).catch((e) => setMsg({ ok: false, text: `读不到客户档案: ${e}` }));
  useEffect(() => void load(), []);

  async function run(fn: () => Promise<unknown>, ok: string) {
    setBusy(true);
    setMsg(null);
    try {
      await fn();
      setMsg({ ok: true, text: ok });
      await load();
    } catch (e) {
      setMsg({ ok: false, text: String(e) });
    } finally {
      setBusy(false);
    }
  }

  const rows = pv?.summary.customers ?? [];
  return (
    <div style={card}>
      <h3 style={{ margin: 0 }}>发布给主管</h3>
      <div style={muted}>
        把下面这张表发到你部门的知识库, 部门主管在中央管理台「部门」页能看到全组漏斗。只发这些字段,
        <b>不发</b>聊天原话、时间线和联系人电话邮箱; 同部门同事看不到。点了才发, 之后客户有变化再点一次更新。
      </div>
      {pv?.published_at ? (
        <div style={{ fontSize: 13 }}>上次发布: {when(pv.published_at)}{pv.namespace ? ` · ${pv.namespace}` : ""}</div>
      ) : (
        <div style={muted}>还没发布过。</div>
      )}
      {rows.length === 0 ? (
        <div style={muted}>还没有客户档案。</div>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table style={{ borderCollapse: "collapse", fontSize: 13, minWidth: 560 }}>
            <thead>
              <tr>{["客户", "阶段", "预计金额", "最近联系", "下一步", "提醒"].map((h) => <th key={h} style={{ ...cell, ...muted }}>{h}</th>)}</tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.name}>
                  <td style={cell}>{r.name}</td>
                  <td style={cell}>{r.stage}</td>
                  <td style={cell}>{r.deal_size}</td>
                  <td style={cell}>{r.last_contact}</td>
                  <td style={cell}>{r.next_action}{r.next_action_due ? ` (${r.next_action_due})` : ""}</td>
                  <td style={cell}>{r.flags.join("、")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div style={{ display: "flex", gap: 8 }}>
        <button type="button" className="chat-composer__primary-button" disabled={busy || rows.length === 0}
          onClick={() => void run(salesPublish, "已发布。主管刷新部门页就能看到。")}>
          {busy ? "处理中…" : pv?.published_at ? "更新发布" : "发布"}
        </button>
        {pv?.published_at && (
          <button type="button" className="chat-composer__secondary-button" disabled={busy}
            onClick={() => void run(salesPublishWithdraw, "已撤回, 主管那边不再显示你的漏斗。")}>
            撤回
          </button>
        )}
      </div>
      {msg && <div style={{ fontSize: 13, color: msg.ok ? "var(--status-ok)" : "var(--status-err)" }}>{msg.text}</div>}
    </div>
  );
}
