/** 单个 tool 调用展示 —— 折叠式,默认收起,点开看 args / result */

import { useState } from "react";
import type { ToolCall } from "../../types/chat";
import { extractFilePaths } from "../../lib/path_detect";
import { FilePillList } from "../../components/FilePill";
import { toolBridgeChatApproval } from "../../lib/tauri";

// P44 (6/5 鸿波 marathon): chat completions approval — session_key 从 SSE event
// `hermes.tool.progress` (status=approval_pending) 拿. plugin P15 注的
// _approval_notify push 这个 event. 全局 mutable 一个 latest session_key (chat
// 一次只可能有 1 个 pending block, 不会并发), ApprovalButton onClick 用.
let _latestApprovalSessionKey: string | null = null;
if (typeof window !== "undefined") {
  window.addEventListener("catfish:approval-pending", (e: Event) => {
    const ev = e as CustomEvent<{ approval_session_key?: string }>;
    _latestApprovalSessionKey = ev.detail?.approval_session_key ?? null;
  });
}

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
  // tool message 含 status:"pending_approval" + approval_pending:true + message
  // text "Asking the user for approval. Code: ...". P27.1 (6/5 鸿波实测 manual mode
  // tool ✓ done 但 button 不显) — 放宽 regex 涵盖 hermes 真**`几种 JSON shape**`** 真:
  //   - "status": "pending_approval"          ← JSON field, 最可靠
  //   - "approval_pending": true              ← JSON bool field
  //   - "Asking the user for approval"        ← message text
  //   - LLM 翻译"授权批准" / "请批准"            ← 中文兜底
  const isApprovalPending =
    (call.status === "done" || call.status === "error") &&
    typeof resultStr === "string" &&
    /pending_approval|approval_pending|Asking the user for approval|授权批准|请.{0,4}批准/i.test(
      resultStr,
    );

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
        </div>
      )}
      {/* P27.2 (6/5 鸿波): approval button 真**`折叠状态也要显**真.
       * 之前 button 真**`在 `{open && ...}` block 内**真 → 员工不展开 toolcall
       * 看不到 button → 卡死. 现在搬出去, ✓ done + approval pending 时
       * 总是显, 跟 file pill 一致 (filePaths 也是折叠也显). */}
      {isApprovalPending && (
        <div
          style={{
            padding: "0 var(--space-3) var(--space-2)",
            borderTop: "1px solid var(--catfish-border)",
          }}
        >
          <ApprovalButtons />
        </div>
      )}
    </div>
  );
}

/** P27 + P44 (6/5 鸿波 marathon): hermes approval pending inline 按钮.
 *  - P44 path (新): 优先调 toolBridgeChatApproval(session_key, choice) →
 *    plugin P15.2 注的 tool-bridge RPC tools/chat_approval →
 *    resolve_gateway_approval. session_key 来自 SSE event approval-pending.
 *  - P27 fallback (老): 没拿到 session_key 时 dispatch "/approve" user
 *    message (兼容老 flow / 历史 session 加载渲染的 button).
 */
function ApprovalButtons() {
  const handleChoice = async (choice: "once" | "session" | "always" | "deny") => {
    const sid = _latestApprovalSessionKey;
    if (sid) {
      try {
        await toolBridgeChatApproval(sid, choice);
        // 解 block 成功 — 清 session_key 防重复点击
        _latestApprovalSessionKey = null;
        return;
      } catch (e) {
        // P44 RPC 失败 → fallback 老 path
        // eslint-disable-next-line no-console
        console.warn("[P44] chat_approval RPC 失败, fallback /approve:", e);
      }
    }
    // P27 fallback: 没 session_key (历史 toolcall) 或 RPC 失败 → 老 path
    const text =
      choice === "always"
        ? "/approve always"
        : choice === "session"
          ? "/approve session"
          : choice === "deny"
            ? "/deny"
            : "/approve";
    window.dispatchEvent(
      new CustomEvent("catfish:approval-send", { detail: { text } }),
    );
  };

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
      <ApprovalBtn label="✓ 批准" color="var(--status-ok, #16a34a)" onClick={() => void handleChoice("once")} />
      <ApprovalBtn label="✓ 始终批准" color="var(--catfish-cyan-dim, #0891b2)" onClick={() => void handleChoice("always")} />
      <ApprovalBtn label="✗ 拒绝" color="var(--status-err, #dc2626)" onClick={() => void handleChoice("deny")} />
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
