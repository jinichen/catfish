/** BL-LEARN-RECMODE Companion UI (5/14 Day 2 #64).
 *
 * 🎙 RecMode 按钮 + setup 模态 + 录制中浮层.
 * 状态机走 store/recmode.ts (idle / setup / recording / analyzing / preview / error).
 *
 * 流程 (用户视角):
 *   点 🎙 → setup 模态 (填名字 / namespace / 简述)
 *   点 "开始录屏 + 录音" → POST /api/learn/start_recording (CDP 后端起 listener)
 *                       → speech_start_recording (ffmpeg 录音, 复用 BL-VOICE3)
 *                       → 进 recording 状态, 显浮层 (⏱ 计时 + 提示 "去 Catfish Chrome 操作")
 *   用户操作 Catfish Chrome 演示流程 + 顺嘴说意图
 *   点 "✅ 完成教学" → speech_stop_and_transcribe (whisper 转写 → transcripts.jsonl)
 *                  → POST /api/learn/stop_recording → 进 analyzing 状态
 *                  → POST /api/learn/analyze (调 main 综合) → 进 preview 状态
 *   preview UI (Day 3 加): 显 SKILL.md + 三按钮 (跑 / 保存 / 重录)
 *
 * Day 2 (本次) ship 的: 🎙 按钮 + setup 模态 + recording 浮层 + 启停
 * Day 3 ship 的: preview 模态 + 错误状态机 + skill 跑一次试
 */

import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";

import {
  useRecModeStore,
  formatRecordingElapsed,
  isValidSkillTitle,
  RECMODE_EXAMPLES,
} from "../../store/recmode";
import {
  newRecModeSessionId,
  startRecording as apiStartRecording,
  stopRecording as apiStopRecording,
  analyzeRecording,
  recordTranscript,
  getRecordingStatus,
  getSkillContent,
  saveSkill,
  testSkill,
  type RecordingStatus,
  type SkillContentResponse,
  type TestSkillResponse,
} from "../../lib/recmode";

interface Props {
  /** 跟 ChatInput 其他 toggle 同 disabled 条件 (chat streaming 时禁用) */
  disabled?: boolean;
}

export default function RecModeButton({ disabled }: Props) {
  const state = useRecModeStore((s) => s.state);

  return (
    <>
      <RecModeToolbarButton disabled={disabled} />
      {state === "setup" && <SetupModal />}
      {(state === "recording" || state === "analyzing") && <RecordingOverlay />}
      {state === "error" && <ErrorBanner />}
      {state === "preview" && <PreviewBanner />}
    </>
  );
}

// ─── 工具栏 🎙 按钮 ────────────────────────────────────────


function RecModeToolbarButton({ disabled }: { disabled?: boolean }) {
  const state = useRecModeStore((s) => s.state);
  const openSetup = useRecModeStore((s) => s.openSetup);
  const isActive = state === "recording" || state === "analyzing";

  return (
    <button
      onClick={openSetup}
      disabled={disabled || isActive}
      title={
        isActive
          ? "RecMode 录制中 — 看右下浮层"
          : "🎙 录屏教学 — 演示一遍 + 顺嘴说意图, 鲶鱼自动学会这个流程"
      }
      style={{
        padding: "6px 10px",
        border: "1px solid " + (isActive ? "var(--status-err)" : "var(--catfish-border)"),
        borderRadius: "var(--radius-sm)",
        background: isActive ? "var(--status-err)" : "transparent",
        color: isActive ? "white" : "var(--catfish-text-muted)",
        fontSize: 14,
        cursor: disabled || isActive ? "default" : "pointer",
        lineHeight: 1,
        minHeight: 36,
        animation: isActive ? "catfish-pulse 1.2s ease-in-out infinite" : undefined,
      }}
    >
      🎙
    </button>
  );
}

// ─── setup 模态 ────────────────────────────────────────────


// 5/14 鸿波 "你现在故意怠工" 反馈后: 全硬 hex 替 var, 不依赖 mac
// dark/light theme 渲染. 这套 token 是 macOS Sonoma 14 设计语言.
const T = {
  cyan: "#06B6D4",          // catfish 主色, 实际可见 cyan 不是水绿
  cyanHover: "#0891B2",
  text: "#1d1d1f",          // macOS body primary
  textSecondary: "#6e6e73", // macOS body secondary
  textTertiary: "#86868b",
  bgWhite: "#FFFFFF",
  bgSurface: "#fbfbfd",     // 模态卡片背景, 比纯白多一点深度
  bgInput: "#ffffff",
  border: "#D2D2D7",        // macOS hairline 标准
  borderFocus: "#06B6D4",
  errorRed: "#ff3b30",      // macOS 系统错误红
  shadow: "0 16px 48px rgba(0,0,0,0.16), 0 4px 12px rgba(0,0,0,0.06)",
  closeBg: "#e5e5e7",
  closeBgHover: "#d2d2d7",
  systemFont: "-apple-system, BlinkMacSystemFont, 'SF Pro Display', 'SF Pro Text', sans-serif",
};


function SetupModal() {
  const setup = useRecModeStore((s) => s.setup);
  const setSetup = useRecModeStore((s) => s.setSetup);
  const closeSetup = useRecModeStore((s) => s.closeSetup);
  const startRecording = useRecModeStore((s) => s.startRecording);
  const setError = useRecModeStore((s) => s.setError);
  const [submitting, setSubmitting] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [closeHover, setCloseHover] = useState(false);
  const [submitHover, setSubmitHover] = useState(false);

  // 5/14 鸿波"UI 不是产品水平" 反馈后改: 用户输人话标题, 后端 LLM 综合时
  // 自动起 snake_case skill_name. 不暴露 snake_case / namespace 术语.
  const titleValid = isValidSkillTitle(setup.name);
  const titleTrimmed = setup.name.trim();

  const [shake, setShake] = useState(false);

  async function onStart() {
    if (submitting) return;
    if (!titleValid) {
      // 不弹错 — input 抖一下 + focus, 视觉提示让用户填名字 (macOS 习惯)
      setShake(true);
      setTimeout(() => setShake(false), 400);
      const inp = document.querySelector<HTMLInputElement>(
        'input[placeholder^="例:"]',
      );
      inp?.focus();
      return;
    }
    setSubmitting(true);
    try {
      const sessionId = newRecModeSessionId();
      await apiStartRecording(sessionId);
      try {
        await invoke("speech_start_recording");
      } catch (e) {
        console.warn("[recmode] speech_start_recording 失败 (继续, 没语音):", e);
      }
      startRecording(sessionId);
    } catch (e) {
      setError(`开录屏失败: ${(e as Error).message || e}`);
    } finally {
      setSubmitting(false);
    }
  }

  function applyExample(idx: number) {
    const ex = RECMODE_EXAMPLES[idx];
    setSetup({ name: ex.title, description: ex.description });
  }

  return (
    <ModalShell onClose={closeSetup}>
      <style>{`
        @keyframes recmode-shake {
          0%, 100% { transform: translateX(0); }
          25% { transform: translateX(-6px); }
          75% { transform: translateX(6px); }
        }
        @keyframes recmode-fade-in {
          from { opacity: 0; transform: translateY(8px) scale(0.98); }
          to   { opacity: 1; transform: translateY(0) scale(1); }
        }
        @keyframes recmode-overlay-fade {
          from { opacity: 0; }
          to   { opacity: 1; }
        }
      `}</style>

      {/* macOS NSCloseButton style — 圆形灰底 + 内部 SF-symbol-style ✕ */}
      <button
        onClick={closeSetup}
        onMouseEnter={() => setCloseHover(true)}
        onMouseLeave={() => setCloseHover(false)}
        title="关闭"
        style={{
          position: "absolute",
          top: 16,
          right: 16,
          width: 22,
          height: 22,
          borderRadius: "50%",
          border: "none",
          background: closeHover ? T.closeBgHover : T.closeBg,
          color: T.textSecondary,
          fontSize: 11,
          cursor: "pointer",
          lineHeight: 1,
          padding: 0,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          transition: "background 120ms ease",
        }}
        aria-label="关闭"
      >
        <svg width="9" height="9" viewBox="0 0 9 9" fill="none">
          <path d="M1 1 L8 8 M8 1 L1 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      </button>

      {/* 视觉锚 — macOS Sonoma 风的 badge: 浅 cyan 圆 + 白线条 mic 图标 */}
      <div style={{
        width: 56,
        height: 56,
        borderRadius: 14,
        background: `linear-gradient(135deg, ${T.cyan}, ${T.cyanHover})`,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        marginBottom: 18,
        boxShadow: `0 6px 16px ${T.cyan}40`,
      }}>
        <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z" />
          <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
          <line x1="12" y1="19" x2="12" y2="22" />
        </svg>
      </div>

      <h3 style={{
        margin: 0,
        fontSize: 19,
        fontWeight: 600,
        color: T.text,
        letterSpacing: "-0.015em",
        fontFamily: T.systemFont,
      }}>
        教鲶鱼一个新流程
      </h3>
      <p style={{
        margin: "6px 0 0",
        fontSize: 13,
        color: T.textSecondary,
        lineHeight: 1.5,
        fontFamily: T.systemFont,
      }}>
        录一遍你正常操作, <strong style={{ color: T.text, fontWeight: 600 }}>顺嘴说意图</strong>, 鲶鱼自动学
      </p>

      {/* 主输入 — macOS native white bg + hairline border */}
      <div style={{ marginTop: 22 }}>
        <input
          type="text"
          value={setup.name}
          onChange={(e) => setSetup({ name: e.target.value })}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !submitting) onStart();
          }}
          placeholder="例: 检查 EIS 资质过期"
          autoFocus
          style={{
            width: "100%",
            padding: "11px 14px",
            border: `1px solid ${titleTrimmed && !titleValid ? T.errorRed : T.border}`,
            borderRadius: 8,
            background: T.bgInput,
            color: T.text,
            fontSize: 14,
            fontFamily: T.systemFont,
            outline: "none",
            boxSizing: "border-box",
            transition: "border-color 120ms ease, box-shadow 120ms ease",
            animation: shake ? "recmode-shake 0.4s ease" : undefined,
          }}
          onFocus={(e) => {
            e.currentTarget.style.borderColor = T.borderFocus;
            e.currentTarget.style.boxShadow = `0 0 0 3px ${T.cyan}25`;
          }}
          onBlur={(e) => {
            e.currentTarget.style.borderColor = titleTrimmed && !titleValid ? T.errorRed : T.border;
            e.currentTarget.style.boxShadow = "none";
          }}
        />
      </div>

      {/* 示例 inline 链接 (cyan, 简约) */}
      <div style={{
        marginTop: 12,
        fontSize: 12,
        color: T.textTertiary,
        fontFamily: T.systemFont,
        lineHeight: 1.6,
      }}>
        没思路?{" "}
        {RECMODE_EXAMPLES.map((ex, i) => (
          <span key={i}>
            <button
              onClick={() => applyExample(i)}
              title={ex.description}
              style={{
                padding: 0,
                border: "none",
                background: "transparent",
                color: T.cyan,
                fontSize: 12,
                fontFamily: T.systemFont,
                cursor: "pointer",
              }}
              onMouseEnter={(e) => { e.currentTarget.style.color = T.cyanHover; }}
              onMouseLeave={(e) => { e.currentTarget.style.color = T.cyan; }}
            >
              {ex.title}
            </button>
            {i < RECMODE_EXAMPLES.length - 1 && <span style={{ color: T.textTertiary }}> · </span>}
          </span>
        ))}
      </div>

      {/* 高级选项 — 极淡, 真要的人才点 */}
      <div style={{ marginTop: 24 }}>
        <button
          onClick={() => setShowAdvanced(!showAdvanced)}
          style={{
            background: "transparent",
            border: "none",
            color: T.textTertiary,
            fontSize: 12,
            fontFamily: T.systemFont,
            cursor: "pointer",
            padding: 0,
          }}
        >
          {showAdvanced ? "▾ 高级选项" : "▸ 高级选项"}
        </button>
        {showAdvanced && (
          <div style={{ marginTop: 10 }}>
            <select
              value={setup.namespace}
              onChange={(e) => setSetup({ namespace: e.target.value })}
              style={{
                padding: "6px 10px",
                border: `1px solid ${T.border}`,
                borderRadius: 6,
                background: T.bgInput,
                color: T.text,
                fontSize: 12,
                fontFamily: T.systemFont,
                outline: "none",
              }}
            >
              <option value="personal">只我自己用</option>
              <option value="department">同部门可用</option>
              <option value="public">全公司可用</option>
            </select>
          </div>
        )}
      </div>

      {/* 按钮 bar — hairline 分隔 + 平衡视觉重量 (取消 ghost / 开始 cyan 实心) */}
      <div style={{
        display: "flex",
        gap: 8,
        marginTop: 24,
        paddingTop: 18,
        borderTop: `1px solid ${T.border}`,
        alignItems: "center",
      }}>
        <button
          onClick={closeSetup}
          disabled={submitting}
          style={{
            padding: "9px 18px",
            border: "none",
            borderRadius: 8,
            background: "transparent",
            color: T.textSecondary,
            fontSize: 14,
            fontFamily: T.systemFont,
            cursor: submitting ? "default" : "pointer",
          }}
        >
          取消
        </button>
        <div style={{ flex: 1 }} />
        <button
          onClick={onStart}
          onMouseEnter={() => setSubmitHover(true)}
          onMouseLeave={() => setSubmitHover(false)}
          disabled={submitting}
          style={{
            padding: "9px 22px",
            border: "none",
            borderRadius: 8,
            background: submitting
              ? T.textTertiary
              : (submitHover ? T.cyanHover : T.cyan),
            color: "white",
            fontSize: 14,
            fontWeight: 600,
            fontFamily: T.systemFont,
            cursor: submitting ? "default" : "pointer",
            opacity: titleValid ? 1 : 0.7,
            transition: "background 120ms ease, opacity 120ms ease",
            boxShadow: titleValid ? `0 2px 6px ${T.cyan}40` : "none",
          }}
        >
          {submitting ? "启动中…" : "开始录屏"}
        </button>
      </div>
    </ModalShell>
  );
}

// ─── 录制中浮层 + analyzing 状态 ───────────────────────────


function RecordingOverlay() {
  const state = useRecModeStore((s) => s.state);
  const sessionId = useRecModeStore((s) => s.sessionId);
  const startedAt = useRecModeStore((s) => s.startedAt);
  const stopRecording = useRecModeStore((s) => s.stopRecording);
  const showPreview = useRecModeStore((s) => s.showPreview);
  const setError = useRecModeStore((s) => s.setError);
  const reset = useRecModeStore((s) => s.reset);
  const [submitting, setSubmitting] = useState(false);
  const [, setTick] = useState(0);
  const [liveStatus, setLiveStatus] = useState<RecordingStatus | null>(null);

  // 1 秒一刷计时
  useEffect(() => {
    if (state !== "recording") return;
    const t = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [state]);

  // F: 每 2s polling /api/learn/status 拉实时 keyframes / events 计数
  useEffect(() => {
    if (state !== "recording" || !sessionId) return;
    let cancelled = false;
    async function poll() {
      try {
        const s = await getRecordingStatus(sessionId!);
        if (!cancelled) setLiveStatus(s);
      } catch {
        // session 没了 / 网络挂 — 静默, 用户用 stop 手动收尾
      }
    }
    void poll();
    const t = setInterval(poll, 2000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [state, sessionId]);

  async function onFinish() {
    if (!sessionId || submitting) return;
    setSubmitting(true);
    try {
      // 停 ffmpeg 录音 + 转写 → A 写 transcripts.jsonl
      try {
        const transcript = await invoke<string>("speech_stop_and_transcribe");
        if (transcript && transcript.trim()) {
          // A: 真写到 ~/.catfish/recordings/<sid>/transcripts.jsonl
          const elapsedAtStop = startedAt
            ? Math.floor(Date.now() / 1000) - startedAt
            : 0;
          await recordTranscript(sessionId, transcript, {
            tsOffset: 0,  // 整段从 0 开始 (whisper 没分句 ts, RecMode 复用整段)
            duration: elapsedAtStop,
          });
        }
      } catch (e) {
        console.warn("[recmode] speech_stop / record_transcript 失败 (继续, 没语音):", e);
      }
      // 停 CDP listener (flush events.jsonl + meta.json)
      await apiStopRecording(sessionId);
      // 触发 analyze (调 main 综合 → 落 draft skill)
      stopRecording();  // state → analyzing
      const preview = await analyzeRecording(sessionId);
      showPreview(preview);
    } catch (e) {
      setError(`完成教学失败: ${(e as Error).message || e}`);
    } finally {
      setSubmitting(false);
    }
  }

  async function onAbort() {
    if (!sessionId) return;
    try {
      await invoke("speech_stop_and_transcribe").catch(() => {});
      await apiStopRecording(sessionId).catch(() => {});
    } finally {
      reset();
    }
  }

  return (
    <OverlayShell>
      <div style={{ fontSize: 14, fontWeight: 600, marginBottom: "var(--space-2)" }}>
        {state === "recording" ? "🔴 录屏中" : "⏳ 分析中..."}
      </div>
      {state === "recording" && (
        <>
          <div style={{ fontSize: 12, color: "var(--catfish-text-muted)", marginBottom: "var(--space-3)" }}>
            ⏱ {formatRecordingElapsed(startedAt)}
            {liveStatus && <> · 📸 {liveStatus.keyframes_count} keyframes · 📊 {liveStatus.events_count} events</>}
            {liveStatus && !liveStatus.ws_connected && (
              <span style={{ color: "var(--status-warn)" }}> · ⚠ ws 未连</span>
            )}
          </div>
          <div style={{ fontSize: 13, lineHeight: 1.5, marginBottom: "var(--space-3)" }}>
            现在切到 <strong>Catfish Chrome</strong> 正常操作.
            <br />
            <strong>顺嘴说</strong> 你在干嘛 (鲶鱼能听懂):
            <br />
            <span style={{ color: "var(--catfish-text-muted)", fontSize: 12 }}>
              "现在点应用是为了进资质管理"
              <br />
              "看这个证书有效期, 90 天内的就要标"
            </span>
          </div>
          <div style={{ display: "flex", gap: "var(--space-2)" }}>
            <button onClick={onFinish} disabled={submitting} style={btnStyle("primary", submitting)}>
              {submitting ? "处理中..." : "✅ 完成教学"}
            </button>
            <button onClick={onAbort} disabled={submitting} style={btnStyle("secondary", submitting)}>
              ❌ 取消放弃
            </button>
          </div>
        </>
      )}
      {state === "analyzing" && (
        <div style={{ fontSize: 13, color: "var(--catfish-text-muted)" }}>
          鲶鱼后端 (catfish-private-main) 正在综合你刚录的 events + 截图 + 语音.
          <br />
          这一步通常 30-90 秒, 取决于录屏长度.
        </div>
      )}
    </OverlayShell>
  );
}

// ─── error / preview banner (Day 3 完整版扩展) ────────────


function ErrorBanner() {
  const errorMessage = useRecModeStore((s) => s.errorMessage);
  const errorCategory = useRecModeStore((s) => s.errorCategory);
  const reset = useRecModeStore((s) => s.reset);
  const openSetup = useRecModeStore((s) => s.openSetup);

  // G: 按 category 给针对性 hint + 修法
  const categoryInfo: Record<string, { title: string; hint: string; canRetry: boolean }> = {
    cdp_unavailable: {
      title: "Catfish Chrome 没起",
      hint: "去 Companion '控制台' 点'启动 Catfish Chrome', 起好后重试. 或确认 Chrome 用 --remote-debugging-port=9222 启动.",
      canRetry: true,
    },
    whisper_failed: {
      title: "录音失败",
      hint: "ffmpeg 或 whisper.cpp 跑挂了. 看 Companion '控制台' 错日志. 没语音也能跑 RecMode (只是 main 综合时少一类信号), 重试吧.",
      canRetry: true,
    },
    aggregator_timeout: {
      title: "鲶鱼分析超时",
      hint: "main 综合 5-10 分钟录屏一般 30-90 秒, 超时通常是录得太长 (>20 min) 或内网 LLM 排队. 重试; 还慢就拆短录屏.",
      canRetry: true,
    },
    llm_parse_failed: {
      title: "鲶鱼输出格式错",
      hint: "main 综合输出的 JSON parse 不了. 通常是 SYSTEM_PROMPT 没卡住格式. 把 questions_for_user 反馈我们调 prompt. 重录可能 OK.",
      canRetry: true,
    },
    network: {
      title: "网络错",
      hint: "catfish-gateway 不可达 / VPN 抖动. 看 gateway 是不是起着 (`pkill -f catfish_gateway` 然后重启).",
      canRetry: true,
    },
    unknown: {
      title: "RecMode 出错",
      hint: "原始错: " + (errorMessage || "(无详情)"),
      canRetry: true,
    },
  };
  const info = categoryInfo[errorCategory || "unknown"] || categoryInfo.unknown;

  return (
    <ModalShell onClose={reset}>
      <button
        onClick={reset}
        title="关闭"
        style={{
          position: "absolute",
          top: 16,
          right: 16,
          width: 22,
          height: 22,
          borderRadius: "50%",
          border: "none",
          background: T.closeBg,
          color: T.textSecondary,
          fontSize: 11,
          cursor: "pointer",
          padding: 0,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <svg width="9" height="9" viewBox="0 0 9 9" fill="none">
          <path d="M1 1 L8 8 M8 1 L1 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      </button>

      <div style={{
        width: 56,
        height: 56,
        borderRadius: 14,
        background: `linear-gradient(135deg, ${T.errorRed}, #ff6b3d)`,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        marginBottom: 18,
        boxShadow: `0 6px 16px ${T.errorRed}40`,
      }}>
        <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round">
          <line x1="12" y1="8" x2="12" y2="13" />
          <circle cx="12" cy="17" r="0.5" fill="white" />
        </svg>
      </div>

      <h3 style={{
        margin: 0,
        fontSize: 19,
        fontWeight: 600,
        color: T.text,
        fontFamily: T.systemFont,
      }}>
        {info.title}
      </h3>
      <p style={{
        margin: "8px 0 0",
        fontSize: 13,
        color: T.textSecondary,
        lineHeight: 1.5,
        fontFamily: T.systemFont,
      }}>
        {info.hint}
      </p>

      {/* 折叠原始错信息 */}
      {errorCategory !== "unknown" && (
        <details style={{ marginTop: 14, fontSize: 11, color: T.textTertiary, fontFamily: T.systemFont }}>
          <summary style={{ cursor: "pointer" }}>原始错信息</summary>
          <pre style={{
            marginTop: 6,
            padding: 10,
            background: "#f5f5f7",
            borderRadius: 6,
            fontSize: 11,
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            color: T.textSecondary,
          }}>
            {errorMessage}
          </pre>
        </details>
      )}

      <div style={{
        display: "flex",
        gap: 8,
        marginTop: 22,
        paddingTop: 16,
        borderTop: `1px solid ${T.border}`,
        alignItems: "center",
      }}>
        <button
          onClick={reset}
          style={{
            padding: "9px 18px",
            border: "none",
            borderRadius: 8,
            background: "transparent",
            color: T.textSecondary,
            fontSize: 14,
            fontFamily: T.systemFont,
            cursor: "pointer",
          }}
        >
          关闭
        </button>
        <div style={{ flex: 1 }} />
        {info.canRetry && (
          <button
            onClick={openSetup}
            style={{
              padding: "9px 22px",
              border: "none",
              borderRadius: 8,
              background: T.cyan,
              color: "white",
              fontSize: 14,
              fontWeight: 600,
              fontFamily: T.systemFont,
              cursor: "pointer",
              boxShadow: `0 2px 6px ${T.cyan}40`,
            }}
          >
            🔄 重试
          </button>
        )}
      </div>
    </ModalShell>
  );
}


function PreviewBanner() {
  // Day 3 完整 PreviewModal — 显 SKILL.md + main.py + 三按钮 (跑试 / 保存 / 重录)
  const preview = useRecModeStore((s) => s.preview);
  const reset = useRecModeStore((s) => s.reset);
  const openSetup = useRecModeStore((s) => s.openSetup);
  const setError = useRecModeStore((s) => s.setError);
  const [content, setContent] = useState<SkillContentResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState<"skill" | "code">("skill");
  const [testResult, setTestResult] = useState<TestSkillResponse | null>(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savedTo, setSavedTo] = useState<string | null>(null);

  // 拉 SKILL.md + main.py 内容
  useEffect(() => {
    if (!preview) return;
    let cancelled = false;
    setLoading(true);
    getSkillContent(preview.skill_dir)
      .then((c) => { if (!cancelled) setContent(c); })
      .catch((e) => { if (!cancelled) setError(`读 skill 文件失败: ${e.message || e}`); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [preview, setError]);

  if (!preview) return null;

  async function onTest() {
    if (!preview || testing) return;
    setTesting(true);
    setTestResult(null);
    try {
      const r = await testSkill(preview.skill_dir);
      setTestResult(r);
    } catch (e) {
      setTestResult({
        ok: false,
        duration_s: 0,
        skill_path: preview.skill_dir,
        error: (e as Error).message || String(e),
      });
    } finally {
      setTesting(false);
    }
  }

  async function onSave() {
    if (!preview || saving) return;
    setSaving(true);
    try {
      const r = await saveSkill(preview.skill_dir);
      setSavedTo(r.final_dir);
    } catch (e) {
      setError(`保存失败: ${(e as Error).message || e}`);
    } finally {
      setSaving(false);
    }
  }

  function onRerecord() {
    // 重录 — 关 preview, 开 setup (保留之前 setup 数据让用户改名)
    openSetup();
  }

  const confidenceColor =
    preview.confidence >= 0.7 ? "#34c759"
    : preview.confidence >= 0.4 ? "#ff9500"
    : "#ff3b30";

  return (
    <ModalShell onClose={reset}>
      {/* 关闭 X */}
      <button
        onClick={reset}
        title="关闭"
        style={{
          position: "absolute",
          top: 16,
          right: 16,
          width: 22,
          height: 22,
          borderRadius: "50%",
          border: "none",
          background: T.closeBg,
          color: T.textSecondary,
          fontSize: 11,
          cursor: "pointer",
          padding: 0,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <svg width="9" height="9" viewBox="0 0 9 9" fill="none">
          <path d="M1 1 L8 8 M8 1 L1 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      </button>

      {/* header — skill 名 + confidence + step count */}
      <div style={{ paddingTop: 4 }}>
        <div style={{
          display: "inline-block",
          padding: "2px 8px",
          fontSize: 11,
          fontWeight: 500,
          color: "white",
          background: "#34c759",
          borderRadius: 6,
          marginBottom: 8,
          letterSpacing: 0.5,
        }}>
          ✓ DRAFT
        </div>
        <h3 style={{
          margin: 0,
          fontSize: 19,
          fontWeight: 600,
          color: T.text,
          letterSpacing: "-0.015em",
          fontFamily: T.systemFont,
        }}>
          {preview.skill_name}
        </h3>
        <div style={{
          display: "flex",
          gap: 12,
          alignItems: "center",
          marginTop: 6,
          fontSize: 12,
          color: T.textSecondary,
          fontFamily: T.systemFont,
        }}>
          <span>{preview.namespace}</span>
          <span>·</span>
          <span>{preview.steps_count} 步</span>
          <span>·</span>
          <span>
            自评{" "}
            <span style={{ color: confidenceColor, fontWeight: 600 }}>
              {(preview.confidence * 100).toFixed(0)}%
            </span>
          </span>
        </div>
      </div>

      {/* 待 confirm 问题 (LLM 不确定的点) */}
      {preview.questions_for_user.length > 0 && (
        <div style={{
          marginTop: 14,
          padding: "10px 12px",
          background: "#fff8e1",
          border: "1px solid #ffe082",
          borderRadius: 8,
          fontSize: 12,
          color: "#7c5500",
          fontFamily: T.systemFont,
        }}>
          <strong style={{ display: "block", marginBottom: 4 }}>⚠ 鲶鱼有些不确定的点:</strong>
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {preview.questions_for_user.map((q, i) => (
              <li key={i} style={{ marginTop: 2 }}>{q}</li>
            ))}
          </ul>
        </div>
      )}

      {/* tabs: SKILL.md / main.py */}
      <div style={{
        marginTop: 18,
        borderBottom: `1px solid ${T.border}`,
        display: "flex",
        gap: 0,
      }}>
        <TabButton active={activeTab === "skill"} onClick={() => setActiveTab("skill")}>
          📄 说明
        </TabButton>
        <TabButton active={activeTab === "code"} onClick={() => setActiveTab("code")}>
          {"</>"} 代码
        </TabButton>
      </div>

      {/* 内容区 — SKILL.md 渲染 / main.py 代码块 */}
      <div style={{
        marginTop: 0,
        height: 240,
        overflowY: "auto",
        padding: "14px 16px",
        background: T.bgWhite,
        border: `1px solid ${T.border}`,
        borderTop: "none",
        borderRadius: "0 0 8px 8px",
        fontSize: 12,
        fontFamily: activeTab === "code" ? "ui-monospace, 'SF Mono', Menlo, monospace" : T.systemFont,
        lineHeight: 1.55,
        color: T.text,
        whiteSpace: "pre-wrap",
      }}>
        {loading ? (
          <span style={{ color: T.textTertiary }}>加载中…</span>
        ) : !content ? (
          <span style={{ color: T.textTertiary }}>读不到 skill 文件</span>
        ) : activeTab === "skill" ? (
          content.skill_md || "(SKILL.md 空)"
        ) : (
          content.main_py || "(main.py 空)"
        )}
      </div>

      {/* 测试结果 (跑一次试 后显) */}
      {testResult && (
        <div style={{
          marginTop: 12,
          padding: "10px 12px",
          background: testResult.ok ? "#e8f7ed" : "#fef0f0",
          border: `1px solid ${testResult.ok ? "#34c759" : T.errorRed}`,
          borderRadius: 8,
          fontSize: 12,
          fontFamily: T.systemFont,
          color: testResult.ok ? "#1e6e2e" : "#a8201a",
        }}>
          <strong>{testResult.ok ? "✅ 跑通" : "❌ 跑挂"}</strong>
          {testResult.duration_s > 0 && <> · {testResult.duration_s.toFixed(1)}s</>}
          {testResult.error && (
            <div style={{ marginTop: 4, fontFamily: "monospace", fontSize: 11 }}>
              {testResult.error.slice(0, 200)}
            </div>
          )}
          {testResult.stdout && (
            <details style={{ marginTop: 6 }}>
              <summary style={{ cursor: "pointer" }}>stdout</summary>
              <pre style={{ marginTop: 4, fontSize: 11, maxHeight: 100, overflow: "auto" }}>
                {testResult.stdout.slice(0, 1500)}
              </pre>
            </details>
          )}
        </div>
      )}

      {/* 已保存提示 */}
      {savedTo && (
        <div style={{
          marginTop: 12,
          padding: "10px 12px",
          background: "#e8f7ed",
          border: `1px solid #34c759`,
          borderRadius: 8,
          fontSize: 12,
          fontFamily: T.systemFont,
          color: "#1e6e2e",
        }}>
          ✅ 已保存到 <code style={{ fontSize: 11 }}>{savedTo}</code>
        </div>
      )}

      {/* 按钮 bar — 三按钮 + 关 */}
      <div style={{
        display: "flex",
        gap: 8,
        marginTop: 20,
        paddingTop: 16,
        borderTop: `1px solid ${T.border}`,
        alignItems: "center",
      }}>
        <button
          onClick={onRerecord}
          style={{
            padding: "9px 14px",
            border: "none",
            borderRadius: 8,
            background: "transparent",
            color: T.textSecondary,
            fontSize: 13,
            fontFamily: T.systemFont,
            cursor: "pointer",
          }}
        >
          🔄 重录
        </button>
        <div style={{ flex: 1 }} />
        <button
          onClick={onTest}
          disabled={testing}
          style={{
            padding: "9px 14px",
            border: `1px solid ${T.cyan}`,
            borderRadius: 8,
            background: "transparent",
            color: T.cyan,
            fontSize: 13,
            fontWeight: 500,
            fontFamily: T.systemFont,
            cursor: testing ? "default" : "pointer",
          }}
        >
          {testing ? "跑中…" : "🚀 跑一次试"}
        </button>
        <button
          onClick={onSave}
          disabled={saving || !!savedTo}
          style={{
            padding: "9px 18px",
            border: "none",
            borderRadius: 8,
            background: savedTo ? T.textTertiary : T.cyan,
            color: "white",
            fontSize: 13,
            fontWeight: 600,
            fontFamily: T.systemFont,
            cursor: saving || savedTo ? "default" : "pointer",
            boxShadow: savedTo ? "none" : `0 2px 6px ${T.cyan}40`,
          }}
        >
          {saving ? "保存中…" : savedTo ? "✓ 已保存" : "💾 保存到 skills"}
        </button>
      </div>
    </ModalShell>
  );
}


function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: "8px 16px",
        border: "none",
        background: "transparent",
        color: active ? T.text : T.textTertiary,
        fontSize: 13,
        fontWeight: active ? 600 : 400,
        fontFamily: T.systemFont,
        cursor: "pointer",
        borderBottom: `2px solid ${active ? T.cyan : "transparent"}`,
        marginBottom: -1,
      }}
    >
      {children}
    </button>
  );
}

// ─── shared layout helpers ────────────────────────────────


function ModalShell({ children, onClose }: { children: React.ReactNode; onClose: () => void }) {
  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.32)",
        backdropFilter: "blur(8px) saturate(150%)",
        WebkitBackdropFilter: "blur(8px) saturate(150%)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
        animation: "recmode-overlay-fade 200ms ease-out",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          position: "relative",
          background: T.bgSurface,
          border: `1px solid ${T.border}`,
          borderRadius: 14,
          padding: "28px 28px 20px",
          maxWidth: 440,
          width: "92%",
          color: T.text,
          boxShadow: T.shadow,
          fontFamily: T.systemFont,
          animation: "recmode-fade-in 240ms cubic-bezier(0.32, 0.72, 0, 1)",
        }}
      >
        {children}
      </div>
    </div>
  );
}


function OverlayShell({ children, tone }: { children: React.ReactNode; tone?: "error" }) {
  return (
    <div
      style={{
        position: "fixed",
        bottom: 16,
        right: 16,
        zIndex: 999,
        background: tone === "error" ? "var(--status-err-dim, rgba(248, 113, 113, 0.12))" : "var(--catfish-bg-elevated)",
        border: "1px solid " + (tone === "error" ? "var(--status-err)" : "var(--catfish-cyan)"),
        borderRadius: "var(--radius-md)",
        padding: "var(--space-4)",
        maxWidth: 380,
        boxShadow: "0 4px 12px rgba(0,0,0,0.2)",
        color: "var(--catfish-text)",
      }}
    >
      {children}
    </div>
  );
}


function btnStyle(kind: "primary" | "secondary", disabled?: boolean): React.CSSProperties {
  if (kind === "primary") {
    return {
      padding: "var(--space-2) var(--space-4)",
      border: "none",
      borderRadius: "var(--radius-sm)",
      background: disabled ? "var(--catfish-border)" : "var(--catfish-cyan)",
      color: "white",
      fontSize: 13,
      fontWeight: 500,
      cursor: disabled ? "default" : "pointer",
    };
  }
  return {
    padding: "var(--space-2) var(--space-4)",
    border: "1px solid var(--catfish-border)",
    borderRadius: "var(--radius-sm)",
    background: "transparent",
    color: "var(--catfish-text-muted)",
    fontSize: 13,
    cursor: disabled ? "default" : "pointer",
  };
}
