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

// E2 (6/6): STATUS_COLOR 删 — 颜色走 CSS class `.toolcall--<status>` 控制 4px
// left-bar accent (见 globals.css). preStyle / Section 也删 — 走 CSS class.

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

  // E2 (6/6 taste-skill 改造): className-based, 详 globals.css `.toolcall*`.
  // 5 大类 anti-pattern 修法见 globals.css 注释.
  const cardClass = [
    "toolcall",
    `toolcall--${call.status}`,
    open ? "toolcall--open" : "",
  ].filter(Boolean).join(" ");

  return (
    <div className={cardClass}>
      <div
        className="toolcall__header"
        onClick={() => setOpen(!open)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setOpen(!open);
          }
        }}
        role="button"
        tabIndex={0}
        aria-expanded={open}
        aria-label={`Tool call ${call.name}, ${open ? "已展开" : "已折叠"}`}
      >
        <span className="toolcall__status">{STATUS_EMOJI[call.status]}</span>
        <span className="toolcall__caret">▶</span>
        <span className="toolcall__name">{call.name}</span>
        <span className="toolcall__args-preview">({argsPreview(call.args)})</span>
        {call.status === "running" && (
          <span className="toolcall__running-label">执行中…</span>
        )}
      </div>
      {/* 文件 pill —— 折叠状态下也显示, 让员工不必展开就看见"下载入口" */}
      {filePaths.length > 0 && (
        <div className="toolcall__filepills">
          <FilePillList paths={filePaths} />
        </div>
      )}
      {open && (
        <div className="toolcall__body">
          <div className="toolcall__section">
            <div className="toolcall__section-label">参数</div>
            <pre className="toolcall__pre">{argsStr}</pre>
          </div>
          {(call.status === "done" || call.status === "error") && (
            <div className="toolcall__section">
              <div className="toolcall__section-label">
                {call.status === "error" ? "错误" : "结果"}
              </div>
              <pre
                className={
                  "toolcall__pre " +
                  (call.status === "error"
                    ? "toolcall__pre--error"
                    : "toolcall__pre--result")
                }
              >
                {call.status === "error"
                  ? call.error || resultDisplay
                  : resultDisplay || "(空)"}
              </pre>
            </div>
          )}
        </div>
      )}
      {/* P27.2 (6/5 鸿波): approval button 真**`折叠状态也要显**真.
       * 之前 button 真**`在 `{open && ...}` block 内**真 → 员工不展开 toolcall
       * 看不到 button → 卡死. 现在搬出去, ✓ done + approval pending 时
       * 总是显, 跟 file pill 一致 (filePaths 也是折叠也显). */}
      {isApprovalPending && (
        <div className="toolcall__approval-slot">
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

  // E5 (6/6): 删 inline style + hardcoded #16a34a/#0891b2/#dc2626 outline button.
  // 走 .toolcall__approval-btn className, brand 一致 (墨青 primary + muted always
  // + status-err deny). 删 emoji ✓/✗ prefix (banner 同).
  // .toolcall__approval-slot 已 display:flex, 这里 fragment 让 button 直接成为 slot 子节点.
  return (
    <>
      <span className="toolcall__approval-label">等待批准</span>
      <ApprovalBtn
        label="批准"
        variant="primary"
        onClick={() => void handleChoice("once")}
      />
      <ApprovalBtn
        label="始终批准"
        variant="always"
        onClick={() => void handleChoice("always")}
      />
      <ApprovalBtn
        label="拒绝"
        variant="deny"
        onClick={() => void handleChoice("deny")}
      />
    </>
  );
}

function ApprovalBtn({
  label,
  variant,
  onClick,
}: {
  label: string;
  variant: "primary" | "always" | "deny";
  onClick: () => void;
}) {
  return (
    <button
      className={`toolcall__approval-btn toolcall__approval-btn--${variant}`}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
    >
      {label}
    </button>
  );
}

// E2 (6/6): preStyle / Section 删 — 走 globals.css `.toolcall__pre`/`.toolcall__section*`.

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
