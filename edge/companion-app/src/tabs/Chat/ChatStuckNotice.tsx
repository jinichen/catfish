/** 11/24 鸿波「这是为什么？」: 小鲶答到一半本机服务重启, 那一轮成了「结果未知」, hermes 把整个对话暂停,
 *  之后每句话都只回 `unknown_execution`。这里说人话, 并给一个按钮放弃那一轮 (src-tauri hermes_unstick.rs)。
 *  暂停期间排着的消息一并取消 —— 员工当时看到的都是报错, 不该事后自己跑起来; 解开后重发一句就行。 */
import { useState } from "react";
import { WarningCircle } from "@phosphor-icons/react";
import { invoke } from "@tauri-apps/api/core";
import { Ico } from "../../components/icons";
import { useChatStore } from "../../store/chat";

export function isStuckSession(error: string | undefined): boolean {
  return !!error && /unknown_execution/.test(error);
}

type State = { kind: "idle" } | { kind: "busy" } | { kind: "done"; cancelled: number } | { kind: "fail"; text: string };

export default function ChatStuckNotice() {
  const sessionId = useChatStore((s) => s.persistedSessionId);
  const [st, setSt] = useState<State>({ kind: "idle" });

  const unstick = async () => {
    if (!sessionId) return;
    setSt({ kind: "busy" });
    try {
      const r = await invoke<{ discarded: number; cancelled: number }>("hermes_session_unstick", { sessionId });
      setSt({ kind: "done", cancelled: r.cancelled });
    } catch (e) {
      setSt({ kind: "fail", text: String(e) });
    }
  };

  return (
    <div
      role="status"
      style={{
        marginTop: "var(--space-2)",
        padding: "var(--space-2) var(--space-3)",
        background: "var(--catfish-bg)",
        border: "1px solid color-mix(in srgb, var(--status-warn) 45%, var(--catfish-border))",
        borderRadius: "var(--radius-sm)",
        fontSize: 13,
        color: "var(--catfish-text)",
        display: "flex",
        alignItems: "center",
        gap: "var(--space-3)",
        flexWrap: "wrap",
      }}
    >
      <span style={{ flex: 1, minWidth: 220, lineHeight: 1.6 }}>
        <Ico as={WarningCircle} />
        {st.kind === "done"
          ? `已经解开了${st.cancelled > 0 ? `, 卡住期间排着的 ${st.cancelled} 条没有执行` : ""}。把你要说的话再发一次就行。`
          : st.kind === "fail"
            ? `没解开: ${st.text}`
            : "这个对话卡住了: 上一轮回答到一半时本机服务重启过, 那一轮不知道做没做完, 之后的消息都被挡住。放弃那一轮就能接着聊 (不会重做, 也不会补发卡住期间的消息)。"}
      </span>
      {st.kind !== "done" && sessionId && (
        <button
          type="button"
          className="chat-composer__primary-button"
          disabled={st.kind === "busy"}
          onClick={() => void unstick()}
          style={{ whiteSpace: "nowrap", flex: "none" }}
        >
          {st.kind === "busy" ? "处理中…" : "放弃卡住的那一轮"}
        </button>
      )}
    </div>
  );
}
