/** ChatInput QueuedMessagesStrip — 抽自 ChatInput.tsx (5/20 拆分).
 *
 * BL-HERMES013-RED-1A (5/13 借鉴 Hermes 0.13 ACP /queue): streaming 中员工
 * 排队下条消息, 当前 [DONE] 后自动发. 这卡显排队中的消息 + 删除按钮.
 *
 * 10/10: 点了停止的任务不会接着发排队消息 (停止 = 员工叫停, 不该紧接着
 * 又跑起来)。这时队列不再自动走, 给一个"现在发"让员工自己决定。
 */

import { useChatStore } from "../../../store/chat";
import type { Attachment } from "../../../types/chat";


interface Props {
  isStreaming: boolean;
  onSend: (text: string, attachments: Attachment[]) => void;
}

function QueuedMessagesStrip({ isStreaming, onSend }: Props) {
  const queue = useChatStore((s) => s.queue);
  const dequeue = useChatStore((s) => s.dequeueMessage);
  const removeQueued = useChatStore((s) => s.removeQueuedMessage);
  const clearQueue = useChatStore((s) => s.clearQueue);

  if (queue.length === 0) return null;

  return (
    <div
      style={{
        marginBottom: "var(--space-2)",
        padding: "var(--space-1) var(--space-2)",
        background: "var(--catfish-cyan-tint)",
        border: "1px dashed var(--catfish-cyan)",
        borderRadius: "var(--radius-sm)",
        fontSize: 11,
        color: "var(--catfish-cyan)",
      }}
    >
      <div style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        marginBottom: queue.length > 0 ? 4 : 0,
      }}>
        <span style={{ fontWeight: 600 }}>
          {isStreaming
            ? `⏳ 排队 ${queue.length} 条 (当前任务跑完自动发)`
            : `⏸ 排队 ${queue.length} 条 (任务已停止, 不会自动发)`}
        </span>
        {!isStreaming && (
          <button
            onClick={() => {
              const head = dequeue();
              if (head) onSend(head.text, head.attachments);
            }}
            title="发出排在第一的那条"
            style={{
              background: "transparent",
              border: "none",
              color: "var(--catfish-cyan)",
              cursor: "pointer",
              fontSize: 11,
              fontWeight: 600,
              padding: "0 4px",
              marginLeft: "auto",
            }}
          >
            现在发
          </button>
        )}
        {queue.length > 1 && (
          <button
            onClick={clearQueue}
            title="清空整个队列"
            style={{
              background: "transparent",
              border: "none",
              color: "var(--catfish-cyan)",
              cursor: "pointer",
              fontSize: 11,
              padding: "0 4px",
            }}
          >
            清空
          </button>
        )}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        {queue.map((q, i) => (
          <div
            key={q.id}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 4,
              fontSize: 11,
            }}
          >
            <span style={{ opacity: 0.6, minWidth: 14 }}>{i + 1}.</span>
            <span
              style={{
                flex: 1,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
                color: "var(--catfish-text)",
              }}
              title={q.text}
            >
              {q.text || "(只有附件)"}
            </span>
            {q.attachments.length > 0 && (
              <span style={{ opacity: 0.7 }}>📎{q.attachments.length}</span>
            )}
            <button
              onClick={() => removeQueued(q.id)}
              title="撤回这一条排队"
              style={{
                background: "transparent",
                border: "none",
                color: "var(--catfish-cyan)",
                cursor: "pointer",
                fontSize: 11,
                padding: "0 4px",
                opacity: 0.7,
              }}
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}


export default QueuedMessagesStrip;
