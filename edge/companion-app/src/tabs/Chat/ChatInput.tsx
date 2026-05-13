/** 输入框 + 发送按钮 + 停止按钮 + 图片附件
 *
 * 快捷键 / 输入方式:
 *   Enter        发送
 *   Shift+Enter  换行
 *   Cmd/Ctrl+L   清屏（reset 整个对话）
 *
 *   📎 按钮       打开 file picker, 选 1+ 张图
 *   粘贴 (Cmd+V) 自动捕获剪贴板里的图片
 *   拖入文件     直接拖到输入框区域
 *
 * 图片走 base64 进 in-memory attachment, 不写磁盘也不进 state.db (MVP 简化).
 */

import { useEffect, useRef, useState, type KeyboardEvent, type ClipboardEvent, type DragEvent, type ChangeEvent } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { Attachment } from "../../types/chat";
import { useUIStore } from "../../store/ui";  // BL-E13 主动闲聊 prefill
import { useAgentStore } from "../../store/agent";  // BL-E11 后续: 员工自定义名
import { useTeachingStore } from "../../store/teaching";  // BL-LEAN-SESSION (5/13)

interface Props {
  isStreaming: boolean;
  onSend: (text: string, attachments: Attachment[]) => void;
  onCancel: () => void;
  /** BL-COMPANION-UX1 (5/12 鸿波"锁死"修): streaming 中一键 abort + 发新消息 */
  onCancelAndSend: (text: string, attachments: Attachment[]) => void;
  onReset: () => void;
}

const MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024; // 20MB 单文件 (PDF 平均, 图片够)
// BL-VOICE3 (5/10): 音频走 ffmpeg → wav → whisper, 大会议录音常见 30+ MB, 单独放宽到 100MB
const MAX_AUDIO_BYTES = 100 * 1024 * 1024;
const MAX_ATTACHMENTS = 6; // 单条消息最多 6 个附件, 防员工误拖整个文件夹

const SUPPORTED_FILE_EXTS = [".pdf", ".xlsx", ".xls", ".docx", ".csv", ".txt", ".md", ".markdown", ".log"];
// BL-VOICE3 (5/10): 拖音频转文字 — ffmpeg 都吃, whisper.cpp 转中文 (公文 prompt)
const SUPPORTED_AUDIO_EXTS = [".mp3", ".m4a", ".wav", ".aac", ".ogg", ".flac", ".opus", ".wma"];

/** 看文件是图片 / 文档 / 音频. 都不是就 throw. */
function classifyFile(file: File): "image" | "file" | "audio" {
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
async function fileToAttachment(file: File): Promise<Attachment> {
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

export default function ChatInput({
  isStreaming,
  onSend,
  onCancel,
  onCancelAndSend,
  onReset,
}: Props) {
  // BL-E11 后续: placeholder 用员工自定义名 ("跟老李说话…")
  const agentName = useAgentStore((s) => s.name);
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [attachError, setAttachError] = useState<string | null>(null);
  const [isDragOver, setIsDragOver] = useState(false);
  // 5/5 鸿波报"上传 Excel 没反应" 修: 大文件 base64 + Python 解析需要几秒,
  // 之前 UI 0 反馈, 员工以为坏了. 加个 "正在解析..." 状态.
  // BL-VOICE3 (5/10): 音频转录耗时更长 (30 分钟会议录音可能跑 1-2 分钟),
  // 用 parseLabel 区分 "正在解析文件" vs "正在转录音频".
  const [isParsingFile, setIsParsingFile] = useState(false);
  const [parseLabel, setParseLabel] = useState<string>("正在解析文件…");
  // 🎤 语音录音状态 (方案 C+ 五一 sprint Day 1: Whisper.cpp 本地, ffmpeg subprocess 录)
  const [isRecording, setIsRecording] = useState(false);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const [speechHint, setSpeechHint] = useState<string | null>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // 输入框 auto-grow（最多 200px 高度）
  useEffect(() => {
    const el = taRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 200) + "px";
  }, [text]);

  // mount 后聚焦
  useEffect(() => {
    taRef.current?.focus();
  }, []);

  // 主动闲聊 BL-E13: 从 ProactiveCard / 通知点击塞过来的 starter, 预填到输入框.
  // 用 zustand store 跨组件传, mount 后消费一次 (避免组件重渲再填入).
  const consumePrefill = useUIStore((s) => s.consumeChatPrefill);
  const pendingPrefill = useUIStore((s) => s.pendingChatPrefill);
  useEffect(() => {
    if (pendingPrefill) {
      const v = consumePrefill();
      if (v) {
        setText(v);
        // 微延迟聚焦, 让 textarea 渲染好
        setTimeout(() => taRef.current?.focus(), 0);
      }
    }
  }, [pendingPrefill, consumePrefill]);

  // 🎤 录音逻辑 — ffmpeg 子进程录 + Whisper.cpp 转 (方案 C+ Day 1, 全本地)
  // 按下 🎤: invoke speech_start_recording → Rust 启 ffmpeg avfoundation 录 wav
  // 再按 🎤: invoke speech_stop_and_transcribe → kill ffmpeg + whisper-cli → 文字 append
  // (绕过 WKWebView 不支持 navigator.mediaDevices.getUserMedia 的限制)
  async function startRecording() {
    if (isRecording || isTranscribing) return;
    try {
      await invoke("speech_start_recording");
      setIsRecording(true);
      setSpeechHint("正在录音… 再按 🎤 结束");
    } catch (err) {
      console.error("speech_start_recording failed:", err);
      setSpeechHint(`录音启动失败: ${(err as Error).message || err}`);
      setTimeout(() => setSpeechHint(null), 4000);
    }
  }

  async function stopRecording() {
    if (!isRecording) return;
    setIsRecording(false);
    setIsTranscribing(true);
    setSpeechHint("识别中…");
    try {
      const transcript = await invoke<string>("speech_stop_and_transcribe");
      // 把文字 append 到当前 text (不覆盖已写内容)
      setText((cur) => (cur ? cur + " " + transcript : transcript));
      setSpeechHint(null);
      taRef.current?.focus();
    } catch (err) {
      console.error("speech_stop_and_transcribe failed:", err);
      setSpeechHint(`识别失败: ${(err as Error).message || err}`);
      setTimeout(() => setSpeechHint(null), 4000);
    } finally {
      setIsTranscribing(false);
    }
  }

  /** 把一组 File 加进 attachments, 校验失败显示在 attachError */
  async function addFiles(files: FileList | File[]): Promise<void> {
    setAttachError(null);
    const arr = Array.from(files);
    if (attachments.length + arr.length > MAX_ATTACHMENTS) {
      setAttachError(`最多 ${MAX_ATTACHMENTS} 张图, 删几张再加`);
      return;
    }
    setIsParsingFile(true);
    try {
      const next: Attachment[] = [];
      for (const f of arr) {
        // BL-VOICE3 (5/10): 音频转录可能跑 30s+, 用专属 label 安抚员工
        try {
          const isAudio =
            f.type.startsWith("audio/") ||
            SUPPORTED_AUDIO_EXTS.some((ext) => (f.name || "").toLowerCase().endsWith(ext));
          setParseLabel(isAudio ? "🎙 正在转录音频…(可能要几十秒, 取决于音频长度)" : "正在解析文件…");
        } catch {
          setParseLabel("正在解析文件…");
        }
        try {
          next.push(await fileToAttachment(f));
        } catch (e) {
          // 5/5 鸿波报: 之前 throw 在 catch 里 setAttachError 后 return,
          // setIsParsingFile(false) 没在 finally 里 → UI 卡在"解析中".
          // 现在 try-finally 兜住, 必出 finally 关 spinner.
          setAttachError((e as Error).message || String(e));
          return;
        }
      }
      setAttachments((cur) => [...cur, ...next]);
    } finally {
      setIsParsingFile(false);
    }
  }

  function removeAttachment(idx: number): void {
    setAttachments((cur) => cur.filter((_, i) => i !== idx));
    setAttachError(null);
  }

  function submit() {
    const t = text.trim();
    if (!t && attachments.length === 0) return;
    // BL-COMPANION-UX1 (5/12): streaming 中也允许发 — 一键 abort 当前 + 发新.
    if (isStreaming) {
      onCancelAndSend(t, attachments);
    } else {
      onSend(t, attachments);
    }
    setText("");
    setAttachments([]);
    setAttachError(null);
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    // Cmd/Ctrl+L 清屏
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "l") {
      e.preventDefault();
      if (confirm("清空当前对话？")) onReset();
      return;
    }
    // Shift+Enter: 换行（默认行为）
    if (e.key === "Enter" && e.shiftKey) return;
    // Enter: 发送
    if (e.key === "Enter") {
      e.preventDefault();
      submit();
    }
  }

  /** 粘贴: 看剪贴板里有没有图片(截屏后直接 Cmd+V) 或文件(从 Finder 复制).
   *
   * 5/5 鸿波报"文件粘贴不行" 修: 之前只过滤 image/* MIME, 文档 (PDF/Excel/Word)
   * 被忽略. 改成所有 kind="file" 都收, 让 fileToAttachment 自己 classifyFile,
   * 不支持的格式会 throw 显示在 attachError. */
  function onPaste(e: ClipboardEvent<HTMLTextAreaElement>) {
    const items = Array.from(e.clipboardData?.items ?? []);
    const pastedFiles: File[] = [];
    for (const it of items) {
      if (it.kind === "file") {
        const f = it.getAsFile();
        if (f) pastedFiles.push(f);
      }
    }
    if (pastedFiles.length > 0) {
      e.preventDefault(); // 阻止把二进制乱码塞进 textarea
      void addFiles(pastedFiles);
    }
    // 没文件就走默认 (粘贴文本)
  }

  /** 拖放: dragover/drop 在最外层 div 上挂, 防止默认行为 (浏览器会打开图片) */
  function onDragOver(e: DragEvent<HTMLDivElement>) {
    if (e.dataTransfer?.types?.includes("Files")) {
      e.preventDefault();
      setIsDragOver(true);
    }
  }
  function onDragLeave(_e: DragEvent<HTMLDivElement>) {
    setIsDragOver(false);
  }
  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setIsDragOver(false);
    const files = e.dataTransfer?.files;
    if (files && files.length > 0) void addFiles(files);
  }

  /** 点 📎 按钮 → 触发 file input */
  function onPickFile() {
    fileInputRef.current?.click();
  }
  function onFileInputChange(e: ChangeEvent<HTMLInputElement>) {
    const files = e.target.files;
    if (files && files.length > 0) void addFiles(files);
    // 清掉 value, 不然下次选同一张图不触发 onChange
    if (e.target) e.target.value = "";
  }

  // BL-COMPANION-UX1 (5/12): streaming 中也允许"发送" (实际走 cancelAndSend).
  // canSend = 有内容. 是否 streaming 由按钮文案/颜色区分.
  const hasContent = text.trim().length > 0 || attachments.length > 0;
  const canSend = hasContent;

  return (
    <div
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
      style={{
        borderTop: "1px solid var(--catfish-border)",
        background: isDragOver
          ? "var(--catfish-cyan-dim)"
          : "var(--catfish-bg-elevated)",
        padding: "var(--space-3) var(--space-4)",
        transition: "background 120ms ease",
      }}
    >
      {/* 缩略图行 —— 只有 attachments 非空才渲染 */}
      {attachments.length > 0 && (
        <div
          style={{
            display: "flex",
            gap: "var(--space-2)",
            flexWrap: "wrap",
            marginBottom: "var(--space-2)",
          }}
        >
          {attachments.map((att, idx) =>
            att.kind === "image" ? (
              <ThumbCard
                key={idx}
                attachment={att}
                onRemove={() => removeAttachment(idx)}
              />
            ) : (
              <FileChip
                key={idx}
                attachment={att}
                onRemove={() => removeAttachment(idx)}
              />
            )
          )}
        </div>
      )}

      {/* 🎤 语音 hint toast (方案 B 五一 sprint Day 1) */}
      {speechHint && (
        <div
          style={{
            color: "var(--catfish-cyan)",
            fontSize: 12,
            marginBottom: "var(--space-2)",
            background: "var(--catfish-cyan-dim)",
            padding: "var(--space-1) var(--space-2)",
            borderRadius: "var(--radius-sm)",
            display: "inline-block",
          }}
        >
          🎤 {speechHint}
        </div>
      )}

      {/* 错误提示 (附件相关) */}
      {attachError && (
        <div
          style={{
            color: "var(--status-err)",
            fontSize: 12,
            marginBottom: "var(--space-2)",
          }}
        >
          ⚠ {attachError}
        </div>
      )}

      {/* 5/5 文件解析进行中 (Excel / 大 PDF 几秒级, 之前 0 反馈员工以为坏了)
          BL-VOICE3 (5/10): 音频走 whisper, 几十秒级别, label 区分提示 */}
      {isParsingFile && (
        <div
          style={{
            color: "var(--catfish-cyan)",
            fontSize: 12,
            marginBottom: "var(--space-2)",
            background: "var(--catfish-cyan-dim)",
            padding: "var(--space-1) var(--space-2)",
            borderRadius: "var(--radius-sm)",
            display: "inline-block",
          }}
        >
          📎 {parseLabel}
        </div>
      )}

      <div
        style={{
          display: "flex",
          alignItems: "flex-end",
          gap: "var(--space-2)",
        }}
      >
        {/* 隐藏的 file input — 点 📎 按钮触发 (图片 + 文档) */}
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*,.pdf,.xlsx,.xls,.docx,.csv,.txt,.md,.markdown,.log,.mp3,.wav,.m4a,.flac,.aac,.ogg,.mp4,.mov,.m4v,.mkv,.webm"
          multiple
          onChange={onFileInputChange}
          style={{ display: "none" }}
        />

        {/* 📎 附件按钮 */}
        <button
          onClick={onPickFile}
          disabled={isStreaming || attachments.length >= MAX_ATTACHMENTS}
          title="加图片或文档 (PDF/Excel/Word/CSV/TXT) — 也可拖入或截图后 Cmd+V"
          style={{
            padding: "6px 10px",
            border: "1px solid var(--catfish-border)",
            borderRadius: "var(--radius-sm)",
            background: "transparent",
            color: "var(--catfish-text-muted)",
            fontSize: 16,
            cursor:
              isStreaming || attachments.length >= MAX_ATTACHMENTS
                ? "default"
                : "pointer",
            lineHeight: 1,
            minHeight: 36,
          }}
        >
          📎
        </button>

        {/* 🎓 教学模式 toggle (BL-LEAN-SESSION 5/13 鸿波拍板).
            开启时这个 session 走 LEAN: 关 9 个干扰 inject (session_facts /
            stats_guard / skill_guard / journal / feedback / 等), prompt 干净
            适合教 catfish 新 skill (catfish_teach_start 流程). 关闭回常态.
            跨 session 隔离, 不重启 gateway. */}
        <TeachingToggleButton isStreaming={isStreaming} />

        {/* 🎤 语音输入按钮 — 方案 C 五一 sprint Day 1: Whisper.cpp 本地
           按一下开始录音, 再按一下停止 → 自动转文字填到 textarea. 数据 100% 本地. */}
        <button
          onClick={isRecording ? stopRecording : startRecording}
          disabled={isStreaming || isTranscribing}
          title={
            isRecording
              ? "再按一下结束录音"
              : isTranscribing
              ? "识别中…"
              : "语音输入 (Whisper.cpp 本地, 不上传)"
          }
          style={{
            padding: "6px 10px",
            border: "1px solid",
            borderColor: isRecording ? "var(--status-err)" : "var(--catfish-border)",
            borderRadius: "var(--radius-sm)",
            background: isRecording ? "var(--status-err)" : "transparent",
            color: isRecording ? "white" : "var(--catfish-text-muted)",
            fontSize: 16,
            cursor: isStreaming || isTranscribing ? "default" : "pointer",
            lineHeight: 1,
            minHeight: 36,
            // 录音中: 心跳呼吸效果
            animation: isRecording ? "catfish-pulse 1.2s ease-in-out infinite" : undefined,
          }}
        >
          {isTranscribing ? "⏳" : "🎤"}
        </button>

        <textarea
          ref={taRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKey}
          onPaste={onPaste}
          placeholder={
            attachments.length > 0
              ? "加点说明 (可空) — Enter 发送"
              : `跟${agentName}说话…  (Enter 发送 · 拖入文件 / 截图 Cmd+V / 点 📎 加附件)`
          }
          rows={1}
          style={{
            flex: 1,
            resize: "none",
            border: "1px solid var(--catfish-border)",
            borderRadius: "var(--radius-md)",
            padding: "var(--space-2) var(--space-3)",
            fontSize: 14,
            lineHeight: 1.5,
            fontFamily: "inherit",
            color: "var(--catfish-text)",
            background: "var(--catfish-bg)",
            outline: "none",
            minHeight: 36,
          }}
          disabled={false /* 仍允许写下一个，发送按钮在 streaming 时变停止 */}
        />
        {/* BL-COMPANION-UX1 (5/12 鸿波"锁死"修): 三态按钮.
            - 非 streaming + 有内容       → "发送" (青)
            - streaming + 有内容          → "⏹ 停下接着发" (青色一键 abort+发)
            - streaming + 没内容          → "停止" (橙色, 单纯 abort) */}
        {isStreaming && hasContent ? (
          <button
            onClick={submit}
            title="停止当前流, 立刻发送新消息 (Enter 同效)"
            style={{
              padding: "var(--space-2) var(--space-3)",
              border: "none",
              borderRadius: "var(--radius-sm)",
              background: "var(--catfish-cyan)",
              color: "white",
              fontSize: 13,
              fontWeight: 500,
              minWidth: 100,
              cursor: "pointer",
            }}
          >
            ⏹ 停下接着发
          </button>
        ) : isStreaming ? (
          <button
            onClick={onCancel}
            title="停止当前流"
            style={{
              padding: "var(--space-2) var(--space-3)",
              border: "1px solid var(--status-warn)",
              borderRadius: "var(--radius-sm)",
              background: "transparent",
              color: "var(--status-warn)",
              fontSize: 13,
              fontWeight: 500,
              minWidth: 60,
            }}
          >
            停止
          </button>
        ) : (
          <button
            onClick={submit}
            disabled={!canSend}
            style={{
              padding: "var(--space-2) var(--space-4)",
              border: "none",
              borderRadius: "var(--radius-sm)",
              background: canSend
                ? "var(--catfish-cyan)"
                : "var(--catfish-border)",
              color: "white",
              fontSize: 13,
              fontWeight: 500,
              minWidth: 60,
              cursor: canSend ? "pointer" : "default",
            }}
          >
            发送
          </button>
        )}
      </div>
    </div>
  );
}

/** 文件类型 → emoji 图标 */
function fileEmoji(name: string): string {
  const lower = name.toLowerCase();
  if (lower.endsWith(".pdf")) return "📕";
  if (lower.endsWith(".xlsx") || lower.endsWith(".xls") || lower.endsWith(".csv")) return "📊";
  if (lower.endsWith(".docx")) return "📝";
  if (lower.endsWith(".md") || lower.endsWith(".markdown")) return "📋";
  return "📄";
}

/** 把 meta 转成简短显示文案. excel: "5 sheets · 365 行"; pdf: "12 页"; etc */
function metaSummary(att: Attachment): string {
  const sizeKb = Math.max(1, Math.round(att.sizeBytes / 1024));
  const sizeLabel = sizeKb >= 1024
    ? `${(sizeKb / 1024).toFixed(1)} MB`
    : `${sizeKb} KB`;
  const m = att.meta || {};
  const kind = att.fileKind;

  if (kind === "excel") {
    const sheets = (m.sheets as string[] | undefined) || [];
    const counts = (m.row_counts as Record<string, number> | undefined) || {};
    const totalRows = Object.values(counts).reduce((a, b) => a + b, 0);
    return `${sheets.length} sheet · ${totalRows} 行 · ${sizeLabel}`;
  }
  if (kind === "pdf") {
    return `${m.page_count ?? "?"} 页 · ${sizeLabel}`;
  }
  if (kind === "word") {
    const tables = m.table_count ?? 0;
    const tableLabel = tables ? ` · ${tables} 表格` : "";
    return `${m.paragraph_count ?? "?"} 段${tableLabel} · ${sizeLabel}`;
  }
  if (kind === "csv") {
    return `${m.total_rows ?? "?"} 行 · ${sizeLabel}`;
  }
  if (kind === "text") {
    return `${m.total_chars ?? "?"} 字 · ${sizeLabel}`;
  }
  // BL-VOICE3 (5/10): 音频转录后显示时长 + 转录字数 (字段名跟 chat.ts 对齐)
  if (kind === "audio") {
    const sec = m.duration_sec as number | null | undefined;
    const chars = m.transcript_chars as number | undefined;
    const durLabel = sec
      ? sec >= 60
        ? `${Math.floor(sec / 60)}分${Math.round(sec % 60)}秒`
        : `${Math.round(sec)}秒`
      : "?";
    return `🎵 ${durLabel} · 转录 ${chars ?? "?"} 字 · ${sizeLabel}`;
  }
  return sizeLabel;
}

function FileChip({
  attachment,
  onRemove,
}: {
  attachment: Attachment;
  onRemove: () => void;
}) {
  const summary = metaSummary(attachment);
  return (
    <div
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "var(--space-1)",
        padding: "6px 10px",
        background: "var(--catfish-bg)",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-md)",
        fontSize: 12,
        maxWidth: 320,
      }}
      title={`${attachment.name}\n${summary}\n完整文件: ${attachment.keptPath || "(未保留)"}`}
    >
      <span style={{ fontSize: 16 }}>{fileEmoji(attachment.name)}</span>
      <span
        style={{
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          maxWidth: 180,
        }}
      >
        {attachment.name}
      </span>
      <span style={{ color: "var(--catfish-text-muted)", fontSize: 11 }}>
        {summary}
      </span>
      <button
        onClick={onRemove}
        style={{
          width: 18,
          height: 18,
          borderRadius: "50%",
          border: "none",
          background: "rgba(0,0,0,0.4)",
          color: "white",
          fontSize: 11,
          cursor: "pointer",
          padding: 0,
          marginLeft: "var(--space-1)",
          lineHeight: 1,
        }}
        title="删除"
      >
        ×
      </button>
    </div>
  );
}

function ThumbCard({
  attachment,
  onRemove,
}: {
  attachment: Attachment;
  onRemove: () => void;
}) {
  const dataUri = `data:${attachment.mimeType};base64,${attachment.base64}`;
  const sizeKb = Math.max(1, Math.round(attachment.sizeBytes / 1024));
  return (
    <div
      style={{
        position: "relative",
        width: 64,
        height: 64,
        borderRadius: "var(--radius-sm)",
        overflow: "hidden",
        border: "1px solid var(--catfish-border)",
        background: "var(--catfish-bg)",
      }}
      title={`${attachment.name} · ${sizeKb} KB`}
    >
      <img
        src={dataUri}
        alt={attachment.name}
        style={{
          width: "100%",
          height: "100%",
          objectFit: "cover",
          display: "block",
        }}
      />
      <button
        onClick={onRemove}
        style={{
          position: "absolute",
          top: 2,
          right: 2,
          width: 18,
          height: 18,
          borderRadius: "50%",
          border: "none",
          background: "rgba(0,0,0,0.6)",
          color: "white",
          fontSize: 11,
          fontWeight: 700,
          cursor: "pointer",
          lineHeight: 1,
          padding: 0,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
        title="删除"
      >
        ×
      </button>
    </div>
  );
}


// ── BL-LEAN-SESSION (5/13 鸿波拍板 "客户无法跑命令行") ────────────
//
// 教学模式 toggle 按钮. 开启 → 这个 session 的 LLM 请求带
// X-Catfish-Teaching-Mode: 1 header → gateway 走 LEAN (关 9 个 inject + L8
// feedback retry, mid_task retry 不影响). 关闭 → 完整注入回常态.
// 状态走 zustand store + localStorage 持久化, 跨 session 隔离.

function TeachingToggleButton({ isStreaming }: { isStreaming: boolean }) {
  const on = useTeachingStore((s) => s.on);
  const toggle = useTeachingStore((s) => s.toggle);
  return (
    <button
      onClick={toggle}
      disabled={isStreaming}
      title={
        on
          ? "🎓 教学模式 ON: 走 LEAN (关 9 个干扰 inject), 适合教新 skill. 点关闭回常态."
          : "🎓 开启教学模式: 关掉 9 个干扰 inject 让 prompt 干净, 适合教 catfish 跑新流程 (catfish_teach_start). 不影响 mid-task retry."
      }
      style={{
        padding: "6px 10px",
        border: "1px solid " + (on ? "var(--catfish-cyan)" : "var(--catfish-border)"),
        borderRadius: "var(--radius-sm)",
        background: on ? "var(--catfish-cyan-dim)" : "transparent",
        color: on ? "var(--catfish-cyan)" : "var(--catfish-text-muted)",
        fontSize: 14,
        fontWeight: on ? 600 : 400,
        cursor: isStreaming ? "default" : "pointer",
        lineHeight: 1,
        minHeight: 36,
        transition: "all 120ms ease",
      }}
    >
      🎓
    </button>
  );
}
