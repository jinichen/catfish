/** 文件分块传给本机后台 (10/14 上传上限统一 200MB, 见 src-tauri/src/commands/upload_stage.rs)。
 *
 * 原来每个上传口子都把整个文件转成一个 base64 串 (或数字数组) 一次 invoke 过去。
 * 200MB 的文件那样要占七八百 MB 内存, 低配 Windows 上 webview 会崩。现在 4MB 一块
 * 依次写进临时文件 (base64 后约 5.4MB, 低于 startupDiagnostics 的 8MB 超大载荷告警),
 * 拿回一个 uploadId, 再交给具体用途的命令 (解析文档 / 转写音频 / 存图片 / 模版 / 技能 zip)。
 */
import { invoke } from "@tauri-apps/api/core";

/** 所有上传口子统一的上限 (10/14 鸿波「就是把现在的上限都调整到200M」), 跟 Rust 那边一致 */
export const MAX_UPLOAD_BYTES = 200 * 1024 * 1024;
export const UPLOAD_CHUNK_BYTES = 4 * 1024 * 1024;

export function tooLargeMessage(size: number): string {
  return `文件太大 (${(size / 1024 / 1024).toFixed(1)}MB > ${MAX_UPLOAD_BYTES / 1024 / 1024}MB)`;
}

export function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const uri = reader.result as string;
      const comma = uri.indexOf(",");
      resolve(comma >= 0 ? uri.slice(comma + 1) : uri);
    };
    reader.onerror = () => reject(reader.error || new Error("FileReader 失败"));
    reader.readAsDataURL(blob);
  });
}

function newUploadId(): string {
  const c = globalThis.crypto;
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  return `u${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`;
}

/** 把文件分块写到本机临时文件, 返回 uploadId。超过 200MB 直接拒。 */
export async function stageFile(file: Blob): Promise<string> {
  if (file.size > MAX_UPLOAD_BYTES) throw new Error(tooLargeMessage(file.size));
  const uploadId = newUploadId();
  try {
    for (let offset = 0; offset < file.size; offset += UPLOAD_CHUNK_BYTES) {
      const chunkB64 = await blobToBase64(file.slice(offset, offset + UPLOAD_CHUNK_BYTES));
      await invoke<number>("upload_stage_chunk", { uploadId, offset, chunkB64 });
    }
  } catch (e) {
    invoke("upload_stage_discard", { uploadId }).catch(() => undefined);
    throw e;
  }
  return uploadId;
}
