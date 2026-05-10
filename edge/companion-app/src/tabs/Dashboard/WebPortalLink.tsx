/** Dashboard 顶部 "去中央门户 →" 锚点 — BL-ARCH2 (5/10).
 *
 * 鸿波 5/10 凌晨架构反思: "中央的功能 WEB 化, 助手的功能还是客户端化".
 * Companion 砍了 7 张管理类卡, 这些功能挪去 catfish-web. 这张顶部 banner 给员工
 * 一条进 web 的路, 按 role 显示能进的入口.
 *
 * 路由约定 (跟 catfish-web src/routes/ 对齐):
 *   /me           我的 (跟 Companion 仪表盘同源数据, 浏览器看一眼)
 *   /skills       Skills Hub 全市场
 *   /mcp          MCP 连接器市场
 *   /manager      部门 (manager+)
 *   /audit        审计大查询 (manager+)
 *   /admin        Admin 后台 (admin / sysadmin)
 *   /admin/system 系统管理 (sysadmin)
 *
 * 渲染策略:
 *   - 顶部 banner 一行 (轻样式), 不抢 ProactiveCard / TasksCard 注意力.
 *   - 按 role 过滤链接 (跟 catfish-web NavBar.tsx 同款).
 *   - 没拿到 role (loading) 仍显示通用入口 (我的 / Skills / MCP).
 *
 * BL-ARCH2 fix4 (5/10): catfish-web 没起来时给"未运行"提示, 不让员工点链接看
 * 系统浏览器"无法连接服务器"无声失败 (鸿波: "还是一样的"). 心跳 10s 一次.
 */

import { useEffect, useState } from "react";

import { config } from "../../lib/env";
import { useMe } from "../../hooks/useMe";

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

// 跟 catfish-web NavBar.tsx role 继承一致: sysadmin > admin > manager > employee
const PORTAL_LINKS: PortalLink[] = [
  {
    path: "/me",
    label: "我的总览",
    desc: "在浏览器看本人 quota / 装的 skill / mcp",
    show: () => true,
  },
  {
    path: "/skills",
    label: "Skills Hub",
    desc: "全公司 skill 广场, 装 / publish / 评分",
    show: () => true,
  },
  {
    path: "/mcp",
    label: "MCP 市场",
    desc: "Jira / GitLab / 文件 mcp 订阅",
    show: () => true,
  },
  {
    path: "/manager",
    label: "部门",
    desc: "本部门 quota / 团队 / audit",
    show: (r) => r === "manager" || r === "admin" || r === "sysadmin",
  },
  {
    path: "/audit",
    label: "审计大查询",
    desc: "跨员工 / 跨部门 / 时间段",
    show: (r) => r === "manager" || r === "admin" || r === "sysadmin",
  },
  {
    path: "/admin",
    label: "Admin 后台",
    desc: "用户 / 配额规则 / billing",
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
        gridColumn: "1 / -1",
        background: isOffline
          ? "rgba(239, 68, 68, 0.08)"  // 淡红色, 区分 offline 状态
          : "var(--catfish-bg-elevated)",
        border: `1px solid ${isOffline ? "rgba(239, 68, 68, 0.4)" : "var(--catfish-border)"}`,
        borderRadius: "var(--radius-md)",
        padding: "var(--space-3)",
        marginBottom: "var(--space-3)",
      }}
    >
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
            : <>管理类功能 (跨员工 / 跨部门 / IT) 都搬去 web 了, 桌面端只看"我的"</>}
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
                if (!isOffline) e.currentTarget.style.borderColor = "var(--catfish-accent)";
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
    </section>
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
