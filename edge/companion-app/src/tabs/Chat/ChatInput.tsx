/** 输入框 + 发送按钮 + 停止按钮
 *
 * 快捷键:
 *   Enter        发送
 *   Shift+Enter  换行
 *   Cmd/Ctrl+L   清屏（reset 整个对话）
 */

import { useEffect, useRef, useState, type KeyboardEvent } from "react";

interface Props {
  isStreaming: boolean;
  onSend: (text: string) => void;
  onCancel: () => void;
  onReset: () => void;
}

export default function ChatInput({
  isStreaming,
  onSend,
  onCancel,
  onReset,
}: Props) {
  const [text, setText] = useState("");
  const taRef = useRef<HTMLTextAreaElement>(null);

  // 输入框 auto-grow（最多 200px 高度）
  useEffect(() => {
    const el = taRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 200) + "px";
  }, [text]);

  // mount 后聚焦
  useEffect(() => {
    taRef.current?.focus();
  }, []);

  function submit() {
    const t = text.trim();
    if (!t || isStreaming) return;
    onSend(t);
    setText("");
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    // Cmd/Ctrl+L 清屏
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "l") {
      e.preventDefault();
      if (confirm("清空当前对话？")) onReset();
      return;
    }
    // Shift+Enter: 换行（默认行为）
    if (e.key === "Enter" && e.shiftKey) return;
    // Enter: 发送
    if (e.key === "Enter") {
      e.preventDefault();
      submit();
    }
  }

  return (
    <div
      style={{
        borderTop: "1px solid var(--catfish-border)",
        background: "var(--catfish-bg-elevated)",
        padding: "var(--space-3) var(--space-4)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "flex-end",
          gap: "var(--space-2)",
        }}
      >
        <textarea
          ref={taRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKey}
          placeholder="跟小鲶说话…  (Enter 发送 · Shift+Enter 换行 · Cmd+L 清屏)"
          rows={1}
          style={{
            flex: 1,
            resize: "none",
            border: "1px solid var(--catfish-border)",
            borderRadius: "var(--radius-md)",
            padding: "var(--space-2) var(--space-3)",
            fontSize: 14,
            lineHeight: 1.5,
            fontFamily: "inherit",
            color: "var(--catfish-text)",
            background: "var(--catfish-bg)",
            outline: "none",
            minHeight: 36,
          }}
          disabled={false /* 仍允许写下一个，发送按钮在 streaming 时变停止 */}
        />
        {isStreaming ? (
          <button
            onClick={onCancel}
            style={{
              padding: "var(--space-2) var(--space-3)",
              border: "1px solid var(--status-warn)",
              borderRadius: "var(--radius-sm)",
              background: "transparent",
              color: "var(--status-warn)",
              fontSize: 13,
              fontWeight: 500,
              minWidth: 60,
            }}
          >
            停止
          </button>
        ) : (
          <button
            onClick={submit}
            disabled={!text.trim()}
            style={{
              padding: "var(--space-2) var(--space-4)",
              border: "none",
              borderRadius: "var(--radius-sm)",
              background: text.trim()
                ? "var(--catfish-cyan)"
                : "var(--catfish-border)",
              color: "white",
              fontSize: 13,
              fontWeight: 500,
              minWidth: 60,
              cursor: text.trim() ? "pointer" : "default",
            }}
          >
            发送
          </button>
        )}
      </div>
    </div>
  );
}
