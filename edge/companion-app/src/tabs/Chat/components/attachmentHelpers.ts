/** ChatInput attachment 处理 — 抽自 ChatInput.tsx (5/20 拆分).
 *
 * classifyFile / fileToAttachment + 大小限制 + ext 白名单 (image/file/audio).
 * 音频走 transcribe_audio_from_staged → 本机转写 (会议组件包, FunASR); 文档走 parse_file_from_staged。
 * 10/14: 上限统一 200MB, 文件都先分块传到本机临时文件 (lib/uploadStage.ts), 不再整份 base64 过 IPC。
 */

import { invoke } from "@tauri-apps/api/core";
import { currentEmailPlatform, type EmailPlatform } from "../../../lib/emailPlatformHints";
import { isMacPlatform } from "../../../lib/platformLabels";

import { IMAGE_WIRE_MAX_BYTES, shrinkImage } from "../../../lib/imageShrink";
import { blobToBase64, MAX_UPLOAD_BYTES, stageFile, tooLargeMessage } from "../../../lib/uploadStage";

import type { Attachment } from "../../../types/chat";

// 10/14: 文档 / 图片 / 音频统一 200MB (原来文档图片 20MB、音频 100MB)
export const MAX_ATTACHMENT_BYTES = MAX_UPLOAD_BYTES;
// 10/2: 6 → 30。一叠发票 (十几二十张 PDF / 照片) 原来要分好几条消息发。文档多了由
// chatWire.formatManyFiles 压短预览, 大照片由 shrinkImage 缩到 2000px, 一条消息不会爆。
export const MAX_ATTACHMENTS = 30;
// 图片缩小搬到 lib/imageShrink.ts (10/14: 会话恢复时也要缩, lib 不该反过来 import tabs)
export {
  IMAGE_MAX_EDGE,
  IMAGE_SHRINK_ABOVE_BYTES,
  IMAGE_WIRE_MAX_BYTES,
  shrinkImage,
  shrinkTarget,
} from "../../../lib/imageShrink";

// P3.3.21 (6/11): 加 .xlsm (macro 启用 xlsx, openpyxl 直接吃) / .pptx (python-pptx)
//   / .json (内置嗅 dict / list). 老 .ppt / .doc / .rtf 在 parse_file.py 给友好
//   "另存为新格式" 错, 不进白名单 (避免员工选了 picker 才知道不行).
export const SUPPORTED_FILE_EXTS = [
  ".pdf",
  ".xlsx", ".xlsm", ".xls",
  ".docx",
  ".pptx",
  ".csv",
  ".json",
  ".txt", ".md", ".markdown", ".log",
];
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
/** 加附件失败时给员工看的那句话 (10/25)。
 *
 *  浏览器读不到文件内容时报 NotReadableError「The I/O read operation failed.」—— 员工看不懂。
 *  10/25 鸿波撞到的: 从微信直接拖进来的 PDF 放在微信自己的沙盒 (~/Library/Containers/com.tencent.xinWeChat/…),
 *  macOS 的 App 数据保护不让 Companion 读 (界面和后台都读不到, 也不弹授权框); 给 Companion 开了
 *  「完全磁盘访问权限」后就能读。所以 mac 上直接告诉员工这两个办法; 别的系统多半是文件被占用或已删除。 */
export function attachErrorText(e: unknown, platform: EmailPlatform = currentEmailPlatform()): string {
  const name = (e as { name?: string } | null)?.name;
  if (name === "NotReadableError") {
    return isMacPlatform(platform)
      ? "读不到文件内容 —— 多半是从微信等 App 里直接拖进来的, macOS 不让 Companion 读别的 App 里的文件。" +
          "先在那个 App 里「另存为」到下载文件夹再拖; 或者在 系统设置 › 隐私与安全性 › 完全磁盘访问权限 里打开 Catfish Companion, 重启 Companion 后再拖"
      : "读不到文件内容 —— 文件可能正被别的程序占用, 或已被移走 / 删除。先另存到本地文件夹再拖";
  }
  return (e as Error)?.message || String(e);
}

export async function fileToAttachment(file: File): Promise<Attachment> {
  const kind = classifyFile(file);

  // 大小限制: 10/14 统一 200MB
  if (file.size > MAX_ATTACHMENT_BYTES) throw new Error(tooLargeMessage(file.size));

  // BL-VOICE3 (5/10): 音频路径
  if (kind === "audio") {
    const uploadId = await stageFile(file);
    const result = await invoke<{
      text: string;
      duration_sec: number | null;
      original_filename: string;
    }>("transcribe_audio_from_staged", {
      uploadId,
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
        model: "FunASR 本机转写",
      },
    };
  }

  if (kind === "image") {
    // 10/2: 发进对话的是缩过的 (一次几十张发票照片也不会让请求几百 MB);
    // 落盘的 keptPath 仍是原图, 模型要看清某张细节可以用工具读原图。
    // 10/14: 缩不了又超过 20MB 的 (比如超大 svg) 不往对话里塞, 请员工转格式。
    const small = await shrinkImage(file);
    if (!small && file.size > IMAGE_WIRE_MAX_BYTES) {
      throw new Error(`图片 ${(file.size / 1024 / 1024).toFixed(1)}MB 太大又没法缩小, 请转成 JPG / PNG 再发`);
    }
    const base64 = small ? "" : await blobToBase64(file);

    // P3.5.8 BL-FILE-SESSION-INDEX-V1 Phase 2 (6/16 鸿波): 落盘到
    // ~/.catfish/uploads/, 拿 keptPath. 让 attachment_record 把 keptPath 也存到
    // attachments.db, resume 时 attachment_load_base64 读回 base64 还原 attachments.
    // 旧路径: image 只 in-memory base64 → 切走 session / 重启 Companion 后
    // wire 里失踪, 小鲶看不到图编借口. 同步 await — chat send 前要这 keptPath.
    // 失败软退化: keptPath=undefined, 当轮 send 仍带 in-memory base64 (UI 不挂),
    // 但下次 resume 这张图就丢. console.warn 让员工 / 开发者知道.
    let keptPath: string | undefined;
    try {
      const uploadId = await stageFile(file);
      const r = await invoke<{ keptPath: string; sizeBytes: number }>(
        "attachment_save_image_staged",
        { uploadId, filename: file.name || "pasted-image.png" },
      );
      keptPath = r.keptPath;
    } catch (e) {
      console.warn(
        "[BL-FILE-SESSION-INDEX-V1 Phase 2] attachment_save_image 失败 — " +
          "本轮 image 仍能发, 但 resume 后会丢:",
        e,
      );
    }

    return {
      kind: "image",
      mimeType: small?.mimeType ?? file.type,
      name: file.name || "pasted-image.png",
      base64: small?.base64 ?? base64,
      sizeBytes: file.size,
      keptPath,
    };
  }

  // 文档: 分块传到临时文件 → Rust 'parse_file_from_staged' 解析 + 原文件搬进 ~/.catfish/uploads/
  // (10/14 之前是整份 base64 一次 invoke, 上限 20MB; 放到 200MB 后那样会把内存冲爆)
  const uploadId = await stageFile(file);

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
  }>("parse_file_from_staged", {
    uploadId,
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
 *   - audio / video: previewText 是本机转写文字
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

