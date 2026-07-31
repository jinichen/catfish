#!/usr/bin/env node
/** 后台路由自查 (7/30 第二步配套).
 *
 * navConfig.ts 让侧栏和 <Route> 守卫同源了, TS 也保证了"漏配组件 = 编译错误"。
 * 但有三件事类型系统管不到, 而它们的失败**都是静默的**:
 *
 *   1. **navConfig 的 require 跟页面自己的 RoleGate 对不上。**
 *      AuditPage / ManagerPage / SystemPage 等页面内部还有一道自己的 RoleGate。
 *      两处不一致时严格的那道生效 —— 侧栏按松的那份显示, 用户点进去看 403。
 *      类型只管 require 存在, 不管它写得对。
 *
 *   2. **代码里还有 <Link to="/老路径">。** App.tsx 末尾是
 *      `<Route path="*" element={<Navigate to="/" replace />} />`,
 *      所以写错的路径不会 404, 是**跳回首页**。没人会报 bug, 只会觉得"点了没反应"。
 *
 *   3. **Companion 那份硬编码路径漂了。** 它在另一个仓, 跨仓引用 web 的 URL,
 *      没有任何编译期关联。
 *
 * 跑: node scripts/check-admin-routes.mjs   (退出码非 0 = 有问题)
 */

import { readFileSync, existsSync, readdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const WEB = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const COMPANION = resolve(WEB, "../../edge/companion-app");

const problems = [];
const read = (p) => readFileSync(p, "utf8");

// ── 1. 解析 navConfig 的路由表 ───────────────────────────────────────────
const nav = read(resolve(WEB, "src/routes/admin/navConfig.ts"));
const routes = [];
for (const m of nav.matchAll(
  /\{\s*path:\s*"([^"]*)"[\s\S]*?require:\s*(ADMIN|MANAGER|SYSADMIN)/g,
)) {
  routes.push({ path: m[1], require: m[2] });
}
if (routes.length < 10) {
  problems.push(`navConfig 只解析出 ${routes.length} 条路由 — 正则跟文件结构脱节了`);
}

// ── 2. navConfig 的 require vs 页面自己的 RoleGate ──────────────────────
// 页面文件位置。null = 组件定义在 AdminPage.tsx 内部, 没有独立文件。
const PAGE_FILES = {
  "": null,
  models: "src/routes/admin/ModelConfigPage.tsx",
  quota: "src/routes/admin/QuotaConfigPage.tsx",
  access: "src/routes/admin/AccessPage.tsx",
  users: "src/routes/admin/UsersPage.tsx",
  departments: "src/routes/ManagerPage.tsx",
  audit: "src/routes/AuditPage.tsx",
  perf: "src/routes/admin/PerfPage.tsx",
  "quota/events": null,
  system: "src/routes/admin/SystemPage.tsx",
  facts: "src/routes/admin/FactsPage.tsx",
  advisory: "src/routes/admin/AdvisoryPage.tsx",
};

/** RoleGate 的 require 归一成 ADMIN / MANAGER / SYSADMIN 三档之一。 */
function normalizeGate(raw) {
  const roles = [...raw.matchAll(/"(\w+)"/g)].map((m) => m[1]).sort().join(",");
  if (roles === "admin,manager") return "MANAGER";
  if (roles === "admin,sysadmin") return "ADMIN";
  if (roles === "sysadmin") return "SYSADMIN";
  if (roles === "admin") return "ADMIN";
  if (roles === "manager") return "MANAGER";
  return `??(${roles})`;
}

for (const r of routes) {
  const rel = PAGE_FILES[r.path];
  if (rel === undefined) {
    problems.push(`navConfig 有 "${r.path}" 但本脚本 PAGE_FILES 没登记 — 补一条`);
    continue;
  }
  if (rel === null) continue; // 组件在 AdminPage.tsx 内, 只有外层守卫
  const file = resolve(WEB, rel);
  if (!existsSync(file)) {
    problems.push(`"${r.path}" → ${rel} 不存在`);
    continue;
  }
  const src = read(file);
  const gate = src.match(/<RoleGate\s+require=\{(\[[^\]]*\]|"[^"]*")\}/);
  if (!gate) continue; // 页面自己没守卫 —— 现在由路由那道兜住, 不算问题
  const own = normalizeGate(gate[1]);
  if (own !== r.require) {
    problems.push(
      `${rel}: 自己的 RoleGate 是 ${own}, navConfig 写的是 ${r.require} — ` +
        `严格的那道生效, 侧栏会显示一个点进去 403 的项`,
    );
  }
}

// ── 3. 全仓找指向已搬走路径的链接 ──────────────────────────────────────
const MOVED = { "/manager": "/admin/departments", "/audit": "/admin/audit" };
const SKIP_DIRS = new Set(["node_modules", "dist", ".git", "target"]);

function sourceFiles(dir, acc = []) {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = `${dir}/${e.name}`;
    if (e.isDirectory()) {
      if (!SKIP_DIRS.has(e.name)) sourceFiles(p, acc);
    } else if (/\.(tsx?|jsx?)$/.test(e.name)) {
      acc.push(p);
    }
  }
  return acc;
}

function scanMovedLinks(root, label, skip = []) {
  for (const f of sourceFiles(root)) {
    if (skip.some((s) => f.includes(s))) continue;
    const src = read(f);
    for (const [from, to] of Object.entries(MOVED)) {
      // 只认导航写法: to="/manager" / to={`/manager/x`} / href="/audit" / path: "/audit".
      // 这样 `${gatewayUrl}/api/audit/me` 这类 API 调用不会被误报 —— 它们没有这些前缀。
      const re = new RegExp(`(to=|href=|path:\\s*)\\{?["'\`]${from}(["'\`/])`);
      if (re.test(src)) {
        problems.push(
          `${label} ${f.slice(root.length)} 还在链到 ${from} — 应该是 ${to}` +
            ` (写错不会 404, 是静默跳首页)`,
        );
      }
    }
  }
}

// navConfig.ts 里的 LEGACY_REDIRECTS 就是专门写老路径的, 跳过。
scanMovedLinks(resolve(WEB, "src"), "web", ["navConfig.ts"]);
scanMovedLinks(resolve(COMPANION, "src"), "companion");

// ── 报告 ────────────────────────────────────────────────────────────────
if (problems.length === 0) {
  console.log(`✓ 后台路由自查通过 (${routes.length} 条路由)`);
  process.exit(0);
}
console.error(`✗ 后台路由自查发现 ${problems.length} 个问题:\n`);
for (const p of problems) console.error(`  · ${p}`);
process.exit(1);
