import { useEffect } from "react";
import { getCurrentWindow } from "@tauri-apps/api/window";

import AuthBanner from "./components/AuthBanner";
import DevUserSwitcher from "./components/DevUserSwitcher";
import LoginGate from "./components/LoginGate";
import OnboardingWizard from "./components/OnboardingWizard";
import TabBar from "./components/TabBar";
import ChatTab from "./tabs/Chat/ChatTab";
import ConsoleTab from "./tabs/Console/ConsoleTab";
import DashboardTab from "./tabs/Dashboard/DashboardTab";
import { useUIStore } from "./store/ui";
import { useProactiveScheduler } from "./hooks/useProactiveScheduler";

export default function App() {
  const activeTab = useUIStore((s) => s.activeTab);

  // BL-E13 主动闲聊: 每分钟看一次, 9:30 / 14:00 / 17:30 自动 macOS 通知 + 起话题.
  useProactiveScheduler();

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
  //
  // OnboardingWizard 五一 sprint 5/2 BL-F3 加: 首次启动 4 步引导, 走完写 localStorage,
  // 不再显. 任何步骤"稍后再说" 也写 onboarded=true.
  return (
    <LoginGate>
      <AppShell activeTab={activeTab} />
      <OnboardingWizard />
    </LoginGate>
  );
}

function AppShell({ activeTab }: { activeTab: string }) {
  // "会话" tab 已并入 "对话" 左侧 sidebar (P0-3.1).
  // 对话 tab 自己管 padding/scroll, 不复用 .app-main padding
  // 品牌 (鲶鱼 Companion) 已在 macOS 原生标题栏显示, 应用内不再加 BrandHeader.
  // 版本号 v0.1.0 移到 Dashboard IdentityCard 的"版本"行.
  if (activeTab === "chat") {
    return (
      <div className="app-shell">
        <AuthBanner />
        <DevUserSwitcher />
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
      <DevUserSwitcher />
      <TabBar />
      <main className="app-main">
        {activeTab === "console" && <ConsoleTab />}
        {activeTab === "dashboard" && <DashboardTab />}
      </main>
    </div>
  );
}
