/** 助手消息下方的 feedback 行 (BL-MM6 五一 sprint 5/5 晚)
 *
 * 三个按钮: 👍 / 👎 / "改". 状态:
 *   - 未标 → 三个按钮都灰
 *   - 已标 thumb_up → 👍 高亮
 *   - 已标 thumb_down → 弹小 input "为啥不好?", 写完写盘
 *   - 已标 edit → 弹小 input "改成怎样?", 写完写盘
 *
 * 写完后状态记 in-memory (不再展示按钮, 显"已记录"). 不阻塞 chat.
 *
 * 注意:
 *   - 流式中 (showCaret=true) 不显按钮, 防员工误点未完成消息
 *   - 错误消息 (status=error) 也显按钮 (👎 + 改在错误消息上意义最大)
 *   - persistedSessionId 没拿到时仍可标 (后台用空 string), 不卡 UX
 */

import { useState } from "react";
import { feedbackRecord } from "../../lib/tauri";
import { useChatStore } from "../../store/chat";

interface Props {
  messageId: string;
  /** 助手消息内容 (前 200 字会作 preview 写入 jsonl, LLM 后续看到能定位是哪条) */
  preview: string;
  /** 流式中不显按钮 */
  hidden?: boolean;
}

type Status = "idle" | "writing-down" | "writing-edit" | "saved";

export default function FeedbackButtons({ messageId, preview, hidden }: Props) {
  const [status, setStatus] = useState<Status>("idle");
  const [comment, setComment] = useState("");
  const [savedKind, setSavedKind] = useState<"thumb_up" | "thumb_down" | "edit" | null>(null);
  const [error, setError] = useState<string | null>(null);

  // 直接从 store 拿 sessionId, 不需 prop drilling
  const sessionId = useChatStore((s) => s.persistedSessionId) || "";

  if (hidden) return null;

  const submit = async (
    kind: "thumb_up" | "thumb_down" | "edit",
    commentArg?: string,
  ) => {
    setError(null);
    try {
      await feedbackRecord({
        kind,
        sessionId,
        messageId,
        preview,
        comment: commentArg,
      });
      setSavedKind(kind);
      setStatus("saved");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  // 已保存: 显简短状态 (淡化, 不再可点)
  if (status === "saved") {
    const label =
      savedKind === "thumb_up"
        ? "👍 已记"
        : savedKind === "thumb_down"
        ? "👎 已记: " + (comment || "无评论")
        : "✏️ 已记: " + (comment || "无评论");
    return (
      <div
        style={{
          marginTop: "var(--space-1)",
          fontSize: 11,
          color: "var(--catfish-text-muted)",
          fontStyle: "italic",
        }}
      >
        {label}
      </div>
    );
  }

  // 写评论模式 (👎 / 改)
  if (status === "writing-down" || status === "writing-edit") {
    const isEdit = status === "writing-edit";
    return (
      <div
        style={{
          marginTop: "var(--space-2)",
          display: "flex",
          gap: 6,
          alignItems: "stretch",
        }}
      >
        <input
          type="text"
          autoFocus
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              const k = isEdit ? "edit" : "thumb_down";
              void submit(k, comment.trim() || undefined);
            }
            if (e.key === "Escape") {
              setStatus("idle");
              setComment("");
            }
          }}
          placeholder={isEdit ? "改成怎样? (Enter 提交 / Esc 取消)" : "为啥不好? (Enter 提交 / Esc 取消)"}
          style={{
            flex: 1,
            fontSize: 12,
            padding: "4px 8px",
            border: "1px solid var(--catfish-border)",
            borderRadius: 4,
            background: "var(--catfish-bg)",
            color: "var(--catfish-text)",
            outline: "none",
          }}
        />
        <button
          type="button"
          onClick={() => void submit(isEdit ? "edit" : "thumb_down", comment.trim() || undefined)}
          style={{
            padding: "4px 10px",
            border: "1px solid var(--catfish-border)",
            borderRadius: 4,
            background: "var(--catfish-cyan-dim)",
            color: "var(--catfish-text)",
            cursor: "pointer",
            fontSize: 12,
          }}
        >
          提交
        </button>
        <button
          type="button"
          onClick={() => {
            setStatus("idle");
            setComment("");
          }}
          style={{
            padding: "4px 10px",
            border: "1px solid var(--catfish-border)",
            borderRadius: 4,
            background: "transparent",
            color: "var(--catfish-text-muted)",
            cursor: "pointer",
            fontSize: 12,
          }}
        >
          取消
        </button>
      </div>
    );
  }

  // idle: 三个按钮
  return (
    <div
      style={{
        marginTop: "var(--space-1)",
        display: "flex",
        gap: 4,
        alignItems: "center",
      }}
    >
      <FeedbackBtn
        title="回答得好"
        onClick={() => void submit("thumb_up")}
      >
        👍
      </FeedbackBtn>
      <FeedbackBtn
        title="回答不行 (可写为啥)"
        onClick={() => setStatus("writing-down")}
      >
        👎
      </FeedbackBtn>
      <FeedbackBtn
        title="想要不一样的回答 (写改成怎样)"
        onClick={() => setStatus("writing-edit")}
      >
        ✏️ 改
      </FeedbackBtn>
      {error && (
        <span style={{ fontSize: 11, color: "var(--status-err)", marginLeft: 6 }}>
          {error}
        </span>
      )}
    </div>
  );
}

function FeedbackBtn({
  title,
  onClick,
  children,
}: {
  title: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      style={{
        padding: "2px 8px",
        fontSize: 11,
        border: "1px solid transparent",
        borderRadius: 4,
        background: "transparent",
        color: "var(--catfish-text-muted)",
        cursor: "pointer",
        opacity: 0.5,
        transition: "opacity 100ms, background 100ms",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.opacity = "1";
        e.currentTarget.style.background = "var(--catfish-bg-elevated)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.opacity = "0.5";
        e.currentTarget.style.background = "transparent";
      }}
    >
      {children}
    </button>
  );
}
