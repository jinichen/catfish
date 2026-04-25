/** 单个 tool 调用展示 —— 折叠式,默认收起,点开看 args / result */

import { useState } from "react";
import type { ToolCall } from "../../types/chat";

interface Props {
  call: ToolCall;
}

const STATUS_EMOJI: Record<ToolCall["status"], string> = {
  pending: "⏳",
  running: "🔧",
  done: "✓",
  error: "✗",
};

const STATUS_COLOR: Record<ToolCall["status"], string> = {
  pending: "var(--catfish-text-muted)",
  running: "var(--catfish-cyan-dim)",
  done: "var(--status-ok)",
  error: "var(--status-err)",
};

export default function ChatToolCall({ call }: Props) {
  const [open, setOpen] = useState(false);

  const argsStr = JSON.stringify(call.args, null, 2);
  const resultStr =
    typeof call.result === "string"
      ? call.result
      : call.result !== undefined
        ? JSON.stringify(call.result, null, 2)
        : "";

  // result 第一层尝试 parse 一下美化(hermes 返回是 JSON 字符串)
  let resultDisplay = resultStr;
  if (resultStr.trim().startsWith("{") || resultStr.trim().startsWith("[")) {
    try {
      const parsed = JSON.parse(resultStr);
      resultDisplay = JSON.stringify(parsed, null, 2);
    } catch {
      // 不是 JSON,原样
    }
  }

  return (
    <div
      style={{
        margin: "var(--space-2) 0",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-sm)",
        background: "var(--catfish-bg)",
        overflow: "hidden",
      }}
    >
      <div
        onClick={() => setOpen(!open)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-2)",
          padding: "6px var(--space-3)",
          cursor: "pointer",
          fontFamily: "var(--font-mono)",
          fontSize: 12,
          userSelect: "none",
        }}
      >
        <span style={{ color: STATUS_COLOR[call.status], width: 14 }}>
          {STATUS_EMOJI[call.status]}
        </span>
        <span
          style={{
            color: "var(--catfish-text-muted)",
            fontSize: 10,
          }}
        >
          {open ? "▼" : "▶"}
        </span>
        <strong style={{ fontSize: 12 }}>{call.name}</strong>
        <span
          style={{
            color: "var(--catfish-text-muted)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            flex: 1,
            minWidth: 0,
          }}
        >
          ({argsPreview(call.args)})
        </span>
        {call.status === "running" && (
          <span style={{ fontSize: 11, color: "var(--catfish-cyan-dim)" }}>
            执行中…
          </span>
        )}
      </div>
      {open && (
        <div
          style={{
            borderTop: "1px solid var(--catfish-border)",
            padding: "var(--space-3)",
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            lineHeight: 1.5,
          }}
        >
          <Section label="参数">
            <pre style={preStyle}>{argsStr}</pre>
          </Section>
          {(call.status === "done" || call.status === "error") && (
            <Section label={call.status === "error" ? "错误" : "结果"}>
              <pre
                style={{
                  ...preStyle,
                  color:
                    call.status === "error"
                      ? "var(--status-err)"
                      : "var(--catfish-text)",
                  maxHeight: 300,
                  overflow: "auto",
                }}
              >
                {call.status === "error"
                  ? call.error || resultDisplay
                  : resultDisplay || "(空)"}
              </pre>
            </Section>
          )}
        </div>
      )}
    </div>
  );
}

const preStyle: React.CSSProperties = {
  margin: "4px 0 0",
  padding: 0,
  background: "transparent",
  border: "none",
  whiteSpace: "pre-wrap",
  wordBreak: "break-all",
};

function Section({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div style={{ marginBottom: "var(--space-2)" }}>
      <div
        style={{
          fontSize: 10,
          color: "var(--catfish-text-muted)",
          textTransform: "uppercase",
          fontWeight: 600,
          letterSpacing: 0.5,
        }}
      >
        {label}
      </div>
      {children}
    </div>
  );
}

/** 参数 inline 预览 —— 折叠时显示前几个 key=value, 太长截断 */
function argsPreview(args: Record<string, unknown>): string {
  const entries = Object.entries(args).slice(0, 3);
  const parts = entries.map(([k, v]) => {
    let val: string;
    if (typeof v === "string") {
      val = `"${v.length > 30 ? v.slice(0, 30) + "…" : v}"`;
    } else if (typeof v === "object") {
      val = "{…}";
    } else {
      val = String(v);
    }
    return `${k}=${val}`;
  });
  if (Object.keys(args).length > 3) parts.push("…");
  return parts.join(", ");
}
