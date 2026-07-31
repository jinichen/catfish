#!/usr/bin/env node
/** 一屏布局的两条硬约束 (8/1).
 *
 * ## 背景
 *
 * 后台是"整页不动, 只有数据区滚": 页面标题、筛选条、分页器一直在手边。
 * 这要求从 App.tsx 的 <main> 一路往下每一层都有确定高度:
 *
 *   main (flex column + minHeight:0)
 *     AdminLayout (flex:1 + minHeight:0, 内容列 flex column)
 *       PageShell scroll="data"   ← 自己不滚
 *         Section fill            ← 吃掉剩余高度
 *           DataTable fill        ← 表身滚 + 表头 sticky
 *
 * ## 这个脚本挡什么
 *
 * **1. `scroll="data"` 但页面里没有任何 `fill`。**
 * 那一页自己 `overflow: hidden` 而里面没人接管滚动 —— 内容直接被裁掉,
 * **而且没有滚动条**。表现是"下面半页内容凭空消失", 比整页滚糟得多,
 * 而且不报错、类型也管不到 (两个 prop 在不同文件、不同组件上)。
 *
 * **2. 一页标了多个 `<Section fill>`。**
 * 剩余高度会被平分, 变成两块各滚各的, 比整页滚更难用。
 *
 * 反过来 (`scroll="page"` 却标了 fill) 不拦: 那是安全的 —— fill 在一个
 * 能滚的父级里最多是"没吃满剩余高度", 不会丢内容。
 *
 * 跑法: npm run check:page-scroll (已并进 npm run check)
 */

import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const WEB = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const ROUTES = join(WEB, "src/routes");

function walk(dir) {
  const out = [];
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name);
    if (e.isDirectory()) out.push(...walk(p));
    else if (e.name.endsWith(".tsx")) out.push(p);
  }
  return out;
}

const problems = [];
let dataPages = 0;
let pagePages = 0;

for (const file of walk(ROUTES)) {
  const src = readFileSync(file, "utf8");
  const rel = relative(WEB, file);

  const dataMode = /<PageShell\b[^>]*scroll=\{?["']data["']/.test(src);
  const hasShell = /<PageShell\b/.test(src);
  if (!hasShell) continue;
  if (dataMode) dataPages++;
  else pagePages++;

  // `fill` 作为独立的 JSX 布尔属性。
  // ⚠ 必须跨行匹配: 属性多的时候写法是
  //     <Section
  //       fill
  //       title={...}
  // 第一版写的是 `<Section fill[\s>]`, 只认同一行 —— QuotaEventsPage 恰好
  // 是换行写法, 于是"一页只能有一个 fill"那条对它完全失效, 而且脚本还
  // 打勾通过。检查自己静默失效比没有检查更糟。
  const fillAttr = /^[ \t]*fill[ \t]*$/gm;                       // 独占一行
  const sectionFillRe = /<Section\b[^>]*\bfill\b/g;              // 同一行 / 跨行都认
  const fillCount =
    (src.match(fillAttr) ?? []).length + (src.match(sectionFillRe) ?? []).length;

  if (dataMode && fillCount === 0)
    problems.push(
      `${rel}: PageShell scroll="data" 但整个文件里没有一个 fill。\n` +
        `      这一页自己不滚、也没人接管滚动 —— 超出的内容会被直接裁掉,\n` +
        `      而且不会有滚动条。要么给数据区那个 <Section> 加 fill,\n` +
        `      要么把 PageShell 改回默认的 scroll="page"。`,
    );

  const sectionFill = (src.match(sectionFillRe) ?? []).length;
  if (sectionFill > 1)
    problems.push(
      `${rel}: 有 ${sectionFill} 个 <Section fill>。\n` +
        `      剩余高度会被它们平分, 变成两块各滚各的 —— 比整页滚更难用。\n` +
        `      一页只留一个。`,
    );
}

if (problems.length) {
  console.error("\n❌ 一屏布局有问题:\n");
  for (const p of problems) console.error("   · " + p + "\n");
  process.exit(1);
}

console.log(
  `✓ 一屏布局自查通过 (${dataPages} 页只滚数据区, ${pagePages} 页整页滚)`,
);
