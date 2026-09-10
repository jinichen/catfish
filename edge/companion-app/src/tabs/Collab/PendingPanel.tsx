/**
 * 横向协同待审批 (9/10, 配插件 P49)。
 *
 * 员工 A 的小鲶让我的小鲶干活时, 两类事要我点头 —— 工具调用 (跟自己对话时
 * 弹的审批一样) 和出站回复 (我的本地数据要发回给 A)。逻辑全在
 * lib/roomLink.ts, 这里只管显示和按钮。
 *
 * 9/10 挪进「协同」tab 右栏, 空态显示说明而不是隐藏 —— 员工得知道这一栏是干嘛的。
 */
import { useState } from "react";

import type { RoomLinkApproval, RoomLinkOutput } from "../../lib/roomLink";
import type { InboxRequest } from "../../lib/roomLinkInbox";
import {
  pendingCount,
  resolveApproval,
  resolveInbox,
  resolveOutput,
  useRoomLink,
} from "../../lib/roomLinkStore";
import { Btn, cardStyle, ErrorLine, itemStyle } from "./roomLinkUi";

export default function PendingPanel() {
  const [busy, setBusy] = useState<string | null>(null); // run_id 正在处理
  const [error, setError] = useState<string | null>(null);
  // 三类都从 roomLinkStore 来 (邮筒 + P49 探针都只在那里轮询一次), 这里只订阅。
  const state = useRoomLink();
  const { inbox, pending } = state;
  const hasInbox = inbox.length > 0;

  async function onApproval(a: RoomLinkApproval, choice: "once" | "deny") {
    setBusy(a.run_id);
    setError(null);
    try {
      await resolveApproval(a, choice);
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
      // gone = 已经超时被 hermes 自己收尾了, 列表里摘掉就行, 不算错
      const r = await resolveOutput(o, choice);
      if (r.gone) setError("这条已超时 (10 分钟没处理), 对方会收到「未放行」。");
    } catch (e) {
      setError(`出站审批失败: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(null);
    }
  }

  async function onInbox(item: InboxRequest, choice: "approve" | "deny") {
    const key = `inbox-${item.id}`;
    setBusy(key);
    setError(null);
    try {
      // 拒绝 = 本地丢掉不回信, 对方等超时
      await resolveInbox(item, choice);
    } catch (e) {
      setError(`回复同事失败: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(null);
    }
  }

  const total = pendingCount(state);

  return (
    <div style={cardStyle}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <h3 style={{ margin: 0, fontSize: "var(--text-md)", fontWeight: 600 }}>
          等我点头
        </h3>
        <span style={{ fontSize: "var(--text-xs)", color: "var(--catfish-text-muted)" }}>
          {total > 0 ? `${total} 条待处理` : "暂无"}
        </span>
      </div>

      {total === 0 && (
        <div style={{ fontSize: "var(--text-sm)", color: "var(--catfish-text-muted)" }}>
          同事想借用你的小鲶、或对方的小鲶要在你机器上跑工具、要把回复发回去时, 都会在这里等你点头。
          不点头就什么都不会发生。
        </div>
      )}

      {error && <ErrorLine>{error}</ErrorLine>}

      {hasInbox && (
        <Section
          title="同事想让你的小鲶帮忙"
          hint="同意只是允许对方来找你的小鲶。之后每个工具、每条发回去的回复, 都还会在这里再问你一次。一小时后自动失效。"
        >
          {inbox.map((item) => (
            <Item key={item.id} busy={busy === `inbox-${item.id}`}>
              <div style={{ fontSize: "var(--text-sm)" }}>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>{item.from}</div>
                <div style={{ whiteSpace: "pre-wrap" }}>{item.request.note}</div>
              </div>
              <Buttons
                busy={busy === `inbox-${item.id}`}
                onYes={() => onInbox(item, "approve")}
                onNo={() => onInbox(item, "deny")}
                yes="同意, 可以来找我"
              />
            </Item>
          ))}
        </Section>
      )}

      {pending && pending.approvals.length > 0 && (
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

      {pending && pending.outputs.length > 0 && (
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
  return <div style={{ ...itemStyle, opacity: busy ? 0.6 : 1 }}>{children}</div>;
}

function Buttons({ busy, onYes, onNo, yes }: { busy: boolean; onYes: () => void; onNo: () => void; yes: string }) {
  return (
    <div style={{ display: "flex", gap: "var(--space-2)", justifyContent: "flex-end" }}>
      <Btn kind="ghost" disabled={busy} onClick={onNo}>拒绝</Btn>
      <Btn kind="primary" disabled={busy} onClick={onYes}>{yes}</Btn>
    </div>
  );
}
