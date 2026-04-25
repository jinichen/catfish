/** 在系统终端里拉起新的 hermes 会话 */

import { openTerminal } from "../../lib/tauri";

export default function SessionLauncher() {
  const launch = async () => {
    try {
      await openTerminal();
    } catch {
      // TODO: 错误反馈
    }
  };

  return (
    <button
      onClick={() => void launch()}
      style={{
        padding: "var(--space-3)",
        background: "var(--catfish-cyan)",
        color: "white",
        border: "none",
        borderRadius: "var(--radius-sm)",
        fontWeight: 600,
      }}
    >
      + 新会话（在终端里打开）
    </button>
  );
}
