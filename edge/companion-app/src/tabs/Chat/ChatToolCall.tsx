/** 单个 tool 调用展示 —— 折叠式,默认收起,点开看 args / result */

import { useState } from "react";
import type { ToolCall } from "../../types/chat";
import { extractFilePaths } from "../../lib/path_detect";
import { FilePillList } from "../../components/FilePill";

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

  // 自动扫描 result 里的文件路径 — skill 生成的 .docx / .xlsx / .pptx
  // 等都该在这里冒出来. 即使工具调用还在 running, 如果上次的 result 留着也能识别
  // (但 status !== done 时不渲染按钮 — 文件可能还没真正写完).
  const filePaths =
    call.status === "done" ? extractFilePaths(resultStr) : [];

  // P27 (6/5 鸿波): hermes approval pending 检测.
  // hermes check_execute_code_guard / check_dangerous_command 真**`pending`** 时返
  // tool message 含 "Asking the user for approval" + code/command. LLM 真 chat 里
  // 翻译成"请批准", 但 user 没 inline button 没法点. 这里直接渲染按钮, 点击发
  // /approve | /approve always | /deny 走 hermes 原 slash command handler.
  const isApprovalPending =
    call.status === "done" &&
    typeof resultStr === "string" &&
    /Asking the user for approval|approval_pending/i.test(resultStr);

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
      {/* 文件 pill —— 折叠状态下也显示, 让员工不必展开就看见"下载入口" */}
      {filePaths.length > 0 && (
        <div
          style={{
            padding: "0 var(--space-3) 6px",
            borderTop: "1px solid var(--catfish-border)",
          }}
        >
          <FilePillList paths={filePaths} />
        </div>
      )}
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
          {isApprovalPending && <ApprovalButtons />}
        </div>
      )}
      {/* 折叠状态下也显 approval button — 不用展开就能点 */}
      {!open && isApprovalPending && (
        <div
          style={{
            padding: "8px var(--space-3) 10px",
            borderTop: "1px solid var(--catfish-border)",
            background: "var(--catfish-bg-elevated, var(--catfish-bg))",
          }}
        >
          <ApprovalButtons />
        </div>
      )}
    </div>
  );
}

/** P27 (6/5): hermes approval pending → inline 按钮.
 *  点击 dispatch CustomEvent, ChatPanel useEffect 监听调 onSend.
 *  Hermes 收到 /approve / /deny 走 _handle_approve_command path resolve block.
 */
function ApprovalButtons() {
  const dispatch = (text: string) =>
    window.dispatchEvent(
      new CustomEvent("catfish:approval-send", { detail: { text } }),
    );
  return (
    <div
      style={{
        display: "flex",
        gap: "var(--space-2)",
        flexWrap: "wrap",
        alignItems: "center",
        fontSize: 12,
      }}
    >
      <span style={{ color: "var(--catfish-text-muted)", marginRight: 4 }}>
        等待批准:
      </span>
      <ApprovalBtn label="✓ 批准" color="var(--status-ok, #16a34a)" onClick={() => dispatch("/approve")} />
      <ApprovalBtn label="✓ 始终批准" color="var(--catfish-cyan-dim, #0891b2)" onClick={() => dispatch("/approve always")} />
      <ApprovalBtn label="✗ 拒绝" color="var(--status-err, #dc2626)" onClick={() => dispatch("/deny")} />
    </div>
  );
}

function ApprovalBtn({
  label,
  color,
  onClick,
}: {
  label: string;
  color: string;
  onClick: () => void;
}) {
  return (
    <button
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      style={{
        background: "transparent",
        border: `1px solid ${color}`,
        color,
        borderRadius: 4,
        padding: "3px 10px",
        fontSize: 12,
        cursor: "pointer",
        fontWeight: 500,
      }}
    >
      {label}
    </button>
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
