/** 控制台 tab —— 三个服务卡片 + 操作栏 + 日志面板 */

import ServiceCard from "./ServiceCard";
import ActionBar from "./ActionBar";
import LogPanel from "./LogPanel";

export default function ConsoleTab() {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-6)",
      }}
    >
      <ActionBar />
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(2, 1fr)",
          gap: "var(--space-4)",
        }}
      >
        <ServiceCard id="gateway" name="LLM Gateway" />
        <ServiceCard id="chrome" name="Catfish Chrome" />
        <ServiceCard id="local_search" name="Local Search" />
        <ServiceCard id="tool_bridge" name="Tool Bridge" />
      </div>
      <LogPanel />
    </div>
  );
}
