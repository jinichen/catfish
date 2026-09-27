#!/usr/bin/env node
/**
 * 起 Companion (tauri dev) / 打 Mac 包之前, 先把邮件组件资源包重建一遍 (9/28)。
 *
 * 为什么: 邮件组件 (Python) 不跟前端一起编译。它从
 * src-tauri/resources/mac-<arch>/catfish-email-dist.tar.gz 装进 hermes 的 venv,
 * 而这个包原来只在手动跑 build-mac-resources.sh 时才重建。9/28 实际发生的:
 * 前端已经是 1.0.49 (邮箱设置面板是新的), 邮件组件还是 9/27 那一份 —— 修过的
 * 读信脚本根本没装上, 界面照样报同一个语法错, 看上去像"修了没用"。
 *
 * 重建之后包的指纹变了, Companion 启动时 (hermes_install addon_current) 会自动
 * 重装邮件组件。重建本身几秒钟 (一个零依赖 wheel + AppleScript 编译检查)。
 *
 * 只在 macOS 上做; 别的平台什么都不做 (Windows 包在 CI 里自己打 wheel)。
 * 用法: node scripts/refresh-email-resource.mjs [aarch64|x64]  (默认按本机架构)
 */
import { spawnSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

if (process.platform !== "darwin") process.exit(0);

const arch = process.argv[2] || (process.arch === "arm64" ? "aarch64" : "x64");
const script = join(dirname(fileURLToPath(import.meta.url)), "build-email-resource.sh");
const result = spawnSync("bash", [script, arch], { stdio: "inherit" });
if (result.status !== 0) {
  console.error("❌ 邮件组件资源包没建成 (上面有原因) —— 不启动, 免得跑着一份旧的邮件组件");
  process.exit(result.status ?? 1);
}
