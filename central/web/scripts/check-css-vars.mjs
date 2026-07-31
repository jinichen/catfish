#!/usr/bin/env node
/** 查代码里引用了但从没定义过的 CSS 变量 (7/30).
 *
 * ## 为什么需要这个
 *
 * CSS 自定义属性引用不存在的变量时**不会报错**, 行为分两种, 两种都是静默的:
 *
 *   · `color: var(--nope)` —— 整条声明 "invalid at computed-value time",
 *     等价于 `unset`。颜色变继承值, 边框直接消失。
 *   · `color: var(--nope, #dc2626)` —— 兜底值生效, 看起来正常, 但这个颜色
 *     游离在设计系统之外, 换主题时不会跟着变。
 *
 * 7/30 第一次跑这个检查, 逮到 8 个从没定义过的变量、18 处引用:
 *
 *   --border-soft    QuotaConfigPage 表格行分隔线 → **根本没有分隔线**
 *   --accent-primary AccessPage 保存按钮背景 → **按钮没有背景色**
 *   --accent-error   AccessPage "加载失败/保存失败" → **不是红的**
 *   --bg-muted / --text-primary / --danger / --warn / --status-bad
 *
 * 全是肉眼可见的问题, 存在了几个月没人发现 —— 因为浏览器不说, 构建也不说。
 *
 * 跑: node scripts/check-css-vars.mjs   (退出码非 0 = 有问题)
 */

import { readFileSync, readdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const WEB = resolve(dirname(fileURLToPath(import.meta.url)), "..");

// 变量定义在 index.html 的 :root 里 (这个项目没有单独的 css 文件)
const html = readFileSync(resolve(WEB, "index.html"), "utf8");
const defRaw = new Map(
  [...html.matchAll(/^\s*(--[a-zA-Z0-9-]+)\s*:\s*([^;]+);/gm)].map((m) => [m[1], m[2].trim()]),
);
const defined = new Set(defRaw.keys());
if (defined.size === 0) {
  console.error("✗ index.html 里一个 CSS 变量都没解析到 — 正则跟文件结构脱节了");
  process.exit(1);
}

const SKIP_DIRS = new Set(["node_modules", "dist", ".git"]);
function sourceFiles(dir, acc = []) {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = `${dir}/${e.name}`;
    if (e.isDirectory()) {
      if (!SKIP_DIRS.has(e.name)) sourceFiles(p, acc);
    } else if (/\.(tsx?|css|html)$/.test(e.name)) {
      acc.push(p);
    }
  }
  return acc;
}

/** name → { file → { dead: n, fallback: n } } */
const missing = new Map();
for (const f of sourceFiles(resolve(WEB, "src"))) {
  const src = readFileSync(f, "utf8");
  for (const m of src.matchAll(/var\(\s*(--[a-zA-Z0-9-]+)\s*(,)?/g)) {
    const [, name, comma] = m;
    if (defined.has(name)) continue;
    const byFile = missing.get(name) ?? new Map();
    const rec = byFile.get(f) ?? { dead: 0, fallback: 0 };
    rec[comma ? "fallback" : "dead"]++;
    byFile.set(f, rec);
    missing.set(name, byFile);
  }
}

// ── 已定义变量上挂着的"死兜底" ──────────────────────────────────────────
//
// `var(--accent, #0d9488)` —— --accent 是定义了的, 所以 #0d9488 永远不会生效。
// 它不是 bug, 但有害: 读代码的人会以为那就是 accent 的颜色 (实际是 #2d8a87,
// 完全不同的青色), 而且哪天变量真被改名, 这行会**静默切到一个错的颜色**
// 而不是显眼地坏掉。
const deadFallback = new Map();
for (const f of sourceFiles(resolve(WEB, "src"))) {
  const src = readFileSync(f, "utf8");
  for (const m of src.matchAll(/var\(\s*(--[a-zA-Z0-9-]+)\s*,/g)) {
    if (!defined.has(m[1])) continue; // 未定义的上面已经报过
    const byFile = deadFallback.get(m[1]) ?? new Map();
    byFile.set(f, (byFile.get(f) ?? 0) + 1);
    deadFallback.set(m[1], byFile);
  }
}

if (missing.size === 0 && deadFallback.size === 0) {
  console.log(`✓ CSS 变量自查通过 (index.html 定义 ${defined.size} 个, 引用全部命中)`);
  process.exit(0);
}

if (missing.size === 0) {
  console.error(`✗ 有 ${deadFallback.size} 个变量挂着永远不会生效的兜底值:\n`);
  for (const [name, byFile] of [...deadFallback].sort()) {
    console.error(`  var(${name}, …) —— ${name} 已定义为 ${defRaw.get(name)}, 兜底是死的`);
    for (const [f, n] of byFile) console.error(`      ${f.slice(WEB.length + 1)}  ×${n}`);
  }
  console.error(`\n修法: 删掉逗号后面那截, 直接写 var(${[...deadFallback][0][0]})。`);
  process.exit(1);
}

console.error(`✗ 有 ${missing.size} 个 CSS 变量被引用但从没定义过:\n`);
for (const [name, byFile] of [...missing].sort()) {
  const dead = [...byFile.values()].reduce((a, r) => a + r.dead, 0);
  const fb = [...byFile.values()].reduce((a, r) => a + r.fallback, 0);
  console.error(`  ${name}`);
  if (dead) console.error(`    · ${dead} 处没有兜底值 → 整条声明失效 (边框会消失 / 颜色会变继承值)`);
  if (fb) console.error(`    · ${fb} 处有兜底值 → 看起来正常, 但这个值游离在设计系统外`);
  for (const [f, r] of byFile) {
    console.error(`      ${f.slice(WEB.length + 1)}  (dead ${r.dead} / fallback ${r.fallback})`);
  }
}
console.error(`\n修法: 改用 index.html :root 里已有的名字, 或者把这个变量真的定义进去。`);
console.error(`已定义的: ${[...defined].sort().join(" ")}`);
process.exit(1);
