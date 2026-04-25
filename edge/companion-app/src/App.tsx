import BrandHeader from "./components/BrandHeader";
import TabBar from "./components/TabBar";
import ChatTab from "./tabs/Chat/ChatTab";
import ConsoleTab from "./tabs/Console/ConsoleTab";
import SessionsTab from "./tabs/Sessions/SessionsTab";
import DashboardTab from "./tabs/Dashboard/DashboardTab";
import { useUIStore } from "./store/ui";

export default function App() {
  const activeTab = useUIStore((s) => s.activeTab);

  // 对话 tab 是聊天主功能 —— 自己管全 padding/scroll，不复用 .app-main 的 padding
  if (activeTab === "chat") {
    return (
      <div className="app-shell">
        <BrandHeader />
        <TabBar />
        <main style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
          <ChatTab />
        </main>
      </div>
    );
  }

  return (
    <div className="app-shell">
      <BrandHeader />
      <TabBar />
      <main className="app-main">
        {activeTab === "console" && <ConsoleTab />}
        {activeTab === "sessions" && <SessionsTab />}
        {activeTab === "dashboard" && <DashboardTab />}
      </main>
    </div>
  );
}
