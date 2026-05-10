/** Skills Hub API 客户端 (BL-ARCH1 5/10).
 *
 * 全部走 gateway /v1/hub/* 反代, 不直连 hub :8997.
 */

import { api } from "./api";

export interface SkillSummary {
  namespace: string;
  name: string;
  latest_version: string;
  all_versions?: string[];
  description?: string;
  deprecated?: boolean;
  published_by?: string;
  published_at?: string;
  file_count?: number;
  total_bytes?: number;
  subscribe_count?: number;
  rating_avg?: number | null;
  rating_count?: number;
}

export interface SkillsListResponse {
  skills: SkillSummary[];
  count: number;
}

export interface SkillDetail {
  namespace: string;
  name: string;
  version: string;
  description: string;
  deprecated: boolean;
  files: string[];
  files_sha256: Record<string, string>;
  skill_md: string;
}

export async function listSkills(namespace?: string): Promise<SkillsListResponse> {
  const q = namespace ? `?namespace=${encodeURIComponent(namespace)}` : "";
  return api.get<SkillsListResponse>(`/v1/hub/skills${q}`);
}

export async function getSkill(
  namespace: string,
  name: string,
  version: string = "latest",
): Promise<SkillDetail> {
  if (version === "latest") {
    return api.get<SkillDetail>(`/v1/hub/skills/${namespace}/${name}`);
  }
  return api.get<SkillDetail>(`/v1/hub/skills/${namespace}/${name}/${version}`);
}

/** 下载 skill 文件 (二进制返 ArrayBuffer). 当前 page 不直接装, 留 Companion 做. */
export async function downloadSkillFile(
  namespace: string,
  name: string,
  version: string,
  filePath: string,
): Promise<string> {
  return api.get<string>(
    `/v1/hub/skills/${namespace}/${name}/${version}/files/${filePath}`,
  );
}

/** Publish skill — multipart 多文件上传. */
export async function publishSkill(
  namespace: string,
  files: File[],
): Promise<{ ok: boolean; name?: string; version?: string; error?: string }> {
  const fd = new FormData();
  for (const f of files) {
    fd.append("files", f, f.name);
  }
  try {
    const r = await api.post<{ ok: boolean; name: string; version: string }>(
      `/v1/hub/skills/${encodeURIComponent(namespace)}`,
      fd,
    );
    return r;
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}

/** Admin 删 skill 版本. */
export async function deleteSkillVersion(
  namespace: string,
  name: string,
  version: string,
): Promise<{ ok: boolean; error?: string }> {
  try {
    return await api.delete<{ ok: boolean }>(
      `/v1/hub/skills/${namespace}/${name}/${version}`,
    );
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}

export interface AuditEvent {
  ts: string;
  event: string;
  namespace?: string;
  name?: string;
  version?: string;
  by_user?: string;
  [k: string]: unknown;
}

export async function fetchAudit(limit: number = 100): Promise<AuditEvent[]> {
  const r = await api.get<{ events: AuditEvent[] }>(
    `/v1/hub/audit?limit=${limit}`,
  );
  return r.events || [];
}
