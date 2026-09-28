#!/usr/bin/env node
/**
 * 编 Companion 之前, 清掉 Rust 编译目录里再也用不上的旧产物 (9/28)。
 *
 * # 为什么
 *
 * 9/28 鸿波「怎么硬盘又被吃了」: src-tauri/target 34G, 其中 target/debug 24G。
 * 查下来是两件事叠在一起, 都是 cargo 自己不会清的:
 *
 *   1. 每次改版本号 (规矩是每个提交都改) —— 版本号进了产物的哈希, 于是整套
 *      Companion 产物换个新名字重来一份: 可执行文件 (~130MB)、build 目录、
 *      增量编译缓存 (~700MB)。旧的那份永远留着。那两天改了十几次版本号,
 *      target/debug 里就躺着 22 份可执行文件、25 份增量缓存。
 *   2. macOS 上 dev 构建默认 split-debuginfo = "unpacked": 调试信息留在每个
 *      .o 里, rustc 不删这些 .o, 每编一次就多 ~270MB (106 批, 12.8G)。
 *      第 2 件已经在 Cargo.toml 的 [profile.dev] 改成 packed 解决了。
 *
 * # 做法 (用 cargo 自己的清理, 不自己猜文件名)
 *
 *   · 版本号变了           → cargo clean -p <本包>   只清 Companion 自己的产物;
 *                           版本一变它本来就要整个重编, 清了不多花时间
 *   · 编译器 / [profile] 变了 → cargo clean --profile <dev|release>
 *                           所有依赖的哈希都变了, 旧的全是孤儿
 *   · 第一次跑 (没有记录)   → 同上, 整个 profile 目录清一次
 *
 * 记录放在 <profile 目录>/.catfish-build-stamp.json。任何一步失败都只提示、
 * 不拦着编译 —— 清理是省硬盘, 不是编译的前提。
 *
 * 用法: node scripts/prune-rust-target.mjs dev|release [target-triple]
 */
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const TAURI = join(dirname(fileURLToPath(import.meta.url)), "..", "src-tauri");
const [mode, triple] = process.argv.slice(2);
if (mode !== "dev" && mode !== "release") {
  console.error("用法: node scripts/prune-rust-target.mjs dev|release [target-triple]");
  process.exit(2);
}

function run(cmd, args) {
  return spawnSync(cmd, args, { cwd: TAURI, encoding: "utf8" });
}

function skip(reason) {
  console.warn(`⚠ 没清理 Rust 编译目录 (${reason}) —— 不影响编译, 只是旧产物还占着硬盘`);
  process.exit(0);
}

const meta = run("cargo", ["metadata", "--format-version", "1", "--no-deps", "--offline"]);
if (meta.status !== 0) skip(`cargo metadata 失败: ${(meta.stderr || meta.error || "").toString().trim()}`);
const metadata = JSON.parse(meta.stdout);
const targetDir = metadata.target_directory;
const { name: pkg, version, manifest_path: manifestPath } = metadata.packages[0];

const rustc = run("rustc", ["-V"]);
if (rustc.status !== 0) skip("rustc -V 失败");
// 编译器版本和 [profile.*] 任何一处变了, 所有依赖的产物都会换哈希。
// 按行切段: 以 "[" 开头的行是段头, 收集 [profile...] 段里的所有行。
const profileLines = [];
let inProfile = false;
for (const line of readFileSync(manifestPath, "utf8").split(/\r?\n/)) {
  if (/^\s*\[/.test(line)) inProfile = /^\s*\[profile/.test(line);
  if (inProfile && line.trim() && !line.trim().startsWith("#")) profileLines.push(line.trim());
}
const env = [rustc.stdout.trim(), ...profileLines].join("\n");

const profileDir = join(targetDir, ...(triple ? [triple] : []), mode === "dev" ? "debug" : "release");
const stampPath = join(profileDir, ".catfish-build-stamp.json");
let stamp = null;
try {
  stamp = JSON.parse(readFileSync(stampPath, "utf8"));
} catch {
  // 没有记录 = 第一次跑, 或者目录刚被清过
}

const selector = [...(mode === "dev" ? ["--profile", "dev"] : ["--release"]), ...(triple ? ["--target", triple] : [])];
let args = null;
if (existsSync(profileDir)) {
  if (!stamp || stamp.env !== env) {
    args = ["clean", "--offline", ...selector];
    console.log(
      stamp
        ? "→ 编译器或 [profile] 变了, 旧的依赖产物全用不上了, 清空 " + profileDir
        : "→ 第一次记录, 清空一次 " + profileDir + " (这次编译会从头来, 要几分钟)",
    );
  } else if (stamp.version !== version) {
    args = ["clean", "--offline", "-p", pkg, ...selector];
    console.log(`→ 版本 ${stamp.version} → ${version}, 清掉 Companion 旧版本的产物 (依赖不动)`);
  }
}
if (args) {
  const out = run("cargo", args);
  const text = `${out.stdout}${out.stderr}`.trim();
  if (out.status !== 0) skip(`cargo ${args.join(" ")} 失败: ${text}`);
  if (text) console.log("  " + text.split("\n").pop());
}
mkdirSync(profileDir, { recursive: true });
writeFileSync(stampPath, JSON.stringify({ version, env }, null, 2) + "\n");
