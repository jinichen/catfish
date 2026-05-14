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
  const [, setTick] = useState(0);  // 仅强制 re-render 跑 elapsed 计时, value 不读

  // 1 秒一刷计时
  useEffect(() => {
    if (state !== "recording") return;
    const t = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [state]);

  async function onFinish() {
    if (!sessionId || submitting) return;
    setSubmitting(true);
    try {
      // 停 ffmpeg 录音 + 转写 (复用 BL-VOICE3 — 转写写到 ~/.catfish/recordings/<sid>/transcripts.jsonl)
      try {
        await invoke<string>("speech_stop_and_transcribe");
        // 注: BL-VOICE3 现状返字符串到调用方, 不直接写文件 — 5/26 真做 Day 4
        // 联调时, 看是不是需要 RecMode 这边主动写 transcripts.jsonl. 现在
        // 先不阻塞, 让 aggregator load_recording_inputs 容忍 transcripts 缺失.
      } catch (e) {
        console.warn("[recmode] speech_stop 失败 (继续, 没语音):", e);
      }
      // 停 CDP listener (flush events.jsonl + meta.json)
      await apiStopRecording(sessionId);
      // 触发 analyze (调 main 综合)
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
            {sessionId && <> · session <code>{sessionId}</code></>}
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
  const reset = useRecModeStore((s) => s.reset);
  return (
    <OverlayShell tone="error">
      <div style={{ fontSize: 14, fontWeight: 600, marginBottom: "var(--space-2)" }}>
        ⚠ RecMode 错
      </div>
      <div style={{ fontSize: 13, marginBottom: "var(--space-3)" }}>{errorMessage}</div>
      <button onClick={reset} style={btnStyle("primary")}>
        知道了
      </button>
    </OverlayShell>
  );
}


function PreviewBanner() {
  const preview = useRecModeStore((s) => s.preview);
  const reset = useRecModeStore((s) => s.reset);
  if (!preview) return null;
  return (
    <OverlayShell>
      <div style={{ fontSize: 14, fontWeight: 600, marginBottom: "var(--space-2)" }}>
        ✅ Skill 已生成
      </div>
      <div style={{ fontSize: 13, lineHeight: 1.5, marginBottom: "var(--space-3)" }}>
        <strong>{preview.namespace}/{preview.skill_name}</strong>
        <br />
        {preview.steps_count} 步, 自评 confidence {(preview.confidence * 100).toFixed(0)}%
        <br />
        <span style={{ color: "var(--catfish-text-muted)", fontSize: 12 }}>
          落档 {preview.skill_dir}
        </span>
        {preview.questions_for_user.length > 0 && (
          <ul style={{ marginTop: "var(--space-2)", paddingLeft: 16, fontSize: 12 }}>
            {preview.questions_for_user.map((q, i) => (
              <li key={i} style={{ color: "var(--status-warn)" }}>{q}</li>
            ))}
          </ul>
        )}
      </div>
      <div style={{ fontSize: 11, color: "var(--catfish-text-muted)", marginBottom: "var(--space-3)" }}>
        Day 3 (5/28) 加 "跑一次试" + "重录" 按钮. 现在直接关.
      </div>
      <button onClick={reset} style={btnStyle("primary")}>
        关闭
      </button>
    </OverlayShell>
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
