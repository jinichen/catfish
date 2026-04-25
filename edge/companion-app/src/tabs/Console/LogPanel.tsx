/** 实时日志窗 —— 选择服务 → 显示该服务的最近 N 行 */

import { useState } from "react";
import { useLogTail } from "../../hooks/useLogTail";

const SERVICES = [
  { id: "gateway", label: "Gateway" },
  { id: "chrome", label: "Chrome" },
  { id: "local_search", label: "Local Search" },
  { id: "tool_bridge", label: "Tool Bridge" },
];

export default function LogPanel() {
  const [service, setService] = useState("gateway");
  const lines = useLogTail(service);

  return (
    <div
      style={{
        background: "var(--catfish-bg-elevated)",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-md)",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          display: "flex",
          gap: "var(--space-2)",
          padding: "var(--space-3) var(--space-4)",
          borderBottom: "1px solid var(--catfish-border)",
        }}
      >
        {SERVICES.map((s) => (
          <button
            key={s.id}
            onClick={() => setService(s.id)}
            style={{
              fontSize: 12,
              padding: "2px 8px",
              background: service === s.id ? "var(--catfish-cyan)" : "transparent",
              color: service === s.id ? "white" : "var(--catfish-text-muted)",
              border: "1px solid var(--catfish-border)",
              borderRadius: "var(--radius-sm)",
            }}
          >
            {s.label}
          </button>
        ))}
      </div>
      <pre
        style={{
          margin: 0,
          padding: "var(--space-3)",
          fontFamily: "var(--font-mono)",
          fontSize: 12,
          lineHeight: 1.45,
          color: "var(--catfish-text)",
          height: 240,
          overflow: "auto",
          whiteSpace: "pre-wrap",
        }}
      >
        {lines.length === 0
          ? "(等待日志…)"
          : lines.map((l) => `${l.ts}  ${l.line}`).join("\n")}
      </pre>
    </div>
  );
}
