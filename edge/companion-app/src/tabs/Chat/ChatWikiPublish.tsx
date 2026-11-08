/**
 * 对话里「发布到部门」的确认 (11/08): catfish_wiki_publish 扫到 PII / 内网地址 / 敏感词时不发, 结果里带 confirm_in_ui;
 * 以前是小鲶转述警告、员工在对话里说「可以」、小鲶再带 acknowledge_warnings=true 重试 —— 代码上没人拦, 小鲶自己说
 * 员工同意了就能发出去。现在只有这里 (Companion 走 tools/dispatch, tool-bridge 给带 _from_ui) 点的确认才算。
 */
import { useState } from "react";

import { toolBridgeCallTool } from "../../lib/tauri_services";

function parse(text: string): Record<string, unknown> | null {
  try {
    const v = JSON.parse(text);
    return v && typeof v === "object" ? (v as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

interface Confirm { wiki_rel_path: string; warnings?: unknown[] }

function warningText(w: unknown): string {
  if (typeof w === "string") return w;
  if (w && typeof w === "object") {
    const o = w as Record<string, unknown>;
    return String(o.advice ?? o.message ?? o.kind ?? o.type ?? JSON.stringify(o));
  }
  return String(w);
}

export function PublishConfirmButton({ resultText }: { resultText: string }) {
  const r = parse(resultText);
  const c = r?.confirm_in_ui as Confirm | undefined;
  const [state, setState] = useState<"idle" | "busy" | "done" | "error">("idle");
  const [msg, setMsg] = useState<string>("");
  if (!c?.wiki_rel_path) return null;
  const warnings = Array.isArray(c.warnings) ? c.warnings : [];

  const confirm = async () => {
    setState("busy");
    try {
      const res = await toolBridgeCallTool("catfish_wiki_publish", { wiki_rel_path: c.wiki_rel_path, acknowledge_warnings: true });
      const result = res.result as Record<string, unknown> | null;
      if (res.ok && result?.ok) {
        setMsg(`已发布到 ${String(result.namespace ?? "部门")}`);
        setState("done");
      } else {
        setMsg(String(result?.error ?? res.error ?? "没发出去"));
        setState("error");
      }
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
      setState("error");
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 12, padding: "6px 0" }}>
      <span style={{ color: "var(--status-warn, #d97706)" }}>
        发布前扫到 {warnings.length} 类要留意的内容 (不会自动脱敏): {warnings.map(warningText).join("; ")}
      </span>
      {state === "done" ? (
        <span style={{ color: "var(--status-ok, #16a34a)" }}>✓ {msg}</span>
      ) : (
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <button type="button" disabled={state === "busy"} onClick={() => void confirm()}
                  style={{ padding: "4px 12px", fontSize: 12, borderRadius: "var(--radius-sm)", border: "none",
                           background: "var(--catfish-cyan)", color: "white", cursor: "pointer" }}>
            {state === "busy" ? "发布中…" : "我看过了, 确认发布到部门"}
          </button>
          <span style={{ color: "var(--catfish-text-muted)" }}>同事装到本机的副本撤不回; 不发就不用管这条</span>
          {state === "error" && <span style={{ color: "var(--status-error, #dc2626)" }}>{msg}</span>}
        </div>
      )}
    </div>
  );
}
