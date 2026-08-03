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
import { ArrowBendDownLeft, Microphone, Paperclip } from "@phosphor-icons/react";
import type { Attachment } from "../../types/chat";
import { useUIStore } from "../../store/ui";  // BL-E13 主动闲聊 prefill
import { useAgentStore } from "../../store/agent";  // BL-E11 后续: 员工自定义名
// 5/20 拆: useTeachingStore / useAutoContinueStore / useChatStore 移到子组件
import RecModeButton from "./RecModeButton";  // BL-LEARN-RECMODE Day 2 (5/14 #64)
// P3.5.167 (7/3 鸿波拍板 B): 3 教学 icon (🎓 教学 / 🎬 录屏 / 💡 让 AI 学) 叠成
// 📚 EduPopover 省空间. RecMode 4 modals 仍在 RecModeButton 里 render (state-driven).
// EduPopover 只用 RecModeToolbarButton 的 openSetup handler.
import EduPopover from "./components/EduPopover";

interface Props {
  isStreaming: boolean;
  /** 8/3: 按了停止但流还没停下来 (在等 tool 返回) */
  isCancelling?: boolean;
  onSend: (text: string, attachments: Attachment[]) => void;
  onCancel: () => void;
  /** BL-COMPANION-UX1 (5/12 鸿波"锁死"修): streaming 中一键 abort + 发新消息 */
  onCancelAndSend: (text: string, attachments: Attachment[]) => void;
  /** BL-HERMES013-RED-1A (5/13 借鉴 Hermes 0.13 ACP /queue): streaming 中
   *  排队下一条, 等当前 [DONE] 自动 send. 跟 onCancelAndSend 互补 (一个停一个排队). */
  onEnqueue: (text: string) => void;
  // P3.5.20.1 (6/17): onSteer prop 砍 — steer 整链退役.
  onReset: () => void;
}


// 5/20 拆 1107 → ~600: helpers + 5 子组件抽到 components/
// P3.5.177 (7/6): ingestAttachmentSourceFireForget 严格 kill (员工 send 时不自动
// 入库). helper 严格保留在 attachmentHelpers.ts 备用 (若 future 需 fallback).
import {
  fileToAttachment,
  MAX_ATTACHMENTS,
  SUPPORTED_AUDIO_EXTS,
} from "./components/attachmentHelpers";
import AutoContinueToggleButton from "./components/AutoContinueToggleButton";
import FileChip from "./components/FileChip";
import QueuedMessagesStrip from "./components/QueuedMessagesStrip";
// P3.5.167 (7/3 鸿波): TeachingToggleButton 移入 EduPopover, ChatInput 不再直接
// 引用. 老组件文件保留 (EduPopover 复用 useTeachingStore 内部 state 逻辑), 只是
// 不再作独立 button render. 未来若需彻底删可 grep 无 caller 后再动.
// import TeachingToggleButton from "./components/TeachingToggleButton";
import ThumbCard from "./components/ThumbCard";

export default function ChatInput({
  isStreaming,
  isCancelling,
  onSend,
  onCancel,
  onCancelAndSend,
  onEnqueue,
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
    // P3.5.177 (7/6 鸿波军规审判): 老 P16 fire-and-forget auto-ingest 严格删.
    //
    // 老行为: 员工每次 send 严格自动写 attachment 全文到 wiki/raw/sources/ →
    // sync_turn 3b 严格自动抽 entity/concept 到 wiki/entities + concepts.
    // 员工 7/6 反馈: "不是所有文档都要进知识库, 是员工显式的方式要求".
    //
    // 新行为: 上传只是 chat context (不污染 wiki), 员工明说"入库/存 wiki/
    // 记住这个文档" 严格 LLM 严格调 catfish_wiki_ingest tool 严格触发. AI-first,
    // 不 hardcode 关键词. 详见 catfish_tool_schemas.py `catfish_wiki_ingest`.
    //
    // ingestAttachmentSourceFireForget helper 严格保留 (以防 future 需 fallback),
    // 但严格不再自动调.
    // for (const att of attachments) {
    //   ingestAttachmentSourceFireForget(att);
    // }
    setText("");
    setAttachments([]);
    setAttachError(null);
  }

  /** P3.5.170 (7/3 鸿波 catch P3.5.167 UX 错): 老 handlePrefillLearn 已删.
   *
   *  老实现: 点 💡 → setText("/learn ") + focus, 员工继续输. 违反员工主权军规:
   *  暴露 hermes slash 语法, 员工可能疑惑"为什么要写 /learn?".
   *
   *  新实现: 点 💡 弹 LearnModal → 员工输**纯描述** → modal 内部拼
   *  "/learn <描述>" → 走此 callback → 直接调 onSend (类 submit) → hermes 8642
   *  → P29 patch translate → agent turn.
   *
   *  参数是完整 fullText (已含 "/learn " 前缀, 由 LearnModal 拼好). ChatInput
   *  只负责透传给 onSend, 不改.
   */
  function handleStartLearn(fullText: string) {
    // 类 submit() 逻辑 (line 191-206) 但简化: learn 场景无 attachments,
    // 无排队 (isStreaming 时也直接 abort 当前 send)
    if (isStreaming) {
      onCancelAndSend(fullText, []);
    } else {
      onSend(fullText, []);
    }
    // 学 skill 场景员工不希望 textarea 被污染 — 保空, 员工继续用 chat.
  }

  /** BL-HERMES013-RED-1A (5/13): streaming 中"排队下一条". 不打断当前 stream,
   *  排队消息暂不支持 attachments (in-memory 太大), 只能纯文字. */
  function enqueueSubmit() {
    const t = text.trim();
    if (!t) return;
    if (attachments.length > 0) {
      setAttachError("排队消息暂不支持附件 (内存限制). 等当前任务跑完再发带附件的消息.");
      return;
    }
    onEnqueue(t);
    setText("");
    setAttachError(null);
  }

  // P3.5.20 / P3.5.20.1 (6/17 鸿波): steerSubmit + [🎯 改主意] button + onSteer
  // prop + useChat.steer + lib/steer.ts + chatWire.applySteerPrefix + _steered
  // field + ChatMessage isSteered render 整链砍 — 设计意图未实现, 实测同效.

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
      className={`chat-composer${isDragOver ? " chat-composer--dragover" : ""}`}
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

      {/* BL-HERMES013-RED-1A (5/13): queue 状态显示 — 排队中的消息列出 +
          支持点 X 撤回. 当前 stream [DONE] 时 useChat 自动 dequeue + send */}
      <QueuedMessagesStrip />

      {/* 5/5 文件解析进行中 (Excel / 大 PDF 几秒级, 之前 0 反馈员工以为坏了)
          BL-VOICE3 (5/10): 音频走 whisper, 几十秒级别, label 区分提示 */}
      {isParsingFile && (
        <div className="chat-composer__status">
          <Paperclip size={14} aria-hidden="true" />
          {parseLabel}
        </div>
      )}

      <div className="chat-composer__surface">
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
          type="button"
          onClick={onPickFile}
          disabled={isStreaming || attachments.length >= MAX_ATTACHMENTS}
          title="添加图片或文档，也可拖入文件或粘贴截图"
          aria-label="添加附件"
          className="chat-composer__tool-button"
        >
          <Paperclip size={18} aria-hidden="true" />
        </button>

        {/* 📚 教学入口 popover (P3.5.167, 7/3 鸿波拍板 B 方案 "叠起来省空间"):
            3 教学场景语义 unified 叠成 1 popover:
              🎓 教学模式  (老 TeachingToggle, BL-LEAN-SESSION 5/13)
              🎬 录屏演示  (老 RecModeToolbarButton, BL-LEARN-RECMODE 5/14 #64)
              💡 让 AI 学  (hermes v0.18 /learn, P29 patch P3.5.168 7/3)

            RecMode 4 modals (SetupModal / RecordingOverlay / ErrorBanner /
            PreviewBanner) 保留在 <RecModeButton> 里, state-driven 触发, popover
            close 后 modal 仍能 render (老架构未破坏). EduPopover 只用 openSetup
            handler 触发 setup state. */}
        <EduPopover
          isStreaming={isStreaming}
          onStartLearn={handleStartLearn}
        />

        {/* ⏭ 自动接力 toggle (BL-AUTO-CONTINUE 5/13 鸿波"长程任务咋办").
            P3.5.46 (6/20 鸿波 catch '🔄 跟长程啥关系'): emoji 🔄 → ⏭ 跟系统自带 retry
            区分. 系统 retry (chat.ts P3.5.34 修-A/D, 默认开透明) 管"流挂了" 技术
            故障 (上游 idle / finish_reason 缺失); 本 toggle 管 "LLM finish_reason=
            stop 正常结束但任务没完" semantic 场景 — 自动发"继续" 续上下文
            (上限 3 轮防失控). 取代 gateway 删的 BL-FIX23 mid-task retry.
            适合长任务 (合并多个 Excel / 资质材料整理 / 长流程 skill 串联).
            关闭回常态: LLM stop 就 stop, 自己打"继续". 跨 session 隔离. */}
        <AutoContinueToggleButton isStreaming={isStreaming} />

        {/* 🎬 RecMode modals 层 (state-driven from useRecModeStore). Setup/
            Recording/Error/Preview 4 modals 保留在这里, click "🎬 录屏演示" (在
            📚 EduPopover 里) → openSetup() → state="setup" → SetupModal 弹.
            按钮部分被 EduPopover 替代 (hideToolbarButton), 但 modal render 仍
            走 RecModeButton (state-driven 分层不动, P3.5.167 refactor 无破坏). */}
        <RecModeButton disabled={isStreaming} hideToolbarButton />

        {/* 🎤 语音输入按钮 — 方案 C 五一 sprint Day 1: Whisper.cpp 本地
           按一下开始录音, 再按一下停止 → 自动转文字填到 textarea. 数据 100% 本地. */}
        <button
          type="button"
          onClick={isRecording ? stopRecording : startRecording}
          disabled={isStreaming || isTranscribing}
          title={
            isRecording
              ? "再按一下结束录音"
              : isTranscribing
              ? "识别中…"
              : "语音输入 (Whisper.cpp 本地, 不上传)"
          }
          aria-label={isRecording ? "结束录音" : "语音输入"}
          data-active={isRecording || undefined}
          className="chat-composer__tool-button chat-composer__tool-button--voice"
        >
          {isTranscribing ? <span aria-hidden="true">…</span> : <Microphone size={18} aria-hidden="true" />}
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
              : `跟${agentName}说话…`
          }
          rows={1}
          className="chat-composer__textarea"
          disabled={false /* 仍允许写下一个，发送按钮在 streaming 时变停止 */}
        />
        {/* BL-COMPANION-UX1 (5/12) + BL-HERMES013-RED-1A (5/13 ACP /queue) +
            BL-HERMES013-RED-1B (5/13 ACP /steer):
            按钮组 4 态:
            - 非 streaming + 有内容       → "发送" (青)
            - streaming + 有内容          → [⏳ 排队] [🎯 改主意] [⏹ 停下接着发] 三排
              用户选:
                ⏳ 排队     = 不打断当前等完再发 (next turn)
                🎯 改主意   = 中途插话改方向, LLM 看到自己 partial 输出 + 新指令综合 (steer)
                ⏹ 停下接着发 = 直接 abort 当前重新问, LLM 看不到自己刚说的部分 (cancelAndSend)
            - streaming + 没内容          → "停止" (橙色, 单纯 abort)
            Enter 默认 ⏹ 停下接着发 (跟 BL-COMPANION-UX1 一致), 排队 / 改主意要点专门按钮 */}
        {isStreaming && hasContent ? (
          <div className="chat-composer__send-group">
            <button
              type="button"
              onClick={enqueueSubmit}
              title="排队等当前任务跑完, 自动发 (借鉴 Hermes 0.13 ACP /queue). 排队消息暂不支持附件."
              className="chat-composer__secondary-button"
            >
              排队
            </button>
            {/* P3.5.20 (6/17 鸿波): [🎯 改主意] 按钮砍 — 设计意图 (LLM 看 partial
                接力) 未实现, 跟 [⏹ 停下接着发] 实测行为一样, 砍掉减歧义. */}
            <button
              type="button"
              onClick={submit}
              title="停止当前流, 立刻发送新消息 (Enter 同效, LLM 看不到自己刚说的, 完全重新回答)"
              className="chat-composer__primary-button"
            >
              停下并发送
            </button>
          </div>
        ) : isStreaming ? (
          /* 8/3: 已经按过停止 → 按钮如实说"在收尾", 而不是继续显示"停止"。
             已发出的 tool 调用取消不掉 (Tauri invoke 无取消机制), 这段等待
             是真实存在的。老版本这里一直显示"停止", 员工点完毫无变化, 就
             得出"停止按钮无效"的结论 —— 而 abort 其实已经发出去了。
             按钮置灰是因为再点一次确实没有任何额外效果, 不该假装有。 */
          <button
            type="button"
            onClick={onCancel}
            disabled={isCancelling}
            title={
              isCancelling
                ? "已经在停了 — 正在等当前这步工具返回, 它没法中途掐断"
                : "停止当前流"
            }
            className="chat-composer__stop-button"
          >
            {isCancelling ? "停止中…" : "停止"}
          </button>
        ) : (
          <button
            type="button"
            onClick={submit}
            disabled={!canSend}
            className="chat-composer__primary-button"
          >
            发送
            <ArrowBendDownLeft className="chat-composer__send-key" size={14} aria-hidden="true" />
          </button>
        )}
      </div>
    </div>
  );
}

/** 文件类型 → emoji 图标 */
