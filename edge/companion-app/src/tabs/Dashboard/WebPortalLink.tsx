/** Dashboard 顶部 "去中央门户 →" 锚点 — BL-ARCH2 (5/10).
 *
 * 鸿波 5/10 凌晨架构反思: "中央的功能 WEB 化, 助手的功能还是客户端化".
 * Companion 砍了 7 张管理类卡, 这些功能挪去 catfish-web. 这张顶部 banner 给员工
 * 一条进 web 的路, 按 role 显示能进的入口.
 *
 * 路由约定 (跟 catfish-web src/routes/ 对齐, **5/17 更新**):
 *   /market       📦 资源市场 (Skills + MCP 合并, 内 sub-tab)
 *   /manager      👥 部门 (manager+)
 *   /audit        📜 审计大查询 (manager+)
 *   /admin        ⚙️ Admin 后台 (admin / sysadmin)
 *   /admin/system 🔐 系统管理 (sysadmin)
 *
 * BL-COMPANION-DASHBOARD-SYNC (5/17 鸿波): 老 7 项链接对齐到 web 新 5 项.
 *   - 删 /me — web 整页废了, 员工自查走 Companion 仪表盘本身
 *   - 合 /skills + /mcp → /market — web 已经合并成单一 tab
 *   - emoji 统一 (📦 / 👥 / 📜 / ⚙️ / 🔐), 顺序按权限阶梯
 *
 * BL-ARCH2 fix4 (5/10): catfish-web 没起来时给"未运行"提示, 不让员工点链接看
 * 系统浏览器"无法连接服务器"无声失败 (鸿波: "还是一样的"). 心跳 10s 一次.
 */

import { useEffect, useState } from "react";
import { getVersion } from "@tauri-apps/api/app";

import { config } from "../../lib/env";
import { useMe } from "../../hooks/useMe";
import { useUIStore } from "../../store/ui";

/** BL-ARCH2 fix1 (5/10): Tauri webview 默认吞 `<a target="_blank">`, 必须程序化
 * 调 `@tauri-apps/plugin-shell` 的 `open()` 才能打开系统默认浏览器.
 *
 * 兜底: 非 Tauri 环境 (vite dev preview / storybook / vitest) 走 window.open.
 */
async function openInSystemBrowser(url: string): Promise<void> {
  try {
    const { open } = await import("@tauri-apps/plugin-shell");
    await open(url);
    return;
  } catch (e) {
    // eslint-disable-next-line no-console
    console.warn("[WebPortalLink] shell.open 失败, 退 window.open", e);
  }
  try {
    const w = window.open(url, "_blank", "noopener,noreferrer");
    if (w) return;
  } catch {
    /* ignore */
  }
  // eslint-disable-next-line no-console
  console.error("[WebPortalLink] 所有打开方式都失败:", url);
}

/** BL-ARCH2 fix4 (5/10): catfish-web 连通性检测.
 *
 * 浏览器 fetch CORS 限制 — catfish-web 已配 CORS (vite default + identity-server
 * BL-D6 fix1 都允许 localhost), 但本地探测最稳当用 `mode: 'no-cors'` + 极短超时.
 * 我们不需要响应内容, 只看 fetch 不抛错 (说明 TCP 连通).
 *
 * 状态:
 *   "checking" — 启动 / 切换中
 *   "online"   — 至少一次 ping 成功
 *   "offline"  — fetch 抛错 (Connection refused / timeout)
 */
type WebStatus = "checking" | "online" | "offline";

async function pingWeb(webBase: string, timeoutMs = 2000): Promise<boolean> {
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), timeoutMs);
    // GET / 而不是 HEAD — vite dev server / nginx 都答 200 主页. mode no-cors
    // 避开 CORS preflight (我们不读 body).
    await fetch(`${webBase}/`, {
      method: "GET",
      mode: "no-cors",
      cache: "no-store",
      signal: ctrl.signal,
    });
    clearTimeout(t);
    return true;
  } catch {
    return false;
  }
}

interface PortalLink {
  path: string;
  label: string;
  desc: string;
  show: (role: string) => boolean;
}

// BL-COMPANION-DASHBOARD-SYNC (5/17 鸿波): 跟 catfish-web NavBar.tsx 5 项 nav 对齐.
// role 继承: sysadmin > admin > manager > employee. 老 7 项 → 新 5 项:
//   - 删 /me (整页废了, 员工自查走 Companion 仪表盘本身)
//   - 合 /skills + /mcp → /market (web 已合并成 sub-tab)
//   - emoji 统一, 顺序按权限阶梯升级
const PORTAL_LINKS: PortalLink[] = [
  {
    path: "/market",
    label: "📦 资源市场",
    desc: "Skills (技能脚本) + MCP (连接器) 合并入口, 全公司共享",
    show: () => true,
  },
  {
    path: "/manager",
    label: "👥 部门",
    desc: "本部门 quota / top 员工 / audit",
    show: (r) => r === "manager" || r === "admin" || r === "sysadmin",
  },
  {
    path: "/audit",
    label: "📜 审计大查询",
    desc: "跨员工 / 跨部门 / 时间段 + 趋势 + CSV 导出",
    show: (r) => r === "manager" || r === "admin" || r === "sysadmin",
  },
  {
    path: "/admin",
    label: "⚙️ Admin 后台",
    desc: "用户 / 配额规则 / billing / 部门 RBAC",
    show: (r) => r === "admin" || r === "sysadmin",
  },
  {
    path: "/admin/system",
    label: "🔐 系统管理",
    desc: "服务状态 / 操作审计 / 危险操作",
    show: (r) => r === "sysadmin",
  },
];

export default function WebPortalLink() {
  const { me } = useMe();
  const role = me?.role ?? "employee";
  const links = PORTAL_LINKS.filter((l) => l.show(role));
  const webBase = config.webUrl;

  // BL-ARCH2 fix4 (5/10): 心跳检测 catfish-web 是否在线.
  const [status, setStatus] = useState<WebStatus>("checking");
  useEffect(() => {
    let alive = true;
    const check = async () => {
      const ok = await pingWeb(webBase);
      if (alive) setStatus(ok ? "online" : "offline");
    };
    void check();
    const id = setInterval(check, 15_000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [webBase]);

  const isOffline = status === "offline";

  return (
    <section
      style={{
        // P3.5.120 + P3.5.121 (6/25): 全宽 sticky toolbar 风.
        // P3.5.121 真因 (鸿波 catch "内容滚动后还是露出来"): .app-main padding 24
        // 让 sticky 真**outer width = 100% .app-main 内宽 (扣 padding 48), 两侧 24
        // 空白透下方滚动内容**, 真**sticky top:0 贴 padding-box top, 上方 24 padding-top
        // 空白也透**. 真**修复**: 真**margin 真**抵消 .app-main padding 三向** →
        // outer 真**全 Companion 宽 + 贴 tab 栏底**, 真**内 padding 补回**让 inner
        // content 跟 .app-main padding 节奏一致.
        position: "sticky",
        top: 0,
        zIndex: 10,
        marginTop: "calc(-1 * var(--space-6))",
        marginLeft: "calc(-1 * var(--space-6))",
        marginRight: "calc(-1 * var(--space-6))",
        marginBottom: "var(--space-3)",
        background: isOffline
          ? "rgba(239, 68, 68, 0.08)"  // 淡红色, 区分 offline 状态
          : "var(--catfish-bg-elevated)",
        // toolbar 风 — 只 borderBottom (砍 borderRadius + 四向 border)
        borderBottom: `1px solid ${isOffline ? "rgba(239, 68, 68, 0.4)" : "var(--catfish-border)"}`,
        // 真**水平 padding 补回 24** — 真**inner content 不贴 toolbar 边**, 真**vertical 真 12 toolbar 风**
        padding: "var(--space-3) var(--space-6)",
      }}
    >
      {/* P3.5.120/121: inner content 真**maxWidth 1600 居中** — 跟下方 grid 节奏一致.
          真**0 padding** 因为 outer 真水平 padding 24 已补 .app-main padding 抵消. */}
      <div style={{ maxWidth: 1600, margin: "0 auto" }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-2)",
          marginBottom: "var(--space-2)",
          flexWrap: "wrap",
        }}
      >
        <span style={{ fontSize: 16 }}>🌐</span>
        <strong style={{ fontSize: 13 }}>中央门户</strong>
        <StatusDot status={status} />
        <span
          style={{
            fontSize: 11,
            color: "var(--catfish-text-muted)",
          }}
        >
          {isOffline
            ? <>未运行 (<code>{webBase}</code>) — 启动: <code>cd central/web && npm run dev</code></>
            : <>管理 + 跨员工市场在 web. 桌面端管个人 (对话 / 画像 / skill 装卸 / 配额自查)</>}
        </span>
        {/* BL-COMPANION-ABOUT-CHIP (5/18): 右侧版本徽章, 点开"关于鲶鱼"模态.
            员工不知道自己装的是哪版 / 鲶鱼是啥 / 谁出的, 需要一个入口告诉他们.
            放这儿不占独立行, 不影响 5 张 nav tile 紧凑. */}
        <span style={{ marginLeft: "auto" }}>
          <AboutChip />
        </span>
      </div>
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: "var(--space-2)",
          opacity: isOffline ? 0.5 : 1,
        }}
      >
        {links.map((l) => {
          const fullUrl = `${webBase}${l.path}`;
          return (
            <a
              key={l.path}
              href={fullUrl}
              target="_blank"
              rel="noopener noreferrer"
              title={isOffline ? "中央门户没起, 点也没用" : l.desc}
              onClick={(e) => {
                e.preventDefault();
                if (isOffline) {
                  // BL-ARCH2 fix4: offline 直接拒, 别让员工点了看 Safari 报错
                  return;
                }
                void openInSystemBrowser(fullUrl);
              }}
              style={{
                fontSize: 12,
                color: "var(--catfish-text)",
                background: "var(--catfish-bg)",
                border: "1px solid var(--catfish-border)",
                borderRadius: "var(--radius-sm)",
                padding: "4px 10px",
                textDecoration: "none",
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
                cursor: isOffline ? "not-allowed" : "pointer",
                transition: "border-color 0.15s ease",
              }}
              onMouseEnter={(e) => {
                if (!isOffline) e.currentTarget.style.borderColor = "var(--catfish-cyan)";
              }}
              onMouseLeave={(e) =>
                (e.currentTarget.style.borderColor = "var(--catfish-border)")
              }
            >
              {l.label}
              <span style={{ opacity: 0.5, fontSize: 10 }}>↗</span>
            </a>
          );
        })}
      </div>
      </div>
    </section>
  );
}

/** BL-COMPANION-ABOUT-CHIP (5/18 鸿波): "关于鲶鱼" 徽章.
 *
 * 5/18 BL-COMPANION-ABOUT-HIJACK: 模态 hoist 到 App.tsx 渲染, 这里只点开 store flag,
 * 跟 macOS app menu "关于鲶鱼" 共享一个 AboutModal 实例.
 */
function AboutChip() {
  const [version, setVersion] = useState<string>("…");
  const openAbout = useUIStore((s) => s.openAbout);

  useEffect(() => {
    getVersion()
      .then((v) => setVersion(v))
      .catch((e) => {
        // eslint-disable-next-line no-console
        console.warn("[AboutChip] getVersion 失败:", e);
        setVersion("?");
      });
  }, []);

  return (
    <button
      type="button"
      onClick={openAbout}
      title="关于鲶鱼 — 版本 / 介绍"
      style={{
        fontSize: 11,
        color: "var(--catfish-text-muted)",
        background: "transparent",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-sm)",
        padding: "2px 8px",
        cursor: "pointer",
        fontFamily: "inherit",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.borderColor = "var(--catfish-accent)";
        e.currentTarget.style.color = "var(--catfish-text)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.borderColor = "var(--catfish-border)";
        e.currentTarget.style.color = "var(--catfish-text-muted)";
      }}
    >
      鲶鱼 v{version}
    </button>
  );
}

function StatusDot({ status }: { status: WebStatus }) {
  const color =
    status === "online"
      ? "rgb(34, 197, 94)"
      : status === "offline"
        ? "rgb(239, 68, 68)"
        : "rgb(156, 163, 175)";
  const label =
    status === "online" ? "在线" : status === "offline" ? "未运行" : "检测中";
  return (
    <span
      title={label}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        fontSize: 11,
        color: "var(--catfish-text-muted)",
      }}
    >
      <span
        style={{
          width: 8,
          height: 8,
          borderRadius: "50%",
          background: color,
        }}
      />
      {label}
    </span>
  );
}
