/** 会话 tab —— 左侧列表 + 右侧详情 + 启动新会话按钮 */

import { useState } from "react";
import SessionList from "./SessionList";
import SessionDetail from "./SessionDetail";
import SessionLauncher from "./SessionLauncher";

export default function SessionsTab() {
  const [selectedId, setSelectedId] = useState<string | null>(null);

  return (
    <div style={{ display: "flex", gap: "var(--space-4)", height: "100%" }}>
      <div style={{ width: 320, display: "flex", flexDirection: "column" }}>
        <SessionLauncher />
        <SessionList
          selectedId={selectedId}
          onSelect={setSelectedId}
        />
      </div>
      <div style={{ flex: 1 }}>
        <SessionDetail id={selectedId} />
      </div>
    </div>
  );
}
