/**
 * 本机服务与网关 · gateway / hermes / Codex / chrome / local_search /
 * tool_bridge / health / picker / roles / logs
 *
 * 2026-08-15 从 lib/tauri.ts 切出来 (1309 行超限)。纯搬迁, 逻辑一行未改。
 * 边界照抄原文件里作者早就画好的 `// ── xxx ──` 分节, 不是我另起的划分。
 *
 * lib/tauri.ts 现在是 barrel, 只做 re-export —— 62 个调用方一行没动。
 */

import { invoke as rawInvoke } from "@tauri-apps/api/core";
import type { HermesBootstrapProgress } from "./hermesBootstrap";
import { config } from "./env";
import type { ServiceStatus } from "../types/service";
import type { CatalogResponse } from "../types/catalog";

// ── gateway (P39 5/22 解耦收尾: start/stop/get_dev_token 3 wrapper 删,
//    只留 status 探活 — 生产员工机 gateway 由 launchctl/客户 IT 管) ──
export const gatewayStatus = () => rawInvoke<ServiceStatus>("gateway_status");

// ── hermes (P3.5.125 6/26 鸿波 catch "hermes hang 不监控") ──
// hermes 默认 launchd 拉, 但 hang (GIL/IO block) launchd 不知道.
// hermesKill : kill -9 后等 launchd 自动重启.
export const hermesStatus = () => rawInvoke<ServiceStatus>("hermes_status");
export const hermesKill = () => rawInvoke<void>("hermes_kill");
export const reinstallHermesAgent = () =>
  rawInvoke<void>("reinstall_hermes_agent");
export const getHermesBootstrapStatus = () =>
  rawInvoke<HermesBootstrapProgress | null>("hermes_bootstrap_status");

// ── Codex 后端（Hermes 原生 codex_app_server runtime）──
export interface CodexBackendStatus {
  installed: boolean;
  binaryPath: string | null;
  version: string | null;
  supported: boolean;
  loggedIn: boolean;
  hermesReady: boolean;
  enabled: boolean;
  runtime: string;
  provider: string | null;
  model: string | null;
  models: string[];
  ready: boolean;
  requiresNewSession: boolean;
  message: string;
}

export const codexBackendStatus = () =>
  rawInvoke<CodexBackendStatus>("codex_backend_status");
export const codexBackendSetEnabled = (enabled: boolean) =>
  rawInvoke<CodexBackendStatus>("codex_backend_set_enabled", { enabled });
export const codexBackendSelectModel = (model: string) =>
  rawInvoke<CodexBackendStatus>("codex_backend_select_model", { model });
export const codexBackendOpenLogin = () =>
  rawInvoke<void>("codex_backend_open_login");

// 教学流程凭据：密码只经 Tauri IPC 写入操作系统凭据库，不进入 shell 或聊天。
//
/** 存一条凭据。
 *
 *  `sites` 三态，别混：
 *    省略        —— 这次不提站点，原有站点**原样留着**（改密码走这条）
 *    `[...]`     —— 用这一串替换
 *    `[]`        —— 明确清空
 *
 *  省略时 `{ sites: undefined }` 经 JSON 序列化会丢掉这个键，Rust 侧收到 `None`
 *  （跟 `profileNextRecomputeAt(days?)` 同一套）。这不是巧合上的依赖 —— Rust
 *  那边 `None` 和 `Some([])` 是分开处理的，不然改个密码会把多入口配置抹掉。 */
export const saveTeachingCredential = (
  label: string,
  password: string,
  sites?: string[],
) => rawInvoke<string>("teaching_credential_save", { label, password, sites });

/** 给已有凭据再挂一个网站 —— 多入口共用一个密码走这条，不用重输密码。
 *
 *  典型：`eis.ffcs.cn` 存过了，登录时跳到 `neis.ffcs.cn`，教学那边报
 *  needs_credential。返回这条凭据更新后的完整站点列表。
 *
 *  **不碰密码**，所以没有 password 参数。 */
export const addTeachingCredentialSite = (label: string, site: string) =>
  rawInvoke<string[]>("teaching_credential_add_site", { label, site });

/** 本机记过的凭据标签。**只有标签，没有密码** —— 密码始终只在系统凭据库里。
 *
 *  这个列表来自 `~/.catfish/teaching_credentials.json`，不是从系统凭据库枚举
 *  出来的（keyring 没有枚举 API，macOS 也没法按前缀搜）。所以它可能有残项：
 *  员工在「钥匙串访问」里手工删过的，这里还会列出来。删一次就清掉了。 */
export interface TeachingCredential {
  label: string;
  reference: string;
  createdAt: string;
  /** 这条凭据管哪些网站（hostname）。tool-bridge 按 `page.url` 查的就是它。
   *
   *  老数据里没这个字段（Rust 侧 `skip_serializing_if`，空的就不写进文件），
   *  所以这里是可选的。空 = 不参与按站点查找。 */
  sites?: string[];
}

export const listTeachingCredentials = () =>
  rawInvoke<TeachingCredential[]>("teaching_credential_list");

/** 删一条。系统凭据库里已经没有也算成功（否则残项永远清不掉）。 */
export const deleteTeachingCredential = (label: string) =>
  rawInvoke<void>("teaching_credential_delete", { label });

// ── chrome ───────────────────────────────────────────────
export const chromeLaunch = () => rawInvoke<void>("chrome_launch");
export const chromeKill = () => rawInvoke<void>("chrome_kill");
export const chromeStatus = () => rawInvoke<ServiceStatus>("chrome_status");

// ── local_search ─────────────────────────────────────────
export const localSearchStart = () => rawInvoke<void>("local_search_start");
export const localSearchStop = () => rawInvoke<void>("local_search_stop");
export const localSearchStatus = () =>
  rawInvoke<ServiceStatus>("local_search_status");

// ── tool_bridge ──────────────────────────────────────────
export const toolBridgeStart = () => rawInvoke<void>("tool_bridge_start");
export const toolBridgeStop = () => rawInvoke<void>("tool_bridge_stop");
export const toolBridgeStatus = () =>
  rawInvoke<ServiceStatus>("tool_bridge_status");
export interface ToolInfo {
  name: string;
  description: string;
  /** snake_case 跟 Python tool-bridge 协议对齐;不要改 camelCase 否则解析空 */
  input_schema: Record<string, unknown>;
  emoji: string;
  toolset: string;
  available: boolean;
  supported?: boolean;
  reason_code?: "unsupported_platform" | "runtime_unavailable" | null;
}
export interface ToolCallResult {
  ok: boolean;
  tool: string;
  result: unknown;
  error: string | null;
}
export const toolBridgeListTools = () =>
  rawInvoke<ToolInfo[]>("tool_bridge_list_tools");
// BL-TODO-BRIDGE-STORE (5/16): sessionId 可选, 用于 per-session stateful tool 注入
// (hermes todo 工具按 session 各自 TodoStore). 不传 → backend 走 __default__ 全局 store.
export const toolBridgeCallTool = (
  name: string,
  args: Record<string, unknown>,
  sessionId?: string,
) =>
  rawInvoke<ToolCallResult>("tool_bridge_call_tool", {
    name,
    args,
    sessionId,  // Tauri 命令 camelCase ↔ Rust snake_case 自动转换
  });

/** P44 (6/5 鸿波 marathon) / P44.4 真根因 fix (6/6): Companion ApprovalButton
 *  onClick 调这个 — fetch hermes API server POST /v1/sessions/{sid}/approval
 *  (plugin P15.2 加的 route). 走 hermes daemon 进程内 resolve_gateway_approval,
 *  跟 P15 _approval_notify 在同一进程, _gateway_queues dict 共享.
 *
 *  老实现走 tool-bridge socket — 跨进程, _gateway_queues 是空 dict, resolve
 *  返 0, block 不解. 已改 fetch 模式.
 *
 *  session_key 从 SSE event `hermes.tool.progress` (status=approval_pending) 的
 *  approval_session_key 字段拿. choice ∈ "once"|"session"|"always"|"deny". */
export const toolBridgeChatApproval = async (
  sessionKey: string,
  choice: "once" | "session" | "always" | "deny",
): Promise<{ resolved?: number; choice?: string }> => {
  const url = `${config.backendUrl}/v1/sessions/${encodeURIComponent(sessionKey)}/approval`;
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (config.useHermes && config.hermesAuthHeader) {
    headers["Authorization"] = config.hermesAuthHeader;
  }
  // BL-CSP-PROXY (7/18 鸿波): 走 Rust reqwest 代理, CSP 严格.
  const { fetchViaProxy } = await import("./http_proxy");
  const resp = await fetchViaProxy(url, {
    method: "POST",
    headers,
    body: JSON.stringify({ choice }),
  });
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`chat_approval HTTP ${resp.status}: ${text.slice(0, 200)}`);
  }
  return (await resp.json()) as { resolved?: number; choice?: string };
};

// BL-DASHBOARD-HERMES-MEMORY-CARD (5/16): 读 hermes 0.13 真活 memory 文件
export interface HermesMemoryView {
  user_entries: string[];
  memory_entries: string[];
  total_bytes: number;
  user_char_limit: number;
  memory_char_limit: number;
  user_file_path: string;
  memory_file_path: string;
}
export const hermesMemoryRead = () =>
  rawInvoke<HermesMemoryView>("hermes_memory_read");
// P3.3.49 (6/12): Rust 端直接改 ~/.hermes/memories/{USER,MEMORY}.md, 绕过
// hermes memory_tool silent fail. byte-exact match + trim() fallback.
export const hermesMemoryRemove = (target: "user" | "memory", entryText: string) =>
  rawInvoke<void>("hermes_memory_remove", { target, entryText });

// P3.3.54 (6/12 鸿波): 审计员看的 xlsx 多 sheet 导出.
export interface AuditExportResult {
  outputPath: string;
  bytesWritten: number;
  decisionsCount: number;
  toolCallsCount: number;
  outboundCount: number;
  chainOk: boolean;
  chainBrokenReason: string | null;
}
export const auditExportXlsx = (
  fromTs: string | null,
  toTs: string | null,
  includeDecisions: boolean,
  includeToolCalls: boolean,
  includeOutbound: boolean,
) =>
  rawInvoke<AuditExportResult>("audit_export_xlsx", {
    fromTs,
    toTs,
    includeDecisions,
    includeToolCalls,
    includeOutbound,
  });

// P3.3.58 (6/12 鸿波): 钓鱼邮件识别 — 批量查邮件 phishing 扫描结果.
export type PhishingSeverity = "high" | "medium" | "low" | "none";
export interface PhishingFlag {
  ruleId: string;
  severity: PhishingSeverity;
  category: string;
  reason: string;
  matchedText?: string | null;
}
export interface PhishingScanResult {
  messageId: string;
  scannedAt: string;
  flags: PhishingFlag[];
  highestSeverity: PhishingSeverity;
  llmVerdict?: string | null;
  llmReason?: string | null;
}
export const emailPhishingGet = (ids: string[]) =>
  rawInvoke<Record<string, PhishingScanResult>>("email_phishing_get", { ids });

/** P3.3.58 段 2B (6/12 鸿波): 前端 trigger 钓鱼扫描. 跟 emailClassifyNow 同款.
 *  items 跟 email_classify_now 同 shape (id/subject/sender/account/date/is_read).
 *  返完整 store snapshot. 已扫的跳过, 新 id 走 light scan + LLM batch.
 */
export const emailPhishingScanNow = (
  items: Array<{
    id: string;
    subject: string;
    sender: string;
    account?: string;
    date?: string;
    is_read?: boolean;
  }>,
) => rawInvoke<Record<string, PhishingScanResult>>("email_phishing_scan_now", { items });

// P3.3.53 (6/13 鸿波): 政治敏感规则引擎 — yaml 可配置, 默认关.
// 集团信安 / 党办下发 keywords_l1 / keywords_l2 / regex_patterns 后, 员工
// 打开邮件 detail pane 时本机扫. 默认 enabled=false 时 engineEnabled=false 返,
// 前端不显 badge / 红条.
export type PoliticalSeverity = "high" | "medium" | "low" | "none";
export type PoliticalFlagLevel = "l1_redline" | "l2_sensitive" | "regex";
export interface PoliticalFlag {
  ruleId: string;
  level: PoliticalFlagLevel;
  severity: PoliticalSeverity;
  matchedLabel: string;     // 关键词脱敏 (前 4 字 + 长度) 防日志回显
  excerpt?: string | null;  // 命中位置上下文 (前后 20 字)
}
export interface PoliticalScanResult {
  messageId: string;
  scannedAt: string;
  flags: PoliticalFlag[];
  highestSeverity: PoliticalSeverity;
  llmVerdict?: string | null;
  llmReason?: string | null;
  /** 引擎是否开启. false → flags 必空, UI 不挂 badge */
  engineEnabled: boolean;
}

/** detail pane 打开邮件时调 — 拿到 body 后扫. 已扫过的 id 直接返缓存. */
export const emailPoliticalScanNow = (
  id: string,
  subject: string,
  sender: string,
  body: string,
) => rawInvoke<PoliticalScanResult>("email_political_scan_now", {
  id, subject, sender, body,
});

/** 批量查已扫的结果 (从 POLITICAL_STORE), 没扫过的 id 不出现在返 map. */
export const emailPoliticalGet = (ids: string[]) =>
  rawInvoke<Record<string, PoliticalScanResult>>("email_political_get", { ids });

// P3.4.1 (6/13 鸿波): mcp OAuth token 本机存. callback 拿到 token 后立即落
// ~/.catfish/mcp/oauth-tokens/<token_ref_local> 文件 0600. 中央 0 持有.
export interface McpOauthTokenSaveResult {
  absPath: string;
  bytes: number;
}
export const mcpOauthTokenSave = (tokenRefLocal: string, accessToken: string) =>
  rawInvoke<McpOauthTokenSaveResult>("mcp_oauth_token_save", {
    tokenRefLocal,
    accessToken,
  });
export const mcpOauthTokenDelete = (tokenRefLocal: string) =>
  rawInvoke<void>("mcp_oauth_token_delete", { tokenRefLocal });

// P3.3.55 (6/12 鸿波): 审计视图 Tab 拿 raw jsonl row 列表.
export interface ToolCallRow {
  ts: string;
  tool: string;
  ok: boolean;
  error: string | null;
  latencyMs: number;
  argsPreview: string;
}
export const auditDecisionsRawRead = (
  fromTs: string | null,
  toTs: string | null,
  limit: number,
) => rawInvoke<Record<string, unknown>[]>("audit_decisions_raw_read", { fromTs, toTs, limit });

export const auditHermesJsonlRead = (
  fromTs: string | null,
  toTs: string | null,
  limit: number,
) => rawInvoke<ToolCallRow[]>("audit_hermes_jsonl_read", { fromTs, toTs, limit });

// P3.3.55: 校验哈希链
export interface ChainVerifyReport {
  ok: boolean;
  totalLines: number;
  expectedCount: number;
  verifiedLines: number;
  brokenAt: number | null;
  brokenReason: string | null;
  chainFirstSha256: string;
  chainLastSha256: string;
}
export const auditChainVerify = (jsonlPath: string) =>
  rawInvoke<ChainVerifyReport>("audit_chain_verify", { jsonlPath });

// ── health ───────────────────────────────────────────────
export const fetchHealthz = () =>
  rawInvoke<{ status: string; service: string }>("healthz");
export const fetchCatalog = () => rawInvoke<CatalogResponse>("catalog");

// ── picker_model (P3.5.139 Phase 4 6/29 鸿波"重启 Companion picker 应该记得这次选择") ─
// 读 ~/.catfish/picker_model (P3.5.28 写的单文件), 启动时同步到 zustand store.model.
// 没 file / 空字符串 → null, caller 不动 store, 走 catalog.default 兜底.
export const getPickerModel = () => rawInvoke<string | null>("get_picker_model");

// ── roles: 8/9 整块删 ─────────────────────────────────────
//
// 原来这里透传 Rust role_config::roles_get_all, 给 fetchRole("chat_default")
// 之类做"picker 没选时的兜底模型"。8/9 鸿波「模型只能 picker 模型」——
// 兜底改成读 picker 落盘的那份, 这条链就没有 caller 了, 连同 Rust 侧
// services/role_config.rs 一起删。
//
// 想加回来之前先想清楚: 它是 picker 之外的第二个模型来源, 而员工在界面上
// 看不出自己用的是哪一个。

// ── logs ─────────────────────────────────────────────────
// 默认 fromEnd=false: 先 dump 老日志, 然后 tail 新增 ——
// 员工切到 LogPanel 立刻能看到内容,不至于面对空白。
// 长期跑的 server 日志通常 <1MB,完整 dump 一次没什么开销。
export const tailLogs = (service: string, fromEnd = false) =>
  rawInvoke<void>("tail", { service, fromEnd });
export const stopTailLogs = (service: string) =>
  rawInvoke<void>("stop_tail", { service });
