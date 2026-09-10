/**
 * 横向协同待审批 (9/10, 配插件 P49)。
 *
 * 员工 A 的小鲶让我的小鲶干活时, 两类事要我点头 —— 工具调用 (跟自己对话时
 * 弹的审批一样) 和出站回复 (我的本地数据要发回给 A)。逻辑全在
 * lib/roomLink.ts, 这里只管显示和按钮。
 *
 * 没有待审批时 **不渲染** (return null): 这个 Card 只在真有跨机器协作时才
 * 有意义, 平时不该占工作台一格。
 */
import { useEffect, useState } from "react";

import {
  hasPending,
  resolveRoomLinkApproval,
  resolveRoomLinkOutput,
  startRoomLinkPolling,
  type RoomLinkApproval,
  type RoomLinkOutput,
  type RoomLinkPending,
} from "../../lib/roomLink";

export default function RoomLinkPendingCard() {
  const [pending, setPending] = useState<RoomLinkPending | null>(null);
  const [busy, setBusy] = useState<string | null>(null); // run_id 正在处理
  const [error, setError] = useState<string | null>(null);

  useEffect(() => startRoomLinkPolling(setPending), []);

  if (!pending || !hasPending(pending)) return null;

  // 点了按钮先从本地列表里摘掉, 不等下一轮轮询 —— 不然按钮点完 3 秒内还在,
  // 员工会以为没生效再点一次。
  const dropLocal = (kind: "approvals" | "outputs", runId: string) =>
    setPending((p) =>
      p ? { ...p, [kind]: p[kind].filter((x) => x.run_id !== runId) } : p,
    );

  async function onApproval(a: RoomLinkApproval, choice: "once" | "deny") {
    setBusy(a.run_id);
    setError(null);
    try {
      await resolveRoomLinkApproval(a.run_id, choice);
      dropLocal("approvals", a.run_id);
    } catch (e) {
      setError(`工具审批失败: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(null);
    }
  }

  async function onOutput(o: RoomLinkOutput, choice: "approve" | "deny") {
    setBusy(o.run_id);
    setError(null);
    try {
      const r = await resolveRoomLinkOutput(o.run_id, choice);
      // gone = 已经超时被 hermes 自己收尾了, 列表里摘掉就行, 不算错
      dropLocal("outputs", o.run_id);
      if (r.gone) setError("这条已超时 (10 分钟没处理), 对方会收到「未放行」。");
    } catch (e) {
      setError(`出站审批失败: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(null);
    }
  }

  const total = pending.approvals.length + pending.outputs.length;

  return (
    <div
      style={{
        background: "var(--catfish-bg-elevated)",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-4)",
        height: "100%",
        boxSizing: "border-box",
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-3)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <h3 style={{ margin: 0, fontSize: "var(--text-md)", fontWeight: 600 }}>
          🤝 同事的小鲶在等你点头
        </h3>
        <span style={{ fontSize: "var(--text-xs)", color: "var(--catfish-text-muted)" }}>
          {total} 条待处理
        </span>
      </div>

      {error && (
        <div
          style={{
            fontSize: 11,
            color: "var(--status-error, #dc2626)",
            background: "rgba(220, 38, 38, 0.06)",
            padding: "4px 8px",
            borderRadius: 4,
          }}
        >
          {error}
        </div>
      )}

      {pending.approvals.length > 0 && (
        <Section title="要在你机器上跑的工具" hint="跟你自己对话时弹的审批是同一回事, 只是发起的是同事的小鲶。">
          {pending.approvals.map((a) => (
            <Item key={a.run_id} busy={busy === a.run_id}>
              <div style={{ fontSize: "var(--text-sm)" }}>
                {a.description && <div style={{ marginBottom: 4 }}>{a.description}</div>}
                {a.command && (
                  <code
                    style={{
                      display: "block",
                      fontSize: 11,
                      padding: "4px 6px",
                      background: "var(--catfish-bg-subtle, rgba(0,0,0,0.04))",
                      borderRadius: 4,
                      whiteSpace: "pre-wrap",
                      wordBreak: "break-all",
                    }}
                  >
                    {a.command}
                  </code>
                )}
              </div>
              <Buttons
                busy={busy === a.run_id}
                onYes={() => onApproval(a, "once")}
                onNo={() => onApproval(a, "deny")}
                yes="允许这一次"
              />
            </Item>
          ))}
        </Section>
      )}

      {pending.outputs.length > 0 && (
        <Section title="要发回给同事的回复" hint="这是你小鲶起草的、会离开你机器的内容。放行才发, 拒绝对方会收到「未放行」。">
          {pending.outputs.map((o) => (
            <Item key={o.run_id} busy={busy === o.run_id}>
              <div
                style={{
                  fontSize: "var(--text-sm)",
                  whiteSpace: "pre-wrap",
                  maxHeight: 160,
                  overflow: "auto",
                }}
              >
                {o.final_response}
              </div>
              <Buttons
                busy={busy === o.run_id}
                onYes={() => onOutput(o, "approve")}
                onNo={() => onOutput(o, "deny")}
                yes="放行发回"
              />
            </Item>
          ))}
        </Section>
      )}
    </div>
  );
}

function Section({ title, hint, children }: { title: string; hint: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
      <div style={{ fontSize: "var(--text-sm)", fontWeight: 600 }}>{title}</div>
      <div style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>{hint}</div>
      {children}
    </div>
  );
}

function Item({ busy, children }: { busy: boolean; children: React.ReactNode }) {
  return (
    <div
      style={{
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-sm, 6px)",
        padding: "var(--space-3)",
        opacity: busy ? 0.6 : 1,
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-2)",
      }}
    >
      {children}
    </div>
  );
}

function Buttons({ busy, onYes, onNo, yes }: { busy: boolean; onYes: () => void; onNo: () => void; yes: string }) {
  return (
    <div style={{ display: "flex", gap: "var(--space-2)", justifyContent: "flex-end" }}>
      <button type="button" disabled={busy} onClick={onNo} className="btn btn--ghost btn--sm">
        拒绝
      </button>
      <button type="button" disabled={busy} onClick={onYes} className="btn btn--primary btn--sm">
        {yes}
      </button>
    </div>
  );
}
