/** ChatInput attachment 处理 — 抽自 ChatInput.tsx (5/20 拆分).
 *
 * classifyFile / fileToAttachment + 大小限制 + ext 白名单 (image/file/audio).
 * 音频走 transcribe_audio_from_b64 → whisper.cpp; 文档走 parse_file_from_b64.
 */

import { invoke } from "@tauri-apps/api/core";

import type { Attachment } from "../../../types/chat";

export const MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024; // 20MB
// BL-VOICE3 (5/10): 音频走 whisper, 大会议录音常见 30+ MB, 100MB
export const MAX_AUDIO_BYTES = 100 * 1024 * 1024;
export const MAX_ATTACHMENTS = 6;

export const SUPPORTED_FILE_EXTS = [".pdf", ".xlsx", ".xls", ".docx", ".csv", ".txt", ".md", ".markdown", ".log"];
export const SUPPORTED_AUDIO_EXTS = [".mp3", ".m4a", ".wav", ".aac", ".ogg", ".flac", ".opus", ".wma"];

/** 看文件是图片 / 文档 / 音频. 都不是就 throw. */
export function classifyFile(file: File): "image" | "file" | "audio" {
  if (file.type.startsWith("image/")) return "image";
  const lowerName = (file.name || "").toLowerCase();
  // BL-VOICE3: audio MIME 优先 ext 名 (Tauri 拖入有时 type='', 单靠 ext)
  if (
    file.type.startsWith("audio/") ||
    SUPPORTED_AUDIO_EXTS.some((ext) => lowerName.endsWith(ext))
  ) {
    return "audio";
  }
  if (SUPPORTED_FILE_EXTS.some((ext) => lowerName.endsWith(ext))) {
    return "file";
  }
  throw new Error(
    `不支持: ${file.type || file.name}. 支持: 图片 / PDF / Excel / Word / CSV / TXT / MD / 音频 (mp3/m4a/wav/...)`
  );
}

/** File → Attachment. 图片走 base64; 文档走 Tauri parse_file → text;
 *  音频走 transcribe_audio_from_b64 → 转录文字当 previewText (BL-VOICE3 5/10). */
export async function fileToAttachment(file: File): Promise<Attachment> {
  const kind = classifyFile(file);

  // 大小限制: 音频放宽到 100MB (会议录音常见 30+ MB), 其他 20MB
  const maxBytes = kind === "audio" ? MAX_AUDIO_BYTES : MAX_ATTACHMENT_BYTES;
  if (file.size > maxBytes) {
    const mb = (file.size / 1024 / 1024).toFixed(1);
    const limitMb = (maxBytes / 1024 / 1024).toFixed(0);
    throw new Error(`文件太大 (${mb}MB > ${limitMb}MB)`);
  }

  // BL-VOICE3 (5/10): 音频路径
  if (kind === "audio") {
    // 读 base64 (跟 file 分支同模板, FileReader.readAsDataURL 异步, V8 优化)
    const fileB64 = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        const dataUri = reader.result as string;
        const comma = dataUri.indexOf(",");
        resolve(comma >= 0 ? dataUri.slice(comma + 1) : dataUri);
      };
      reader.onerror = () => reject(reader.error || new Error("FileReader 失败"));
      reader.readAsDataURL(file);
    });

    const result = await invoke<{
      text: string;
      duration_sec: number | null;
      original_filename: string;
    }>("transcribe_audio_from_b64", {
      fileB64,
      filename: file.name || "audio",
    });

    // 把转录文字塞进 previewText, LLM 像读 PDF 一样直接拿到全文.
    // BL-VOICE3 (5/10): meta 字段对齐 chat.ts formatFileAttachment 已有 audio 分支
    // (transcript_chars / model — 5/8 BL-I4 留的预留接口, 现在真接通).
    return {
      kind: "file",
      mimeType: file.type || "audio/mpeg",
      name: file.name || "audio",
      sizeBytes: file.size,
      fileKind: "audio",
      previewText: result.text,
      meta: {
        duration_sec: result.duration_sec,
        transcript_chars: result.text.length,
        model: "whisper.cpp",
      },
    };
  }

  if (kind === "image") {
    // 图片: FileReader → base64 (gateway 拼 data URI 给 vision 模型)
    const dataUri = await new Promise<string>((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => resolve(r.result as string);
      r.onerror = () => reject(r.error);
      r.readAsDataURL(file);
    });
    const comma = dataUri.indexOf(",");
    const base64 = comma >= 0 ? dataUri.slice(comma + 1) : dataUri;
    return {
      kind: "image",
      mimeType: file.type,
      name: file.name || "pasted-image.png",
      base64,
      sizeBytes: file.size,
    };
  }

  // 文档: base64 → Rust command 'parse_file_from_b64' 内部写 tmp + 解析 + 删 tmp
  // 5/5 鸿波报"上传 Excel 没反应" 修: 之前用 String.fromCharCode + 循环 + btoa,
  // 几 MB 文件会卡死主线程 30+ 秒, UI 看起来"没反应". 改用 FileReader.readAsDataURL
  // (浏览器原生异步, V8 优化), 抠 data URI 后面的 base64 部分.
  const fileB64 = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const dataUri = reader.result as string;
      // dataUri = "data:<mime>;base64,<b64-content>"
      const comma = dataUri.indexOf(",");
      resolve(comma >= 0 ? dataUri.slice(comma + 1) : dataUri);
    };
    reader.onerror = () => reject(reader.error || new Error("FileReader 失败"));
    reader.readAsDataURL(file);
  });

  // 5/5 重构: parse_file 改 preview-only, 返 preview_text + kept_path + meta
  // 5/7 BL-L26: 大文件 (≥50KB 全文) 多返一个 parsed_text_path (BM25 sidecar)
  const result = await invoke<{
    filename: string;
    ext: string;
    kind: string;
    preview_text: string;
    preview_chars: number;
    meta: Record<string, unknown>;
    kept_path: string;
    parsed_text_path?: string;
  }>("parse_file_from_b64", {
    fileB64,
    filename: file.name || "upload",
  });

  return {
    kind: "file",
    mimeType: file.type || "application/octet-stream",
    name: result.filename,
    sizeBytes: file.size,
    fileKind: result.kind,
    previewText: result.preview_text,
    meta: result.meta,
    keptPath: result.kept_path,
    parsedTextPath: result.parsed_text_path,
  };
}

/** P16 (6/5 鸿波): fire-and-forget ingest attachment 全文 → ~/.catfish/wiki/raw/sources/.
 *
 *  调用时机: ChatInput.submit() 在 onSend 之后. 不 await, 不 throw, 失败静默.
 *  catfish-memory plugin 后台 sync_turn 3b 扫 sources dir merge 进 Analysis input.
 *
 *  跳过的 kind:
 *   - image: 走 vision, 没文本可 ingest
 *
 *  入的 kind:
 *   - pdf / word / text: 全文 (大文件走 sidecar)
 *   - excel / csv: preview = Sheet 名 + 列头 + 前 N 行 markdown, 够 LLM 抽 entity
 *     (6/5 鸿波实测: xlsx 入库失败 → 改成入. LLM 也会另调 execute_code 取细节.)
 *   - audio / video: previewText 是 whisper 转录文字
 */
export function ingestAttachmentSourceFireForget(att: {
  kind: "image" | "file";
  fileKind?: string;
  name?: string;
  previewText?: string;
  keptPath?: string;
  parsedTextPath?: string;
}): void {
  if (att.kind !== "file") return;
  const fk = (att.fileKind || "").toLowerCase();
  // preview 空且没 sidecar → 没东西可 ingest
  if (!att.previewText && !att.parsedTextPath) return;

  invoke<{ rel_path: string; bytes: number; full_text_chars: number }>(
    "wiki_ingest_source",
    {
      keptPath: att.keptPath || "",
      parsedTextPath: att.parsedTextPath,
      previewText: att.previewText || "",
      filename: att.name || "upload",
      kind: fk || "text",
    },
  )
    .then((r) => {
      // 静默成功 (console 调试用, 不弹 toast 防干扰输入)
      console.info(
        `[P16] wiki ingest ✓ ${r.rel_path} (${r.full_text_chars} chars)`,
      );
    })
    .catch((e) => {
      console.warn("[P16] wiki_ingest_source 失败 (静默):", e);
    });
}

