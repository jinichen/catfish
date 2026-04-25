/** 通用确认对话框 —— 用于"停止所有服务""kill Chrome"等需要二次确认的动作。 */

interface Props {
  open: boolean;
  title: string;
  body: string;
  onConfirm: () => void;
  onCancel: () => void;
}

export default function ConfirmDialog({
  open,
  title,
  body,
  onConfirm,
  onCancel,
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
        zIndex: 100,
      }}
    >
      <div
        style={{
          background: "var(--catfish-bg-elevated)",
          padding: "var(--space-6)",
          borderRadius: "var(--radius-md)",
          minWidth: 320,
          boxShadow: "var(--shadow-md)",
        }}
      >
        <h3 style={{ marginBottom: "var(--space-2)" }}>{title}</h3>
        <p
          style={{
            color: "var(--catfish-text-muted)",
            marginBottom: "var(--space-6)",
          }}
        >
          {body}
        </p>
        <div
          style={{
            display: "flex",
            justifyContent: "flex-end",
            gap: "var(--space-2)",
          }}
        >
          <button onClick={onCancel}>取消</button>
          <button onClick={onConfirm}>确认</button>
        </div>
      </div>
    </div>
  );
}
