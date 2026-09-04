/**
 * 应用自身 · 桌宠副窗与跨窗通信 / 技能与 MCP 安装 / 系统 / 偏好 / 文件
 *
 * 2026-08-15 从 lib/tauri.ts 切出来 (1309 行超限)。纯搬迁, 逻辑一行未改。
 * 边界照抄原文件里作者早就画好的 `// ── xxx ──` 分节, 不是我另起的划分。
 *
 * lib/tauri.ts 现在是 barrel, 只做 re-export —— 62 个调用方一行没动。
 */

import { invoke as rawInvoke } from "@tauri-apps/api/core";

// ── BL-E27 spike: 桌宠副窗 ─────────────────────────────────
export const petShow = () => rawInvoke<void>("pet_show");
export const petHide = () => rawInvoke<void>("pet_hide");
export const petIsVisible = () => rawInvoke<boolean>("pet_is_visible");


// ── E7 phase 2 (6/6): skill 安装/卸载, MCP 接入/移除 ───────────
//
// 流程:
//   - installSkillFromUrl: 走 `npx -y skills add <url>` (Tauri spawn npx subprocess),
//     返 stdout / stderr / exitCode 让 UI 显安装日志
//   - uninstallSkill: 移到 ~/.catfish/.trash/skills/<ts>/, 返 trashPath + originalPath,
//     UI 5 秒 toast 内可调 restoreSkill 恢复
//   - restoreSkill: undo button onClick, 5 秒内有效, 把 trash 内的 skill 移回原路径
//   - addMcpServer / removeMcpServer: 改 ~/.hermes/config.yaml, 重启 hermes 后生效
//     拒绝员工动 catfish-* 命名 (主链路核心 — 后端 hard reject)

export interface InstallResult {
  success: boolean;
  stdout: string;
  stderr: string;
  exitCode: number | null;
}

export interface UninstallResult {
  trashPath: string;
  originalPath: string;
}

export const installSkillFromUrl = (url: string) =>
  rawInvoke<InstallResult>("install_skill_from_url", { url });

// P3.3.23 (6/11): 装外部 skill zip (ClawHub / Anthropic .skill / 任何 SKILL.md zip).
//   zipBytes 走 Vec<u8> (从 File.arrayBuffer() → Array.from(new Uint8Array(buf))).
//   namespace 默认 "external", 装到 ~/.catfish/skills/<ns>/<slug>/.
//   warnings 含跳过的非白名单文件名 (.exe / .py 等被滤).
export interface InstallSkillFromZipResult {
  success: boolean;
  installedPath: string;
  filesCount: number;
  warnings: string[];
}

export const installSkillFromZip = (zipBytes: number[], namespace?: string) =>
  rawInvoke<InstallSkillFromZipResult>("install_skill_from_zip", {
    zipBytes,
    namespace: namespace ?? null,
  });

export const uninstallSkill = (skillPath: string) =>
  rawInvoke<UninstallResult>("uninstall_skill", { skillPath });

export const restoreSkill = (trashPath: string, originalPath: string) =>
  rawInvoke<void>("restore_skill", { trashPath, originalPath });

export const addMcpServer = (
  name: string,
  command: string,
  args: string[],
) => rawInvoke<void>("add_mcp_server", { name, command, args });

export const removeMcpServer = (name: string) =>
  rawInvoke<void>("remove_mcp_server", { name });


// ── system ───────────────────────────────────────────────
export const sendNotification = (title: string, body: string) =>
  rawInvoke<void>("notify", { title, body });

// ── prefs (BL-COMPANION-PREFS-TOGGLES 5/20) ───────────────
// 读 email scheduler 当前 effective 配置 (truth source: ~/.catfish/companion.yaml).
// AgentPrefsCard 显当前 rate_enabled 状态 + 打开 yaml 按钮.
export const emailConfigGet = () =>
  rawInvoke<EmailConfigPublic>("email_config_get");

export interface EmailConfigPublic {
  poll_secs: number;
  rate_enabled: boolean;
  /**
   * P3.5.139 (6/29 鸿波"都要去除硬编码"): null = yaml/env 没显式 override,
   * 评级走 chain (picker > role > Err). AgentPrefsCard 现在不展示这字段,
   * 改 null 无 UI break. 后续要展示走 useRole hook 拿 effective 值.
   */
  rate_model: string | null;
  /** Windows Foxmail 自定义 Storage 目录；null = 自动探测。 */
  foxmail_root: string | null;
  yaml_path: string;
}


// ── 桌宠跨窗通信 (5/6: Tauri 跨 webview event 不通, 走 Rust polling buffer) ─
export interface PetEmitBubbleDiag {
  pet_window_exists: boolean;
  pet_visible: boolean;
  /** 已 push 到 polling buffer, pet.tsx 300ms 内拉走 */
  queued: boolean;
}
/** 主窗调: 桌宠头顶冒气泡, 8s 后自动收. */
export const petEmitBubble = (text: string, agentName?: string) =>
  rawInvoke<PetEmitBubbleDiag>("pet_emit_bubble", { text, agentName });
/** 主窗调: 切桌宠 4 状态 (idle / thinking / running / done). */
export const petEmitStatus = (status: "idle" | "thinking" | "running" | "done") =>
  rawInvoke<void>("pet_emit_status", { status });

// ── file (Phase 2 优雅下载: skill 生成的文件,在 Finder 打开/显示) ───
/** 在 Finder/资源管理器里高亮选中文件 (macOS: open -R). */
export const revealInFinder = (path: string) =>
  rawInvoke<void>("reveal_in_finder", { path });
/** 用系统默认 app 打开文件 (macOS: open <path>). */
export const openFile = (path: string) =>
  rawInvoke<void>("open_file", { path });
