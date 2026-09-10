/**
 * 请同事帮忙 (P50, 9/10) —— 横向协同的发起端 (A 侧)。
 *
 * 填同事邮箱 + 想让对方小鲶做什么 → 投邮筒 → 等对方在自己的 Companion 里点头
 * → 拿到 grant 后直连对方 8642 派工 → 轮询结果。全部状态机在 lib/roomLinkStore,
 * 这里只显示和按钮。放在「协同」tab 左栏 (9/10 鸿波: 塞工作台「今日」区不人性)。
 *
 * 员工看到的每一步都对应对方那边一次人工点头: 同意被找 / 批工具 / 放行回复。
 * 所以状态文案写的是「对方在做什么」, 不是技术阶段名。
 */
import { useState } from "react";

import { NOTE_MAX_CHARS } from "../../lib/roomLinkHandshake";
import {
  dismissOutgoing,
  sendRequest,
  useRoomLink,
  type OutgoingRequest,
} from "../../lib/roomLinkStore";
import { Btn, cardStyle, ErrorLine, inputStyle, itemStyle } from "./roomLinkUi";

function phaseLabel(o: OutgoingRequest): { text: string; tone: "muted" | "ok" | "error" } {
  switch (o.phase) {
    case "waiting_grant":
      return { text: "已送达, 等对方同意", tone: "muted" };
    case "dispatching":
      return { text: "对方同意了, 正在派工", tone: "muted" };
    case "running":
      return o.run_status === "waiting_for_approval"
        ? { text: "对方正在审批一个工具调用", tone: "muted" }
        : { text: "对方的小鲶在干活", tone: "muted" };
    case "done":
      return { text: "完成, 对方已放行回复", tone: "ok" };
    case "failed":
      return {
        text: o.error?.includes("outbound_declined")
          ? "对方看了回复, 没放行"
          : o.error?.includes("outbound_timed_out")
            ? "对方 10 分钟内没处理回复"
            : `失败: ${o.error ?? "未知"}`,
        tone: "error",
      };
  }
}

export default function AskColleaguePanel() {
  const { outgoing } = useRoomLink();
  const [to, setTo] = useState("");
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSend() {
    setSending(true);
    setError(null);
    try {
      await sendRequest(to, text);
      setText("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSending(false);
    }
  }

  const canSend = !sending && to.trim().length > 0 && text.trim().length > 0 && text.length <= NOTE_MAX_CHARS;

  return (
    <div style={cardStyle}>
      <div>
        <h3 style={{ margin: 0, fontSize: "var(--text-md)", fontWeight: 600 }}>🤝 请同事的小鲶帮忙</h3>
        <div style={{ fontSize: 11, color: "var(--catfish-text-muted)", marginTop: 4 }}>
          对方要先在自己的小鲶里同意; 之后每个工具、每条发回来的回复, 也都由对方本人放行。中央只转信, 看不到内容。
        </div>
      </div>

      <input
        type="email"
        value={to}
        onChange={(e) => setTo(e.target.value)}
        placeholder="同事的邮箱 (跟登录小鲶用的一样)"
        style={inputStyle}
      />
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="想让对方的小鲶做什么 — 对方同意前看到的就是这段话"
        rows={3}
        maxLength={NOTE_MAX_CHARS}
        style={{ ...inputStyle, resize: "vertical" }}
      />
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
          {text.length}/{NOTE_MAX_CHARS}
        </span>
        <Btn kind="primary" disabled={!canSend} onClick={onSend}>
          {sending ? "发送中…" : "发给同事"}
        </Btn>
      </div>

      {error && <ErrorLine>{error}</ErrorLine>}

      {outgoing.map((o) => {
        const { text: label, tone } = phaseLabel(o);
        const color =
          tone === "ok" ? "var(--status-ok, #16a34a)"
          : tone === "error" ? "var(--status-error, #dc2626)"
          : "var(--catfish-text-muted)";
        const dismissible = o.phase === "done" || o.phase === "failed" || o.phase === "waiting_grant";
        return (
          <div key={o.room_id} style={itemStyle}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
              <span style={{ fontSize: "var(--text-sm)", fontWeight: 600 }}>{o.to}</span>
              <span style={{ fontSize: 11, color }}>{label}</span>
            </div>
            <div style={{ fontSize: "var(--text-sm)", whiteSpace: "pre-wrap" }}>{o.text}</div>
            {o.output && (
              <div
                style={{
                  fontSize: "var(--text-sm)",
                  whiteSpace: "pre-wrap",
                  maxHeight: 200,
                  overflow: "auto",
                  padding: "6px 8px",
                  background: "var(--catfish-bg-subtle, rgba(0,0,0,0.04))",
                  borderRadius: 4,
                }}
              >
                {o.output}
              </div>
            )}
            {dismissible && (
              <div style={{ display: "flex", justifyContent: "flex-end" }}>
                <Btn kind="ghost" onClick={() => dismissOutgoing(o.room_id)}>
                  {o.phase === "waiting_grant" ? "不等了" : "收起"}
                </Btn>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
