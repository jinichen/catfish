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
import { feedbackRecord, wikiSaveChatMessage } from "../../lib/tauri";
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
  // BL-CATFISH-WIKI-MODE P1.2.4 (6/4): wiki save 真 inline 状态 — Tauri webview
  // 禁 native window.alert, 点击后只能用 inline label 显示反馈.
  const [wikiSavedPath, setWikiSavedPath] = useState<string | null>(null);
  const [wikiSaving, setWikiSaving] = useState(false);

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
      {wikiSavedPath ? (
        <span
          style={{
            fontSize: 11,
            color: "var(--status-ok, #6c8c5a)",
            paddingLeft: 6,
          }}
          title={wikiSavedPath}
        >
          ✓ 已存 wiki ({wikiSavedPath.split("/").pop()})
        </span>
      ) : (
        <FeedbackBtn
          title="把这轮 Q&A 存进 wiki/queries/, 自动抽 entity/concept (BL-CATFISH-WIKI-MODE P1.2)"
          onClick={async () => {
            // P1.2.2 ship (6/4): 调 tauri command wiki_save_chat_message,
            // 写 ~/.catfish/wiki/queries/<date>-<slug>.md (frontmatter + Q&A).
            // P1.2.3 plugin watch 触发 Analysis/Generation ingest.
            // P1.2.4 (6/4): inline state — Tauri webview 禁 native alert,
            // 改 setWikiSavedPath / wikiSaving / setError 显示状态.
            if (wikiSaving) return;
            setWikiSaving(true);
            setError(null);
            try {
              // 从 store 拿当前 messages, 找 assistant message 真前一条 user
              const allMessages = useChatStore.getState().messages;
              const assistantIdx = allMessages.findIndex((m) => m.id === messageId);
              if (assistantIdx < 0) {
                setError("找不到当前 assistant 消息");
                return;
              }
              // 倒着找最近一条 user
              let userMsg = "";
              for (let i = assistantIdx - 1; i >= 0; i--) {
                if (allMessages[i].role === "user") {
                  userMsg = allMessages[i].content;
                  break;
                }
              }
              if (!userMsg) {
                setError("找不到对应 user message");
                return;
              }
              const assistantMsg = allMessages[assistantIdx].content;
              const now = new Date();
              const pad = (n: number) => String(n).padStart(2, "0");
              const date = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
              const time = `${pad(now.getHours())}:${pad(now.getMinutes())}`;
              const result = await wikiSaveChatMessage({
                date,
                time,
                sessionId,
                messageId,
                userMessage: userMsg,
                assistantResponse: assistantMsg,
              });
              setWikiSavedPath(result.path);
            } catch (e) {
              setError(String(e));
            } finally {
              setWikiSaving(false);
            }
          }}
        >
          {wikiSaving ? "⏳ 存…" : "💾 存 wiki"}
        </FeedbackBtn>
      )}
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
