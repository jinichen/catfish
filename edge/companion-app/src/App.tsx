import { useEffect } from "react";
import { getCurrentWindow } from "@tauri-apps/api/window";

import AuthBanner from "./components/AuthBanner";
import BrandHeader from "./components/BrandHeader";
import LoginGate from "./components/LoginGate";
import TabBar from "./components/TabBar";
import ChatTab from "./tabs/Chat/ChatTab";
import ConsoleTab from "./tabs/Console/ConsoleTab";
import DashboardTab from "./tabs/Dashboard/DashboardTab";
import { useUIStore } from "./store/ui";

export default function App() {
  const activeTab = useUIStore((s) => s.activeTab);

  // 五一 sprint 5/5: Esc 隐藏浮窗 (配合 Cmd+Shift+Space 召唤)
  // 输入框聚焦时 Esc 由组件自己处理 (e.g. 关闭弹层); 这里只在 body 聚焦时拦截.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName?.toLowerCase() ?? "";
      // 输入态 (textarea/input/contenteditable) 不抢, 让组件用 (清空输入 / 关弹窗).
      if (tag === "textarea" || tag === "input" || target?.isContentEditable) return;
      e.preventDefault();
      void getCurrentWindow().hide();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  // SSO Phase 1C: LoginGate 包整个 App. 没登录时挡住, 让员工先点登录.
  // dev_token 模式下 try_load_session 自动返已登录, gate 直接放过.
  return (
    <LoginGate>
      <AppShell activeTab={activeTab} />
    </LoginGate>
  );
}

function AppShell({ activeTab }: { activeTab: string }) {
  // "会话" tab 已并入 "对话" 左侧 sidebar (P0-3.1).
  // 对话 tab 自己管 padding/scroll, 不复用 .app-main padding
  if (activeTab === "chat") {
    return (
      <div className="app-shell">
        <AuthBanner />
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
      <AuthBanner />
      <BrandHeader />
      <TabBar />
      <main className="app-main">
        {activeTab === "console" && <ConsoleTab />}
        {activeTab === "dashboard" && <DashboardTab />}
      </main>
    </div>
  );
}
