/** RecMode SetupModal — 抽自 RecModeButton.tsx (5/20 拆分).
 *
 * 录制前设置: skill 名 / 简述 / advanced (namespace / recordAudio).
 * 点 "开始" → POST /api/learn/start_recording (CDP) + 起 speech 录音.
 */

import { useState } from "react";
import { invoke } from "@tauri-apps/api/core";

import {
  useRecModeStore,
  isValidSkillTitle,
  RECMODE_EXAMPLES,
} from "../../../store/recmode";
import {
  newRecModeSessionId,
  startRecording as apiStartRecording,
} from "../../../lib/recmode";

import { ModalShell, T } from "./shared";


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
      // V2 #70: recordAudio 关时跳过 speech_start (隐私 / 没麦 场景)
      if (setup.recordAudio) {
        try {
          await invoke("speech_start_recording");
        } catch (e) {
          console.warn("[recmode] speech_start_recording 失败 (继续, 没语音):", e);
        }
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
          <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 12 }}>
            <div>
              <label style={{ fontSize: 11, color: T.textSecondary, display: "block", marginBottom: 4, fontFamily: T.systemFont }}>
                共享范围
              </label>
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
            {/* V2 #70: 录音 toggle */}
            <label style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              fontSize: 12,
              color: T.text,
              fontFamily: T.systemFont,
              cursor: "pointer",
              userSelect: "none",
            }}>
              <input
                type="checkbox"
                checked={setup.recordAudio}
                onChange={(e) => setSetup({ recordAudio: e.target.checked })}
                style={{ accentColor: T.cyan, cursor: "pointer" }}
              />
              <span>同时录音 (顺嘴说意图, 鲶鱼综合质量更高)</span>
            </label>
            {!setup.recordAudio && (
              <div style={{
                fontSize: 11,
                color: T.textTertiary,
                marginLeft: 22,
                fontFamily: T.systemFont,
                lineHeight: 1.5,
              }}>
                ⚠ 关录音 = 鲶鱼只看操作 + 截图猜意图, 综合 SKILL.md 较机械.
                隐私场景 / 没麦克风 / 嘈杂环境 才关.
              </div>
            )}
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
export default SetupModal;
