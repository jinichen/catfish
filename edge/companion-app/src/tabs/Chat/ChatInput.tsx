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
import type { Attachment } from "../../types/chat";

interface Props {
  isStreaming: boolean;
  onSend: (text: string, attachments: Attachment[]) => void;
  onCancel: () => void;
  onReset: () => void;
}

const MAX_ATTACHMENT_BYTES = 12 * 1024 * 1024; // 12MB 单张 — 跟 catfish_screenshot 的 _MAX_SCREENSHOT_BYTES 对齐
const MAX_ATTACHMENTS = 6; // 单条消息最多 6 张图, 防员工误拖整个文件夹

/** File → Attachment, 失败 throw */
async function fileToAttachment(file: File): Promise<Attachment> {
  if (!file.type.startsWith("image/")) {
    throw new Error(`不支持的文件类型: ${file.type || "未知"} (目前只支持图片)`);
  }
  if (file.size > MAX_ATTACHMENT_BYTES) {
    const mb = (file.size / 1024 / 1024).toFixed(1);
    throw new Error(`图片太大 (${mb}MB > 12MB), 压一下再传`);
  }
  // FileReader → base64 (data URI 头要剥掉, gateway 那边拼)
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

export default function ChatInput({
  isStreaming,
  onSend,
  onCancel,
  onReset,
}: Props) {
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [attachError, setAttachError] = useState<string | null>(null);
  const [isDragOver, setIsDragOver] = useState(false);
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

  /** 把一组 File 加进 attachments, 校验失败显示在 attachError */
  async function addFiles(files: FileList | File[]): Promise<void> {
    setAttachError(null);
    const arr = Array.from(files);
    if (attachments.length + arr.length > MAX_ATTACHMENTS) {
      setAttachError(`最多 ${MAX_ATTACHMENTS} 张图, 删几张再加`);
      return;
    }
    const next: Attachment[] = [];
    for (const f of arr) {
      try {
        next.push(await fileToAttachment(f));
      } catch (e) {
        setAttachError((e as Error).message);
        return;
      }
    }
    setAttachments((cur) => [...cur, ...next]);
  }

  function removeAttachment(idx: number): void {
    setAttachments((cur) => cur.filter((_, i) => i !== idx));
    setAttachError(null);
  }

  function submit() {
    const t = text.trim();
    if ((!t && attachments.length === 0) || isStreaming) return;
    onSend(t, attachments);
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

  /** 粘贴: 看剪贴板里有没有图片(截屏后直接 Cmd+V) */
  function onPaste(e: ClipboardEvent<HTMLTextAreaElement>) {
    const items = Array.from(e.clipboardData?.items ?? []);
    const imageFiles: File[] = [];
    for (const it of items) {
      if (it.kind === "file" && it.type.startsWith("image/")) {
        const f = it.getAsFile();
        if (f) imageFiles.push(f);
      }
    }
    if (imageFiles.length > 0) {
      e.preventDefault(); // 阻止把二进制乱码塞进 textarea
      void addFiles(imageFiles);
    }
    // 没图就走默认 (粘贴文本)
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

  const canSend = !isStreaming && (text.trim().length > 0 || attachments.length > 0);

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
          {attachments.map((att, idx) => (
            <ThumbCard
              key={idx}
              attachment={att}
              onRemove={() => removeAttachment(idx)}
            />
          ))}
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

      <div
        style={{
          display: "flex",
          alignItems: "flex-end",
          gap: "var(--space-2)",
        }}
      >
        {/* 隐藏的 file input — 点 📎 按钮触发 */}
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          multiple
          onChange={onFileInputChange}
          style={{ display: "none" }}
        />

        {/* 📎 附件按钮 */}
        <button
          onClick={onPickFile}
          disabled={isStreaming || attachments.length >= MAX_ATTACHMENTS}
          title="加图片 (粘贴 / 拖放也可以)"
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

        <textarea
          ref={taRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKey}
          onPaste={onPaste}
          placeholder={
            attachments.length > 0
              ? "加点说明 (可空) — Enter 发送"
              : "跟小鲶说话…  (Enter 发送 · Shift+Enter 换行 · 📎/粘贴/拖入加图)"
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
        {isStreaming ? (
          <button
            onClick={onCancel}
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
