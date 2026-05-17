/** catfish-web 主入口 (BL-ARCH1 5/10, BL-ARCH2 fix2 5/10 修白屏).
 *
 * 启动流程:
 *   1. 路径是 /auth/callback → AuthCallback 组件:
 *      handleCallback() (signin) → fetchMe + setMe → navigate(returnTo)
 *      (BL-ARCH2 fix2: 必须在 navigate 前 setMe, 不然 App 渲染时 me=null
 *       的 useEffect 早跑过了, 路由切换不会触发 re-fetch → 白屏)
 *   2. 其他路径: 启动 useEffect → getCurrentUser, 没登 → login() 跳 IdP;
 *      已登 → fetchMe + setMe.
 *   3. me!=null → 渲染 NavBar + 子 route
 *
 * 鸿波 5/10 反馈 (BL-ARCH2 fix2):
 *   - "每次进入页面都要再刷新才能看到内容, 不然就是白屏"
 *     → 真因: AuthCallback navigate 后 App 重渲染, 但 useEffect 依赖 [setMe,
 *       setError] 引用稳定, 不会 re-run; me 还是 null → 进 "正在跳转登录页…"
 *       分支看着像白屏. 修法: AuthCallback 在 navigate 前主动 setMe, App
 *       渲染时 me 已经在 store 里, 直接进主路由.
 *   - "为什么还要再登录一次"
 *     → 真因 1: sessionStorage → 关 tab 丢. 修: lib/auth.ts 改 localStorage.
 *     → 真因 2: Companion 跟 web 不共享 token (Tauri vs 浏览器隔离).
 *       Phase 1 接受第一次需登, 后续 IdP cookie SSO. (Phase 2 加 token transfer)
 */

import { useEffect } from "react";
import { Navigate, Route, Routes, useNavigate } from "react-router-dom";

import { NavBar } from "./components/NavBar";
import { fetchMe } from "./lib/me";
import { getCurrentUser, handleCallback, login } from "./lib/auth";
import { useAuthStore } from "./store/auth";

import { HomePage } from "./routes/HomePage";
import { MePage } from "./routes/MePage";
import { SkillsHubPage } from "./routes/SkillsHubPage";
import { McpMarketPage } from "./routes/McpMarketPage";
import { ManagerPage } from "./routes/ManagerPage";
import { AdminPage } from "./routes/AdminPage";
import { AuditPage } from "./routes/AuditPage";
// BL-CENTRAL-WEB-PURGE-USERDATA (5/17 鸿波): 删 SessionsPage / KanbanPage —
// 它们读员工本机 ~/.hermes/state.db + ~/.catfish/tasks.jsonl, 违反
// BL-CENTRAL-EDGE-BOUNDARY 规则. 这俩功能 Companion 桌面 app 自己有.

function AuthCallback() {
  const navigate = useNavigate();
  const setMe = useAuthStore((s) => s.setMe);
  const setError = useAuthStore((s) => s.setError);
  useEffect(() => {
    (async () => {
      try {
        // 1. signin: code → token, 写 localStorage (BL-ARCH2 fix2)
        const returnTo = await handleCallback();
        // 2. BL-ARCH2 fix2 (5/10): 主动 fetchMe + setMe, **navigate 之前**.
        //    不然 App 重渲染时 me 还是 null → 走 "正在跳转登录页…" 白屏分支.
        try {
          const meInfo = await fetchMe();
          setMe(meInfo);
        } catch (e) {
          console.warn("[auth] callback 后 fetchMe 失败, 主 useEffect 兜底:", e);
          setError(e instanceof Error ? e.message : String(e));
        }
        // 3. 跳目标页. 此时 me 已在 store, App 直接渲主路由.
        navigate(returnTo, { replace: true });
      } catch (e) {
        console.error("[auth] callback 失败:", e);
        navigate("/", { replace: true });
      }
    })();
  }, [navigate, setMe, setError]);
  return (
    <div
      style={{
        height: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        color: "var(--text-muted)",
      }}
    >
      正在完成登录…
    </div>
  );
}

export function App() {
  // BL-ARCH2 fix2 (5/10): 选择器订阅, me 变就重渲, 不再白屏卡死
  const me = useAuthStore((s) => s.me);
  const loading = useAuthStore((s) => s.loading);
  const setMe = useAuthStore((s) => s.setMe);
  const setError = useAuthStore((s) => s.setError);

  // 启动: 检查 session, 拉 me. 没登录就跳 IdP.
  // BL-ARCH2 fix2: 依赖加 me — me 已设过就不重跑 (常见场景), me 仍空时
  // (callback 失败 / localStorage 过期) 主 effect 兜底.
  useEffect(() => {
    if (window.location.pathname === "/auth/callback") {
      // callback 路由由专门组件处理
      return;
    }
    if (me) {
      // 已经有 me (callback 设过 / 上次 localStorage 没过期) — 跳过
      return;
    }
    (async () => {
      const user = await getCurrentUser();
      if (!user) {
        // 没登录 → 跳 IdP, 登完回到当前 URL
        await login();
        return;
      }
      try {
        const meInfo = await fetchMe();
        setMe(meInfo);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    })();
  }, [me, setMe, setError]);

  // 单独 callback 路由, 不需要 me
  if (window.location.pathname === "/auth/callback") {
    return (
      <Routes>
        <Route path="/auth/callback" element={<AuthCallback />} />
      </Routes>
    );
  }

  if (loading) {
    return (
      <div
        style={{
          height: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: "var(--text-muted)",
        }}
      >
        加载中… (鲶鱼中央门户)
      </div>
    );
  }

  if (!me) {
    // 已经触发跳 IdP, 这里是过渡态
    return (
      <div
        style={{
          height: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: "var(--text-muted)",
        }}
      >
        正在跳转登录页…
      </div>
    );
  }

  return (
    <div style={{ minHeight: "100vh", background: "var(--bg)" }}>
      <NavBar />
      <main
        style={{
          maxWidth: 1280,
          margin: "0 auto",
          padding: "var(--space-4)",
        }}
      >
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/me" element={<MePage />} />
          {/* BL-CENTRAL-WEB-PURGE-USERDATA (5/17 鸿波):
              /sessions + /kanban 删了 — 它们读员工本机 state.db + tasks.jsonl,
              违反 BL-CENTRAL-EDGE-BOUNDARY 规则. 这俩功能 Companion 自己有.
              老链接 redirect 回首页 (员工开 Companion 看会话 / 看板). */}
          <Route path="/sessions" element={<Navigate to="/" replace />} />
          <Route path="/sessions/*" element={<Navigate to="/" replace />} />
          <Route path="/kanban" element={<Navigate to="/" replace />} />
          <Route path="/skills/*" element={<SkillsHubPage />} />
          <Route path="/mcp/*" element={<McpMarketPage />} />
          <Route path="/manager/*" element={<ManagerPage />} />
          <Route path="/admin/*" element={<AdminPage />} />
          <Route path="/audit" element={<AuditPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
