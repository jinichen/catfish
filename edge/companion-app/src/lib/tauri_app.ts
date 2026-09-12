/**
 * 应用自身 · 技能与 MCP 安装 / 系统 / 偏好 / 文件
 *
 * 2026-08-15 从 lib/tauri.ts 切出来 (1309 行超限)。纯搬迁, 逻辑一行未改。
 * 边界照抄原文件里作者早就画好的 `// ── xxx ──` 分节, 不是我另起的划分。
 *
 * lib/tauri.ts 现在是 barrel, 只做 re-export —— 62 个调用方一行没动。
 *
 * 9/12: 桌宠副窗 (BL-E27) 的 pet_* 命令全删。
 */

import { invoke as rawInvoke } from "@tauri-apps/api/core";

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

// ── Hermes Profile 专家 Bot 管理器（默认关闭）──────────────────
export interface ExpertBotsStatus {
  enabled: boolean;
  ready: boolean;
  advisorProfile: string;
  reason: string;
}

export const expertBotsStatus = () =>
  rawInvoke<ExpertBotsStatus>("expert_bots_status");

export const expertBotsSetEnabled = (enabled: boolean) =>
  rawInvoke<ExpertBotsStatus>("expert_bots_set_enabled", { enabled });

export type ExpertBotModelPolicy =
  | { mode: "inherit_picker"; model_id?: null }
  | { mode: "fixed"; model_id: string };

export interface ExpertBotSummary {
  id: string;
  displayName: string;
  description: string;
  managedByCompanion: boolean;
  enabled: boolean;
  ready: boolean;
  reason: string;
  modelPolicy: ExpertBotModelPolicy;
  configuredModel: string | null;
  provider: string | null;
  skillCount: number;
  boundScenarios: string[];
}

export interface AvailableExpertProfile {
  id: string;
  displayName: string;
  description: string;
  registered: boolean;
  managedByCompanion: boolean;
}

export interface ExpertBotScenario {
  id: string;
  label: string;
  profileId: string | null;
}

export interface ExpertBotsSnapshot {
  enabled: boolean;
  bots: ExpertBotSummary[];
  availableProfiles: AvailableExpertProfile[];
  scenarios: ExpertBotScenario[];
}

export interface ExpertBotCreateInput {
  id: string;
  displayName: string;
  description: string;
  soul: string;
  cloneFrom?: string | null;
  modelPolicy: ExpertBotModelPolicy;
}

export interface ExpertBotUpdateInput {
  id: string;
  displayName?: string | null;
  description?: string | null;
  soul?: string | null;
  enabled?: boolean | null;
  modelPolicy?: ExpertBotModelPolicy | null;
}

export interface ExpertBotRoute {
  enabled: boolean;
  ready: boolean;
  profileId: string | null;
  model: string;
  reason: string;
}

export const expertBotsList = () =>
  rawInvoke<ExpertBotsSnapshot>("expert_bots_list");
export const expertBotCreate = (input: ExpertBotCreateInput) =>
  rawInvoke<ExpertBotsSnapshot>("expert_bot_create", { input });
export const expertBotUpdate = (input: ExpertBotUpdateInput) =>
  rawInvoke<ExpertBotsSnapshot>("expert_bot_update", { input });
export const expertBotRegisterExisting = (profileId: string) =>
  rawInvoke<ExpertBotsSnapshot>("expert_bot_register_existing", { profileId });
export const expertBotUnregister = (profileId: string) =>
  rawInvoke<ExpertBotsSnapshot>("expert_bot_unregister", { profileId });
export const expertBotDelete = (profileId: string) =>
  rawInvoke<ExpertBotsSnapshot>("expert_bot_delete", { profileId });
export const expertBotBind = (scenario: string, profileId: string | null) =>
  rawInvoke<ExpertBotsSnapshot>("expert_bot_bind", { scenario, profileId });
export const expertBotSoulGet = (profileId: string) =>
  rawInvoke<string>("expert_bot_soul_get", { profileId });
export const expertBotRoute = (scenario: string, pickerModel: string) =>
  rawInvoke<ExpertBotRoute>("expert_bot_route", { scenario, pickerModel });


// ── file (Phase 2 优雅下载: skill 生成的文件,在 Finder 打开/显示) ───
/** 在 Finder/资源管理器里高亮选中文件 (macOS: open -R). */
export const revealInFinder = (path: string) =>
  rawInvoke<void>("reveal_in_finder", { path });
/** 用系统默认 app 打开文件 (macOS: open <path>). */
export const openFile = (path: string) =>
  rawInvoke<void>("open_file", { path });
