#!/usr/bin/env node
/** 前端的截断上限常量必须跟后端 SQL 的 LIMIT 一致 (8/1).
 *
 * ## 为什么值得一个脚本
 *
 * 后端每个分组查询都有 LIMIT。前端拿这些数判断"这张表是不是被截断了",
 * 而截断这件事影响三处显示:
 *
 *   · 表尾"只列前 N 名"的说明 —— 没有它, 用户分不清"公司就这些"和"被截了"
 *   · 占比之和不足 100% 的解释
 *   · 数据对账告警要不要跳过这一维
 *
 * 两边不一致的后果**全是静默的**: 后端把 50 改成 100 之后, 前端仍然在
 * 第 50 行画出"只列前 50 名", 而实际有 100 行; 或者反过来, 对账告警
 * 永久亮着。没有任何一处会报错, 表现只是"数字看起来有点怪"。
 *
 * 8/1 之前这些数在前端有四份裸写法 (AdminHome 的 BREAKDOWN_LIMIT、
 * PerfPage 两处裸的 20、AuditBreakdowns 裸的 50)。
 *
 * ## 两组常量, 不是一组
 *
 * `AUDIT_TOP_N` ← quota_audit.py `audit_summary_global_since` 全公司审计
 * `DEPT_TOP_N`  ← quota_audit.py `audit_summary_dept_since`   单个部门
 * `PERF_TOP_N`  ← metrics_perf.py `query_perf_summary_global` 性能页
 *
 * 三组不是一组: 不同的函数、不同的表、不同的排序键, 而且**值本来就不同**
 * (全公司 by_user 是 50, 部门 by_user 是 10)。合成一组的话, 改一处会把
 * 另外两处的判断悄悄改错。所以这里分别校验。
 *
 * ## 后端源码不在的时候
 *
 * 这个脚本要读 central/llm-gateway 下的 Python 源码, 而 `npm run check` 挂在
 * `npm run build` 上, build 又在 web 的 Dockerfile 里跑 —— 那里的构建上下文
 * 只有 central/web 一个目录 (docker-compose.yml `context: ./web`), llm-gateway
 * 根本不在。第一版直接 readFileSync 会让 **web 镜像构建整个失败**,
 * 而且失败在 CI 里看不见 (CI 是完整 checkout, 文件在)。
 *
 * "CI 绿、交付出去的镜像坏"正是这个 Dockerfile 里已经写过一次的教训
 * (见 central/web/Dockerfile 里 chmod a+rX 那段)。
 *
 * 所以: 后端源码不在就跳过并说明为什么。真正的把关发生在完整 checkout 里。
 *
 * ## 已知盲区
 *
 * 只认 SQL 里的 `LIMIT n`。metrics.py 的 JSONL 兜底路径 (没配 PG 时) 用的是
 * Python 切片 `[:20]`, 这里看不见 —— 把 PG 那条改成 50、前端常量跟着改 50,
 * 这个脚本会放行, 而走 JSONL 的部署仍然只返 20。
 *
 * 没有一并覆盖是因为切片的写法太自由 (变量名、常量、内联数字都可能),
 * 正则匹配它的假阳性会比它挡住的问题还多。改 metrics.py 的 LIMIT 时
 * 请手工确认两条路径都改了。
 *
 * 跑法: npm run check:audit-limits (已并进 npm run check)
 */

import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const ME_TS = resolve(here, "../src/lib/me.ts");
const GW = resolve(here, "../../llm-gateway/src/catfish_gateway");
const QUOTA_PY = resolve(GW, "quota.py");
const QUOTA_AUDIT_PY = resolve(GW, "quota_audit.py");
const METRICS_PY = resolve(GW, "metrics.py");
const METRICS_PERF_PY = resolve(GW, "metrics_perf.py");

function fail(msg) {
  console.error(`\n❌ ${msg}\n`);
  process.exit(1);
}

// 判断的是**整棵后端源码树在不在**, 不是单个文件在不在。
//
// 按单个文件判断的话, 有人把 metrics.py 改名或挪位置 → 检查静默消失、
// exit 0、CI 全绿 —— 而这个脚本存在的全部意义就是"别让不一致静默"。
// 目录在 = 完整 checkout, 那么文件必须在, 不在就是真出事了。
if (!existsSync(GW)) {
  console.log(
    "· 跳过截断上限自查: 找不到 llm-gateway 源码树 (只有前端的构建上下文, " +
      "比如 web 镜像的 docker build)。完整 checkout 里会真跑。",
  );
  process.exit(0);
}
for (const [f, why] of [
  [QUOTA_PY, "审计分组的 LIMIT"],
  [METRICS_PY, "性能分组的 LIMIT"],
])
  if (!existsSync(f))
    fail(
      `${f} 不在, 但 llm-gateway 源码树在 —— 文件被改名或挪走了?\n` +
        `   这个脚本靠它校验${why}。路径要跟着更新, 不然检查会静默失效。`,
    );

// quota.py 现在是兼容旧 import 的 re-export 壳, 审计函数实际在
// quota_audit.py。旧 checkout 仍可能把实现放在 quota.py, 两种布局都支持。
const QUOTA_SOURCE = existsSync(QUOTA_AUDIT_PY) ? QUOTA_AUDIT_PY : QUOTA_PY;
// metrics.py 同样是兼容旧 import 的 re-export 壳, 性能聚合实际在
// metrics_perf.py。旧 checkout 仍可能把实现放在 metrics.py。
const METRICS_SOURCE = existsSync(METRICS_PERF_PY) ? METRICS_PERF_PY : METRICS_PY;

// ── 前端声明的值 ────────────────────────────────────────────────────
const meSrc = readFileSync(ME_TS, "utf8");

/** 读 `export const <名字> = { k: n, ... } as const;` 里的数字。 */
function readConst(name, keys) {
  const m = meSrc.match(new RegExp(`${name}\\s*=\\s*\\{([^}]*)\\}`));
  if (!m)
    fail(
      `在 ${ME_TS} 里找不到 ${name} 的声明。\n` +
        `   期望形如: export const ${name} = { ${keys
          .map((k) => `${k}: 20`)
          .join(", ")} } as const;\n` +
        `   改了写法的话这个脚本也要跟着改 —— 它静默失效比不存在更糟。`,
    );
  const out = {};
  for (const k of keys) {
    const kv = m[1].match(new RegExp(`\\b${k}\\s*:\\s*(\\d+)`));
    if (!kv) fail(`${name} 里少了 ${k}`);
    out[k] = Number(kv[1]);
  }
  return out;
}

const AUDIT = readConst("AUDIT_TOP_N", ["model", "department", "user"]);
const DEPT = readConst("DEPT_TOP_N", ["model", "user"]);
const PERF = readConst("PERF_TOP_N", ["model", "department"]);

// ── 后端 SQL 里的 LIMIT ──────────────────────────────────────────────

/** 一个 Python 顶层函数的函数体。
 *
 * 边界必须认 `class` / `async def` / 装饰器, 不能只认 `\ndef` ——
 * 只认 def 的话函数体会一路吞到下一个 def, 把中间的 class 也算进来。
 * 那样别处新加的 SQL 会被误算成这个函数的, 而症状是**前端构建挂掉**,
 * 排查的人完全想不到是后端某个类里加了一句 SQL。 */
function funcBody(src, name, file) {
  const start = src.indexOf(`def ${name}(`);
  if (start < 0) fail(`在 ${file} 里找不到 ${name}`);
  const rest = src.slice(start + 1);
  const end = rest.search(/\n(?:@|class |async def |def )\w/);
  return end < 0 ? rest : rest.slice(0, end);
}

/** 把 `GROUP BY <目标> ORDER BY ... LIMIT <n>` 按目标归类。
 *  `classify` 返回维度名, 返回 null = 这条不关我们的事。 */
function limitsIn(body, classify, where) {
  const found = {};
  const re = /GROUP BY\s+([\w, ]+?)\s+ORDER BY\s+[^\n]*?LIMIT\s+(\d+)/gi;
  let m;
  while ((m = re.exec(body)) !== null) {
    const target = m[1].trim().toLowerCase();
    const dim = classify(target);
    if (dim === null) continue;
    if (dim === undefined)
      fail(`${where}: 认不出的 GROUP BY 目标 "${target}" —— 脚本要跟着更新`);
    (found[dim] ??= new Set()).add(Number(m[2]));
  }
  return found;
}

const auditFound = limitsIn(
  funcBody(
    readFileSync(QUOTA_SOURCE, "utf8"),
    "audit_summary_global_since",
    QUOTA_SOURCE,
  ),
  (t) =>
    t === "model" ? "model" : t === "dept" ? "department" : t.startsWith("ue") ? "user" : undefined,
  "quota.py audit_summary_global_since",
);
const deptFound = limitsIn(
  funcBody(
    readFileSync(QUOTA_SOURCE, "utf8"),
    "audit_summary_dept_since",
    QUOTA_SOURCE,
  ),
  (t) => (t === "model" ? "model" : t === "user_email" ? "user" : undefined),
  "quota.py audit_summary_dept_since",
);
const perfFound = limitsIn(
  funcBody(
    readFileSync(METRICS_SOURCE, "utf8"),
    "query_perf_summary_global",
    METRICS_SOURCE,
  ),
  (t) => (t === "model" ? "model" : t === "department" ? "department" : undefined),
  "metrics.py query_perf_summary_global",
);

// ── 对齐 ────────────────────────────────────────────────────────────
const problems = [];

function compare(label, front, found, source) {
  for (const dim of Object.keys(front)) {
    const seen = [...(found[dim] ?? [])];
    if (seen.length === 0)
      problems.push(`${source}: 没找到 ${dim} 那条 GROUP BY ... LIMIT —— SQL 改写过?`);
    else if (seen.length > 1)
      problems.push(
        `${source}: ${dim} 的两套实现 (PG / SQLite) LIMIT 不一样: ${seen.join(" vs ")}` +
          ` —— 同一个接口在不同部署下返回的行数不同, 这本身就是 bug。`,
      );
    else if (seen[0] !== front[dim])
      problems.push(
        `${source}: 后端 ${dim} LIMIT ${seen[0]}, 前端 ${label}.${dim} = ${front[dim]}`,
      );
  }
}

compare("AUDIT_TOP_N", AUDIT, auditFound, "quota.py audit_summary_global_since");
compare("DEPT_TOP_N", DEPT, deptFound, "quota.py audit_summary_dept_since");
compare("PERF_TOP_N", PERF, perfFound, "metrics.py query_perf_summary_global");

if (problems.length)
  fail(
    "截断上限前后端对不上:\n" +
      problems.map((p) => `   · ${p}`).join("\n") +
      `\n\n   前端常量: ${ME_TS}`,
  );

console.log(
  `✓ 截断上限自查通过 (全公司 ${AUDIT.model}/${AUDIT.department}/${AUDIT.user} · ` +
    `部门 ${DEPT.model}/${DEPT.user} · 性能 ${PERF.model}/${PERF.department}, 前后端一致)`,
);
