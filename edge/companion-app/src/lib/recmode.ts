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
  const res = await fetchWithAuth(config.gatewayUrl + path, {
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
  const res = await fetchWithAuth(config.gatewayUrl + path);
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
