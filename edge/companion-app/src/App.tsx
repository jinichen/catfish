import { lazy, Suspense, useEffect } from "react";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { listen } from "@tauri-apps/api/event";

import AboutModal from "./components/AboutModal";
import AdvisoryBanner from "./components/AdvisoryBanner";  // 6/7 BL-MANIFESTO-ADVISORY-PHASE1
import AuthBanner from "./components/AuthBanner";
import HermesReconnectBanner from "./components/HermesReconnectBanner";  // P3.3.5 (6/9): hermes 重连 banner
import HermesBootstrapStatus from "./components/HermesBootstrapStatus";
import DevUserSwitcher from "./components/DevUserSwitcher";
import FocusModeView from "./components/FocusModeView";
import LoginGate from "./components/LoginGate";
import OnboardingWizard from "./components/OnboardingWizard";
import TabBar from "./components/TabBar";
import ChatTab from "./tabs/Chat/ChatTab";
// BL-CONSOLE-TAB-KILL (5/16): 控制台 tab 砍, ConsoleTab.tsx 源码留着作 git 历史.
// import ConsoleTab from "./tabs/Console/ConsoleTab";
import BriefingTab from "./tabs/Briefing/BriefingTab";  // 5/20 BL-COMPANION-DAILY-BRIEFING-MVP
import { useUIStore } from "./store/ui";
import { useAgentStore } from "./store/agent";
import { useChatStore } from "./store/chat";  // P3.5.139 Phase 4: 启动同步 picker_model → store
import { useEmailStore } from "./store/email";
import { useFocusStore } from "./store/focus";
import { useProactiveScheduler } from "./hooks/useProactiveScheduler";
import { useProactiveTriggers } from "./hooks/useProactiveTriggers";
import { usePetStatusBroadcast } from "./hooks/usePetStatusBroadcast";
import { getPickerModel } from "./lib/tauri";  // P3.5.139 Phase 4
import { fetchUrgentEmailStarter } from "./lib/briefing";

// 非默认工作区按需加载，避免所有邮件/知识图谱/仪表盘代码挤进主包。
// React.lazy 会缓存已加载模块，TAB 往返不会重复下载或重新初始化模块。
const DashboardTab = lazy(() => import("./tabs/Dashboard/DashboardTab"));
const EmailTab = lazy(() => import("./tabs/Email/EmailTab"));
const WikiTab = lazy(() => import("./tabs/Wiki/WikiTab"));
const CollabTab = lazy(() => import("./tabs/Collab/CollabTab"));  // P50 (9/10): 横向协同

export default function App() {
  const activeTab = useUIStore((s) => s.activeTab);
  const loadAgentPrefs = useAgentStore((s) => s.loadAgentPrefs);
  const focusActive = useFocusStore((s) => s.active);
  const toggleFocus = useFocusStore((s) => s.toggle);
  const openAbout = useUIStore((s) => s.openAbout);

  // BL-E27 一次到位: 桌宠状态联动 LLM (idle/thinking/running/done)
  usePetStatusBroadcast();

  // BL-E11 命名权: 启动拉一次 agent prefs (员工自定义鲶鱼名 + 人设),
  // ChatPanel / Onboarding / 通知等多处 UI 共用. Onboarding 改了立即更新 store.
  useEffect(() => {
    void loadAgentPrefs();
  }, [loadAgentPrefs]);

  // P3.5.139 Phase 4 (6/29 鸿波"重启 Companion picker 应该记得这次选择"):
  // 启动读 ~/.catfish/picker_model (P3.5.28 写的单文件) 注入 zustand store.model.
  // file > store 优先级 — 重启 Companion 上次选过的 model 立刻生效, 不被
  // catalog.default useEffect (ChatTab) 覆盖 (pickedByUser=true 锁 picker).
  //
  // 失败 (file 不在 / 空 / 权限) → 不动 store, model="" 走 ChatTab catalog.default
  // useEffect 兜底注入 catalog.default. 客户第一次启动没 picker_model 文件就是这个路径.
  useEffect(() => {
    void (async () => {
      try {
        const m = await getPickerModel();
        if (m && m.trim()) {
          // pickedByUser=true 锁 picker, 不被 catalog.default 覆盖
          useChatStore.getState().setModel(m.trim(), true);
        }
      } catch (e) {
        console.warn("[P3.5.139 Phase 4] getPickerModel 失败 (静默, 走 catalog 兜底):", e);
      }
    })();
  }, []);

  // BL-E13 主动闲聊 (死时间兜底): 9:30 / 14:00 / 17:30
  useProactiveScheduler();

  // 5/6 BL-E13.5 真主动 Phase A: chat 沉默 / journal deadline / focus 切换 信号触发
  useProactiveTriggers();

  // BL-E15 专注模式: 监听 Tauri 后端 emit 的 toggle 事件 (Cmd+Shift+F 触发).
  // 切到 store, App 顶层根据 active 决定渲染 FocusModeView 还是正常 AppShell.
  useEffect(() => {
    let unlisten: (() => void) | null = null;
    void (async () => {
      unlisten = await listen("catfish:focus_mode_toggle", () => {
        toggleFocus();
      });
    })();
    return () => {
      if (unlisten) unlisten();
    };
  }, [toggleFocus]);

  // 5/18 BL-COMPANION-ABOUT-HIJACK: macOS app menu "鲶鱼 Companion → 关于鲶鱼"
  // 走自定义 React 模态, 不走原生 NSPanel. Rust 端 emit "show-about", 这里接.
  useEffect(() => {
    let unlisten: (() => void) | null = null;
    void (async () => {
      unlisten = await listen("show-about", () => {
        openAbout();
      });
    })();
    return () => {
      if (unlisten) unlisten();
    };
  }, [openAbout]);

  // 5/18 BL-COMPANION-EMAIL-DIGEST-STEP4: 邮件 scheduler 检测到"急"邮件 →
  // Rust 端 emit catfish:email-urgent. 这里接 → 走桌宠主动闲聊路径 (BL-E13 同套).
  //
  // 5/20 BL-EMAIL-URGENT-LLM-PUSH: 改用 LLM 写 starter (跟早安播报同套路, 更自然),
  // LLM 挂了 fallback 老 Rust 拼的 hard-coded starter.
  const startProactiveChat = useUIStore((s) => s.startProactiveChat);
  useEffect(() => {
    let unlisten: (() => void) | null = null;
    void (async () => {
      unlisten = await listen<{
        count: number;
        starter: string;
        ids: string[];
        items?: Array<{ subject: string; sender: string }>;
      }>("catfish:email-urgent", async (event) => {
        const { starter: fallbackStarter, count, items, ids } = event.payload;
        if (!fallbackStarter || count === 0) return;

        // BL-COMPANION-EMAIL-DIGEST-STEP5 sub-task 2 (5/20): 员工已点开过的急
        // 邮件不再叫醒. Rust scheduler 24h push_history dedup 只防"刚 push 过",
        // 不知道员工是否真读了; 前端 useEmailStore.readIds (localStorage 持久化)
        // 记录员工真读过的 id, 这里 filter.
        if (ids && ids.length > 0) {
          const readSet = useEmailStore.getState().readIds;
          const unreadIds = ids.filter((id) => !readSet.has(id));
          if (unreadIds.length === 0) {
            console.log(
              `[email-urgent] ${ids.length} 封急邮件员工都已读过, 跳过桌宠通知`,
            );
            return;
          }
          if (unreadIds.length < ids.length) {
            console.log(
              `[email-urgent] ${ids.length - unreadIds.length}/${ids.length} 封已读, 仍 ${unreadIds.length} 封未读, 走通知`,
            );
          }
        }

        // BL-EMAIL-URGENT-LLM-PUSH (5/20): 调 LLM 写更自然版本
        let finalStarter = fallbackStarter;
        if (items && items.length > 0) {
          try {
            const model = useChatStore.getState().model;
            const personality = useAgentStore.getState().personality;
            const llmStarter = await fetchUrgentEmailStarter(
              items.map((it) => ({ subject: it.subject, sender: it.sender })),
              model,
              personality,
            );
            if (llmStarter) {
              finalStarter = llmStarter;
              console.log(`[email-urgent] LLM starter: ${llmStarter.slice(0, 60)}`);
            } else {
              console.log("[email-urgent] LLM 挂, fallback scheduler starter");
            }
          } catch (e) {
            console.warn("[email-urgent] LLM 异常, fallback scheduler starter", e);
          }
        }

        startProactiveChat(finalStarter);
      });
    })();
    return () => {
      if (unlisten) unlisten();
    };
  }, [startProactiveChat]);

  // 五一 sprint 5/5: Esc 隐藏浮窗 (配合 Cmd+Shift+Space 召唤)
  // 输入框聚焦时 Esc 由组件自己处理 (e.g. 关闭弹层); 这里只在 body 聚焦时拦截.
  // BL-E15: 专注模式激活时这个 Esc-hide 不能跑, FocusModeView 自己 capture Esc 退专注.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      // 专注模式打开时, FocusModeView 用 capture 阶段抢先处理 Esc, 这里不该再 hide
      if (useFocusStore.getState().active) return;
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

  // BL-E15: 专注模式激活 → 整个 App 替换成 FocusModeView (不显聊天/Dashboard 杂讯).
  // 注意放在 LoginGate 之前: 即使没登录, 按 Cmd+Shift+F 也能进专注 (员工常用场景:
  // 临时打开 Companion 没登, 按快捷键挡屏).
  if (focusActive) {
    // 5/18 BL-COMPANION-ABOUT-HIJACK: AboutModal 在专注模式也要能弹 (员工
    // 从 macOS menu 触发, 不应被专注 view 吞掉).
    return (
      <>
        <FocusModeView />
        <AboutModal />
        <HermesBootstrapStatus />
      </>
    );
  }

  // SSO Phase 1C: LoginGate 包整个 App. 没登录时挡住, 让员工先点登录.
  // dev_token 模式下 try_load_session 自动返已登录, gate 直接放过.
  //
  // OnboardingWizard 五一 sprint 5/2 BL-F3 加: 首次启动 4 步引导, 走完写 localStorage,
  // 不再显. 任何步骤"稍后再说" 也写 onboarded=true.
  return (
    <>
      <LoginGate>
        <AppShell activeTab={activeTab} />
        <OnboardingWizard />
      </LoginGate>
      {/* 必须在 LoginGate 外：检测服务器、登录加载或未登录时，
          LoginGate 都不渲染 children，否则 macOS“关于鲶鱼”会点击无反应。 */}
      <AboutModal />
      {/* 首启安装与登录互不阻塞；服务器不可达时也能看见安装状态。 */}
      <HermesBootstrapStatus />
    </>
  );
}

function AppShell({ activeTab }: { activeTab: string }) {
  // "会话" tab 已并入 "对话" 左侧 sidebar (P0-3.1).
  // 对话 tab 自己管 padding/scroll, 不复用 .app-main padding
  // 品牌 (鲶鱼 Companion) 已在 macOS 原生标题栏显示, 应用内不再加 BrandHeader.
  // 版本号 v0.1.0 移到 Dashboard IdentityCard 的"版本"行.
  // P3.3.29 (6/11): briefing 也走 main overflow:hidden 路径 — BriefingTab 内
  //   .briefing-2col 已用 calc(100vh - 200px) 自管 sidebar/detail 独立 scroll,
  //   app-main 的 overflow-y:auto 跟它撞车导致外层多一根滚动条 (内容区左右各
  //   自滚 ✓, 外层也滚 ✗).
  //
  // 8/31: AppShell 以前按 activeTab 分成两套 return. 早安从 chat/其他 TAB 切换
  //   时会被 React 卸载，切回来重新 mount AdvisorView，重新采集四个数据源并进入
  //   loading。早安是需要保留结果和后台时段计时器的常驻面板，不能跟着 TAB 销毁。
  //   现在 AppShell 只有一套稳定树，BriefingTab 始终挂载；非早安时只 display:none。
  //   display:contents 保留早安原有布局，不额外引入一层可见容器。
  const isBriefing = activeTab === "briefing";
  const isChatOrBriefing = activeTab === "chat" || isBriefing;
  return (
    <div className="app-shell">
      <TabBar />
      <div className="app-workspace">
        <AuthBanner />
        {isChatOrBriefing && <AdvisoryBanner />}
        <HermesReconnectBanner />
        <DevUserSwitcher />
        <main className={isChatOrBriefing ? "app-workspace__main app-workspace__main--locked" : "app-main"}>
          {/* BL-CONSOLE-TAB-KILL (5/16): {activeTab === "console" && <ConsoleTab />} */}
          <div
            style={{ display: isBriefing ? "contents" : "none" }}
            aria-hidden={!isBriefing}
          >
            <BriefingTab />
          </div>
          {activeTab === "chat" && <ChatTab />}
          <Suspense fallback={<div className="app-tab-loading">正在打开…</div>}>
            {activeTab === "dashboard" && <DashboardTab />}
            {activeTab === "email" && <EmailTab />}
            {activeTab === "wiki" && <WikiTab />}
            {activeTab === "collab" && <CollabTab />}
          </Suspense>
        </main>
      </div>
    </div>
  );
}
