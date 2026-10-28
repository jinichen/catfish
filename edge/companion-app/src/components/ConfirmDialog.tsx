/** 通用确认对话框 —— 用于"停止所有服务""kill Chrome"等需要二次确认的动作。
 *  10/28: 也给 useConfirm 当 confirm / alert / prompt 用 (Tauri WebView 不弹原生的那三个)。 */

import type { ReactNode } from "react";

interface Props {
  open: boolean;
  title: string;
  body: string;
  onConfirm: () => void;
  onCancel: () => void;
  /** false = 只有一个「知道了」(当 alert 用) */
  showCancel?: boolean;
  /** 正文下面的额外内容 (当 prompt 用时放输入框) */
  children?: ReactNode;
}

export default function ConfirmDialog({
  open,
  title,
  body,
  onConfirm,
  onCancel,
  showCancel = true,
  children,
}: Props) {
  if (!open) return null;
  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.4)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 2000, // 要盖过 ModalShell (1000): 弹窗里也会用到 (useConfirm)
      }}
    >
      <div
        style={{
          background: "var(--catfish-bg-elevated)",
          padding: "var(--space-6)",
          borderRadius: "var(--radius-md)",
          minWidth: 320,
          maxWidth: "min(560px, 90vw)",
          overflowWrap: "anywhere",
          boxShadow: "var(--shadow-md)",
        }}
      >
        <h3 style={{ marginBottom: "var(--space-2)" }}>{title}</h3>
        <p
          style={{
            color: "var(--catfish-text-muted)",
            marginBottom: "var(--space-6)",
            whiteSpace: "pre-wrap",
          }}
        >
          {body}
        </p>
        {children}
        <div
          style={{
            display: "flex",
            justifyContent: "flex-end",
            gap: "var(--space-2)",
          }}
        >
          {showCancel && <button onClick={onCancel}>取消</button>}
          <button onClick={onConfirm}>{showCancel ? "确认" : "知道了"}</button>
        </div>
      </div>
    </div>
  );
}
