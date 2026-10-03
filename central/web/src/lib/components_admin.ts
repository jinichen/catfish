/** 管理员上传 / 下架组件包 (10/3) —— 后端 llm-gateway components_admin.py。
 *
 * 一个包两个多 G: 分 8MB 一块 PUT (web nginx client_max_body_size 20M), 每块带 sha256
 * (浏览器给算的话; 纯 HTTP 访问非 localhost 时 crypto.subtle 不可用, 就不带, 服务端照收)。
 * 断了重选同一个文件, 服务端告诉从哪接着传。
 */
import { api, HttpError } from "./api";
import { getIdToken, login } from "./auth";

export const CHUNK_BYTES = 8 * 1024 * 1024;
/** 跟 build_component_manifest.py / components_admin.py 同一个规则 */
export const PLATFORMS = ["mac-arm64", "mac-x64", "windows-x64", "any"] as const;
const NAME_RE = new RegExp(
  `^([a-z0-9]+(?:-[a-z0-9]+)*?)-(\\d+\\.\\d+\\.\\d+)-(${PLATFORMS.join("|")})\\.tar\\.gz$`,
);

export interface ComponentEntry {
  name: string;
  version: string;
  platform: string;
  file: string;
  size: number;
  sha256: string;
}

export interface Manifest {
  schema: number;
  generated_at: string;
  components: ComponentEntry[];
}

export interface PendingUpload {
  file: string;
  size: number;
  received: number;
}

export function parseComponentFilename(file: string): { name: string; version: string; platform: string } | null {
  const m = NAME_RE.exec(file);
  return m ? { name: m[1], version: m[2], platform: m[3] } : null;
}

export const listComponentsAdmin = () =>
  api.get<{ dir: string; manifest: Manifest; uploads: PendingUpload[] }>("/api/admin/components");

export const startUpload = (file: string, size: number, overwrite: boolean) =>
  api.post<PendingUpload>("/api/admin/components/uploads", { file, size, overwrite });

export const completeUpload = (file: string) =>
  api.post<{ published: ComponentEntry | null; manifest: Manifest }>(
    `/api/admin/components/uploads/${encodeURIComponent(file)}/complete`,
  );

export const cancelUpload = (file: string) =>
  api.delete<{ ok: boolean }>(`/api/admin/components/uploads/${encodeURIComponent(file)}`);

export const deleteComponent = (file: string) =>
  api.delete<{ manifest: Manifest }>(`/api/admin/components/${encodeURIComponent(file)}`);

async function sha256Hex(buf: ArrayBuffer): Promise<string | null> {
  if (!globalThis.crypto?.subtle) return null;
  const d = await crypto.subtle.digest("SHA-256", buf);
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** 传一块; 返回服务端已收到的总字节数 (409 = offset 对不上时也返回服务端的真实进度, 调用方从那接着传) */
export async function putChunk(file: string, offset: number, chunk: Blob): Promise<number> {
  const buf = await chunk.arrayBuffer();
  const headers: Record<string, string> = { "Content-Type": "application/octet-stream" };
  const token = await getIdToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const sha = await sha256Hex(buf);
  if (sha) headers["X-Chunk-Sha256"] = sha;
  const resp = await fetch(`/api/admin/components/uploads/${encodeURIComponent(file)}?offset=${offset}`, {
    method: "PUT",
    headers,
    body: buf,
  });
  if (resp.status === 401) {
    await login();
    throw new HttpError(401, "登录过期");
  }
  const text = await resp.text();
  if (resp.status === 409) {
    try {
      const received = JSON.parse(text)?.detail?.received;
      if (typeof received === "number") return received;
    } catch {
      // 落到下面按错误处理
    }
  }
  if (!resp.ok) throw new HttpError(resp.status, text);
  return (JSON.parse(text) as { received: number }).received;
}

/** 从 start 一块块传到底。网络抖一下 (fetch 抛错 / 5xx) 退避重试 5 次; shouldStop() 为真就停 (暂停)。 */
export async function uploadFrom(
  file: File,
  start: number,
  onProgress: (received: number) => void,
  shouldStop: () => boolean,
): Promise<"done" | "stopped"> {
  let offset = start;
  let failures = 0;
  while (offset < file.size) {
    if (shouldStop()) return "stopped";
    try {
      offset = await putChunk(file.name, offset, file.slice(offset, offset + CHUNK_BYTES));
      failures = 0;
      onProgress(offset);
    } catch (e) {
      const status = e instanceof HttpError ? e.status : 0;
      // 断网 (0) / 服务端 5xx / 这一块传坏了 (422, sha256 对不上) → 重试; 别的 (403 等) 直接报
      const retryable = status === 0 || status >= 500 || status === 422;
      if (!retryable || ++failures > 5) throw e;
      await new Promise((r) => setTimeout(r, 2000 * failures));
    }
  }
  return "done";
}
