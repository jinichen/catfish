/** BL-LEARN-RECMODE Companion 端 client (5/14 Day 2 #64).
 *
 * 包装 catfish-gateway 的 4 个 /api/learn/* endpoints, 给 Companion UI 用.
 * 用 fetchWithAuth (跟 lib/me.ts 同模式) 自动带 dev token / OIDC bearer.
 */

import { fetchWithAuth } from "./me";
import { config } from "./env";

export interface StartRecordingResponse {
  session_id: string;
  started_at: number;
  output_dir: string;
  ws_connected: boolean;
  viewer: string;
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
  viewer: string;
}

export interface AnalyzeResponse {
  skill_name: string;
  namespace: string;
  skill_dir: string;
  steps_count: number;
  confidence: number;
  questions_for_user: string[];
  viewer: string;
}

async function _post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetchWithAuth(config.backendUrl + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = "";
    try {
      const j = await res.json();
      detail = j.detail || JSON.stringify(j);
    } catch {
      detail = await res.text();
    }
    throw new Error(`HTTP ${res.status}: ${detail}`);
  }
  return res.json();
}

async function _get<T>(path: string): Promise<T> {
  const res = await fetchWithAuth(config.backendUrl + path);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  return res.json();
}

/** 生成 session_id (rec_<timestamp>_<random>), Companion 端控制保持唯一. */
export function newRecModeSessionId(): string {
  const ts = Math.floor(Date.now() / 1000);
  const rand = Math.random().toString(36).slice(2, 8);
  return `rec_${ts}_${rand}`;
}

/** POST /api/learn/start_recording — 开 CDP listener 录屏. */
export async function startRecording(
  sessionId: string,
  options: { chromeWs?: string; connectWs?: boolean } = {},
): Promise<StartRecordingResponse> {
  return _post<StartRecordingResponse>("/api/learn/start_recording", {
    session_id: sessionId,
    chrome_ws: options.chromeWs ?? "ws://localhost:9222",
    connect_ws: options.connectWs ?? true,
  });
}

/** POST /api/learn/stop_recording — 停录, 拿录制 summary. */
export async function stopRecording(sessionId: string): Promise<StopRecordingResponse> {
  return _post<StopRecordingResponse>("/api/learn/stop_recording", {
    session_id: sessionId,
  });
}

/** POST /api/learn/analyze — 触发 aggregator (调 main 综合 → 落 SKILL.md). */
export async function analyzeRecording(
  sessionId: string,
  skillsRoot?: string,
): Promise<AnalyzeResponse> {
  return _post<AnalyzeResponse>("/api/learn/analyze", {
    session_id: sessionId,
    skills_root: skillsRoot,
  });
}

/** GET /api/learn/active — 列当前在录的 session (调试 / 监控). */
export async function listActiveRecordings(): Promise<{ active_session_ids: string[] }> {
  return _get<{ active_session_ids: string[] }>("/api/learn/active");
}

// ─── F: 录制中实时状态 polling ─────────────────────────────

export interface RecordingStatus {
  session_id: string;
  started_at: number;
  elapsed_s: number;
  events_count: number;
  keyframes_count: number;
  ws_connected: boolean;
}

/** GET /api/learn/status/<sid> — 实时拿 keyframes / events 计数 + 录制时长. */
export async function getRecordingStatus(sessionId: string): Promise<RecordingStatus> {
  return _get<RecordingStatus>(`/api/learn/status/${encodeURIComponent(sessionId)}`);
}

// ─── A: Companion 把语音转写写到 transcripts.jsonl ──────────

/** POST /api/learn/record_transcript — Companion 转写完调一下 gateway 落 jsonl. */
export async function recordTranscript(
  sessionId: string,
  text: string,
  options: { tsOffset?: number; duration?: number } = {},
): Promise<{ ok: boolean; lines_count: number }> {
  return _post("/api/learn/record_transcript", {
    session_id: sessionId,
    text,
    ts_offset: options.tsOffset ?? 0,
    duration: options.duration ?? 0,
  });
}

// ─── B: 读 SKILL.md / main.py 内容给 preview UI ────────────

export interface SkillContentResponse {
  skill_dir: string;
  skill_md: string;
  main_py: string;
  recmode_meta: any | null;
}

/** GET /api/learn/skill_content?skill_dir=... */
export async function getSkillContent(skillDir: string): Promise<SkillContentResponse> {
  return _get<SkillContentResponse>(
    `/api/learn/skill_content?skill_dir=${encodeURIComponent(skillDir)}`,
  );
}

// ─── C: 用户点保存把 draft mv 到正式 skills ────────────────

export interface SaveSkillResponse {
  ok: boolean;
  final_dir: string;
  namespace: string;
  name: string;
  moved: boolean;
}

/** POST /api/learn/save_skill */
export async function saveSkill(draftDir: string): Promise<SaveSkillResponse> {
  return _post<SaveSkillResponse>("/api/learn/save_skill", {
    draft_dir: draftDir,
  });
}

// ─── E: 跑一次试 ───────────────────────────────────────────

export interface TestSkillResponse {
  ok: boolean;
  returncode?: number;
  stdout?: string;
  stderr?: string;
  duration_s: number;
  skill_path: string;
  error?: string;
}

/** POST /api/learn/test_skill — 触发 catfish CLI 跑这个 skill 一次, 拿结果. */
export async function testSkill(
  skillDir: string,
  params: Record<string, unknown> = {},
): Promise<TestSkillResponse> {
  return _post<TestSkillResponse>("/api/learn/test_skill", {
    skill_dir: skillDir,
    params,
  });
}
