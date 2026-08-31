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
import { Navigate, Route, Routes, useLocation, useNavigate, useParams } from "react-router-dom";

import { NavBar } from "./components/NavBar";
import { fetchMe, fetchModelCatalog } from "./lib/me";
import { setRuntimeModelMeta } from "./lib/modelDisplay";
import { getCurrentUser, handleCallback, login } from "./lib/auth";
import { useAuthStore } from "./store/auth";

import { HomePage } from "./routes/HomePage";
import { PasswordPage } from "./routes/PasswordPage";
// BL-CENTRAL-WEB-PURGE-MEPAGE (5/17 鸿波): 删 MePage — 个人数据展示违
// BL-CENTRAL-EDGE-BOUNDARY spirit. 员工自查 → Companion 桌面 app.
// BL-CENTRAL-WEB-CONSOLIDATE (5/17): Skills Hub + MCP 市场合到 /market 下,
// 2 个 sub-tab. 本质同类 (LLM plugin marketplace).
import { MarketPage } from "./routes/MarketPage";
import { AdminPage } from "./routes/AdminPage";
import { LEGACY_REDIRECTS } from "./routes/admin/navConfig";
// BL-CENTRAL-WEB-PURGE-USERDATA (5/17 鸿波): 删 SessionsPage / KanbanPage —
// 它们读员工本机 ~/.hermes/state.db + ~/.catfish/tasks.jsonl, 违反
// BL-CENTRAL-EDGE-BOUNDARY 规则. 这俩功能 Companion 桌面 app 自己有.

/** 老 URL 跳新 URL, 保住路径尾巴和 query.
 *
 * 不能直接用 <Navigate to="/admin/departments"> —— 那样 /manager/研发部 会掉到
 * 部门列表, 用户以为自己点错了。而下面还有个 `path="*"` 兜底跳首页, 所以漏掉的
 * 老链接不会 404, 是**静默回首页**, 更难发现。
 */
function LegacyRedirect({ to }: { to: string }) {
  const tail = useParams()["*"];
  const { search, hash } = useLocation();
  return <Navigate to={`${to}${tail ? `/${tail}` : ""}${search}${hash}`} replace />;
}

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

  // 7/30: 登录后灌一次模型元信息 (显示名 / 颜色 / 单价) 给审计页用。
  //
  // 在这之前这些是硬编码在 lib/modelDisplay.ts 两张表里的。模型改成能在
  // /admin/models 界面上增删改之后, 硬编码会让客户新加的模型在审计页显示
  // "未知模型 ⚪"、成本按兜底价 0.001 算 —— 而真实单价跨度是 0.00005 到
  // 0.0218, 差 400 倍, 那个数字不能当准确值给客户看。
  //
  // 放在 App 层加载一次, 而不是让每个用到的页面各自 fetch: 审计页有三个
  // 组件用 getModelDisplay/costRMB, 各自 fetch 既浪费也容易漏掉一个。
  //
  // 失败不影响任何功能 —— fetchModelCatalog 内部已经吞异常返 []，
  // setRuntimeModelMeta 灌空表就等于退回硬编码表, 也就是 7/30 之前的行为。
  useEffect(() => {
    if (!me) return;
    void fetchModelCatalog().then((models) => {
      if (models.length) setRuntimeModelMeta(models);
    });
  }, [me]);

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
    // P3.5.26.3 (6/17 鸿波"页面固定一屏高度, 滚动条仅仅只能是内容区域"):
    // flex column + height 100vh, NavBar flex: 0 永远 top (自然高度),
    // main flex: 1 + overflowY auto 独立 scroll. 整页不 scroll, 浏览器
    // window 没 scrollbar.
    //
    // 6/17 history (3 次撞坑后最简方案):
    // - 6ebdce7 P3.5.26: height 100vh + main overflow (✓ layout 对) + AuditPage
    //   sticky 头部 negative margin (✗ 撞 Webkit sticky bug + KPI 卡层叠).
    // - 654c978 P3.5.26.1: 去 negative coord 修抖动, 仍 KPI 卡透 sticky.
    // - 1f6b59c P3.5.26.2: revert 整套, NavBar position: sticky 单独, 整页 window scroll.
    //   NavBar 真贴 top 但**整页超 viewport scroll 时晃动** (sticky + window scroll
    //   交互).
    // - 这次 P3.5.26.3: 恢复 height: 100vh + main overflow (P3.5.26 layout), 但
    //   **不**加 sticky 头部. AuditPage PageHeader 普通, 在 main scroll
    //   内自然 scroll. NavBar 真 flex: 0 永远 top, 不需要 sticky (它本来就在
    //   flex column 顶, 不会随 main scroll 移动).
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100vh",
        background: "var(--bg)",
      }}
    >
      <NavBar />
      <main
        style={{
          flex: 1,
          overflowY: "auto",
          maxWidth: 1280,
          margin: "0 auto",
          width: "100%",
          padding: "var(--space-4)",
          boxSizing: "border-box",
          // 8/1: 加 flex column + minHeight 0。
          //
          // 目的是给**想要自己管滚动的页面**一个高度确定的容器。后台那些
          // 表格页要的是"整页不动, 只有数据区滚" —— 那需要一路从这里往下
          // 每一层都有确定高度, 中间任何一层 height:auto 都会把高度交还给
          // 内容, 于是滚动条又跑回 main 上。
          //
          // 对其余页面 (首页 / 市场) 没有影响: 它们的根 div 高度是 auto,
          // 在 flex column 里照样按内容撑开, main 照样滚。
          //
          // minHeight: 0 不能省 —— flex 子项的默认 min-height 是 auto,
          // 也就是"不小于内容", 于是 overflow 永远不生效。这是整套布局里
          // 最容易漏、而且漏了之后表现是"滚动条莫名其妙出现在外层"的一条。
          display: "flex",
          flexDirection: "column",
          minHeight: 0,
        }}
      >
        <Routes>
          <Route path="/account/password" element={<PasswordPage />} />
          <Route path="/" element={<HomePage />} />
          {/* BL-CENTRAL-WEB-PURGE-USERDATA + BL-CENTRAL-WEB-PURGE-MEPAGE
              (5/17 鸿波): 删 /me /sessions /kanban 三页 — 都涉及员工本机/
              个性化数据展示, 违 BL-CENTRAL-EDGE-BOUNDARY 规则. 员工自查身份/
              配额/会话/看板 → 桌面 Companion app. 老链接 redirect 到首页. */}
          <Route path="/me" element={<Navigate to="/" replace />} />
          <Route path="/sessions" element={<Navigate to="/" replace />} />
          <Route path="/sessions/*" element={<Navigate to="/" replace />} />
          <Route path="/kanban" element={<Navigate to="/" replace />} />
          {/* BL-CENTRAL-WEB-CONSOLIDATE (5/17 鸿波): Skills + MCP 合并到 /market.
              老 /skills/* /mcp/* 自动 redirect 到 /market/* 保 bookmark 不破. */}
          <Route path="/market/*" element={<MarketPage />} />
          <Route path="/skills" element={<Navigate to="/market/skills" replace />} />
          <Route path="/skills/*" element={<Navigate to="/market/skills" replace />} />
          <Route path="/mcp" element={<Navigate to="/market/mcp" replace />} />
          <Route path="/mcp/*" element={<Navigate to="/market/mcp" replace />} />
          <Route path="/admin/*" element={<AdminPage />} />
          {/* 7/30 第二步: /manager 和 /audit 并入 /admin (见 navConfig 文件头).
              `${from}/*` 一条同时接住 /manager 和 /manager/研发部 —— splat 为空
              时也匹配。深链的尾巴由 LegacyRedirect 接上去。 */}
          {LEGACY_REDIRECTS.map((r) => (
            <Route
              key={r.from}
              path={`${r.from}/*`}
              element={<LegacyRedirect to={r.to} />}
            />
          ))}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
