/** P3.5.45 (鸿波 6/20 拍 'A: 砍录屏 HTTP path').
 *
 * 老版本: 11 个 HTTP fetch /api/learn/* → gateway → hermes P7 → tool-bridge sock,
 *         4 跳 + 2 个 HTTP 中转 + token 验签, 30 天 SERVICE_TOKEN 过期 cascade fail.
 *
 * 新版本: invoke("recmode_rpc", {method, params}) → Tauri 端直调 tool-bridge sock,
 *         1 跳 + 0 token 验签. 跟 chat 路径彻底解耦.
 *
 * # 为啥跟 chat 不同 path
 *
 * chat 必经 hermes (LLM 调用走 LiteLLM, hermes 直连上游 deepseek/qwen 公网 API).
 * 录屏纯本机操作 (CDP keyframes / screenshot / events jsonl / fs IO), tool-bridge
 * 已在 Companion 同台机器, 没必要绕 HTTP. 现在两条 path:
 *   - chat: Companion → hermes (API_SERVER_KEY) → LiteLLM → 上游 LLM
 *   - 录屏: Companion → tool-bridge unix sock (直接)
 *
 * gateway /api/learn/* 11 个 endpoint 在 ship 后期清理 (留作 backward compat,
 * 老版本 Companion 可以仍 work; 新版 Companion 一律走 Tauri command).
 */

import { invoke } from "@tauri-apps/api/core";

// ─── 类型 (跟老版兼容) ─────────────────────────────────────

export interface StartRecordingResponse {
  session_id: string;
  started_at: number;
  output_dir: string;
  ws_connected: boolean;
  viewer?: string;  // 老版 gateway 注入, 新版没了 (前端没用过)
}

export interface StopRecordingResponse {
  session_id: string;
  started_at: number;
  stopped_at: number;
  duration_s: number;
  events_count: number;
  keyframes_count: number;
  output_dir: string;
  events_path: string;
  viewer?: string;
}

export interface AnalyzeResponse {
  skill_name: string;
  namespace: string;
  skill_dir: string;
  steps_count: number;
  confidence: number;
  questions_for_user: string[];
  viewer?: string;
}

export interface RecordingStatus {
  session_id: string;
  started_at: number;
  elapsed_s: number;
  events_count: number;
  keyframes_count: number;
  ws_connected: boolean;
}

export interface SkillContentResponse {
  skill_dir: string;
  skill_md: string;
  main_py: string;
  recmode_meta: unknown | null;
}

export interface SaveSkillResponse {
  ok: boolean;
  final_dir: string;
  namespace: string;
  name: string;
  moved: boolean;
}

export interface TestSkillResponse {
  ok: boolean;
  returncode?: number;
  stdout?: string;
  stderr?: string;
  duration_s: number;
  skill_path: string;
  error?: string;
}

// ─── 内部: Tauri RPC wrapper ──────────────────────────────

/** Tauri 端 (commands/recmode.rs:recmode_rpc) 调 tool-bridge sock.
 *
 * method 必须 "recmode/" 前缀 (Rust 端守卫白名单).
 * Rust 端自动注入 catfish_home env, 前端不用传.
 */
async function _rpc<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
  return await invoke<T>("recmode_rpc", {
    method: `recmode/${method}`,
    params,
  });
}

// ─── session_id 生成 (老接口保留) ─────────────────────────

/** 生成 session_id (rec_<timestamp>_<random>), Companion 端控制保持唯一. */
export function newRecModeSessionId(): string {
  const ts = Math.floor(Date.now() / 1000);
  const rand = Math.random().toString(36).slice(2, 8);
  return `rec_${ts}_${rand}`;
}

// ─── 11 个 endpoint 改 Tauri command ──────────────────────

/** 开 CDP listener 录屏. */
export async function startRecording(
  sessionId: string,
  options: { chromeWs?: string; connectWs?: boolean } = {},
): Promise<StartRecordingResponse> {
  return _rpc<StartRecordingResponse>("start_recording", {
    session_id: sessionId,
    chrome_ws: options.chromeWs ?? "ws://localhost:9222",
    connect_ws: options.connectWs ?? true,
  });
}

/** 停录, 拿录制 summary. */
export async function stopRecording(sessionId: string): Promise<StopRecordingResponse> {
  return _rpc<StopRecordingResponse>("stop_recording", {
    session_id: sessionId,
  });
}

/** 触发 aggregator (LLM 综合 → 落 SKILL.md). LLM 调用慢, Rust 端给 10 分钟超时. */
export async function analyzeRecording(
  sessionId: string,
  skillsRoot?: string,
): Promise<AnalyzeResponse> {
  return _rpc<AnalyzeResponse>("analyze", {
    session_id: sessionId,
    skills_root: skillsRoot,
  });
}

/** 列当前在录的 session (调试 / 监控). */
export async function listActiveRecordings(): Promise<{ active_session_ids: string[] }> {
  return _rpc<{ active_session_ids: string[] }>("active", {});
}

/** 实时拿 keyframes / events 计数 + 录制时长 (RecordingOverlay 2s poll). */
export async function getRecordingStatus(sessionId: string): Promise<RecordingStatus> {
  return _rpc<RecordingStatus>("status", { session_id: sessionId });
}

/** Companion 转写完调一下, tool-bridge 落 transcripts.jsonl. */
export async function recordTranscript(
  sessionId: string,
  text: string,
  options: { tsOffset?: number; duration?: number } = {},
): Promise<{ ok: boolean; lines_count: number }> {
  return _rpc<{ ok: boolean; lines_count: number }>("record_transcript", {
    session_id: sessionId,
    text,
    ts_offset: options.tsOffset ?? 0,
    duration: options.duration ?? 0,
  });
}

/** 读 SKILL.md / main.py / recmode_meta 内容 (给 preview UI). */
export async function getSkillContent(skillDir: string): Promise<SkillContentResponse> {
  return _rpc<SkillContentResponse>("skill_content", { skill_dir: skillDir });
}

/** 用户点保存把 draft mv 到正式 skills. */
export async function saveSkill(
  draftDir: string,
  options: { namespace?: string; name?: string; keepForever?: boolean } = {},
): Promise<SaveSkillResponse> {
  return _rpc<SaveSkillResponse>("save_skill", {
    draft_dir: draftDir,
    namespace: options.namespace ?? "",
    name: options.name ?? "",
    keep_forever: options.keepForever ?? false,
  });
}

/** 触发 catfish CLI 跑这个 skill 一次, 拿结果. */
export async function testSkill(
  skillDir: string,
  params: Record<string, unknown> = {},
): Promise<TestSkillResponse> {
  return _rpc<TestSkillResponse>("test_skill", {
    skill_dir: skillDir,
    params,
  });
}

/** V2 #68 selector 漂移修: skill 跑时 find_by_text(hint) 找不到 → vision 看截图找新 selector. */
export async function repairSelector(
  args: {
    skillPath: string;
    stepNo: number;
    hint: Record<string, unknown>;
    screenshotB64?: string;
  },
): Promise<unknown> {
  return _rpc("repair_selector", {
    skill_path: args.skillPath,
    step_no: args.stepNo,
    hint: args.hint,
    screenshot_b64: args.screenshotB64 ?? null,
  });
}

/** 清理 recordings/ 老 session (GC). */
export async function cleanupRecordings(
  args: { olderThanDays?: number; dryRun?: boolean } = {},
): Promise<{ ok: boolean; removed: number; freed_bytes: number }> {
  return _rpc<{ ok: boolean; removed: number; freed_bytes: number }>("cleanup", {
    older_than_days: args.olderThanDays ?? 30,
    dry_run: args.dryRun ?? false,
  });
}
