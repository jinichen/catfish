/** BL-LEARN-RECMODE Companion UI (5/14 Day 2 #64).
 *
 * 🎙 RecMode 按钮 + setup 模态 + 录制中浮层. 状态机走 store/recmode.ts
 * (idle / setup / recording / analyzing / preview / error).
 *
 * 5/20 拆分 1217 → 73 行: 子组件抽到 RecMode/ 目录
 * (SetupModal / RecordingOverlay / ErrorBanner / PreviewBanner / shared).
 *
 * 流程 (用户视角):
 *   点 🎙 → setup 模态 (填名字 / namespace / 简述)
 *   点 "开始录屏 + 录音" → POST /api/learn/start_recording (CDP listener)
 *                       → speech_start_recording (ffmpeg 录音)
 *                       → recording 状态 (浮层 ⏱ + 提示)
 *   操作 Catfish Chrome 演示 + 顺嘴说意图
 *   点 "✅ 完成教学" → speech_stop_and_transcribe + stop_recording
 *                   → analyzing → analyze (main 综合) → preview
 *   preview: SKILL.md + main.py + 三按钮 (跑 / 保存 / 重录)
 */

import { useRecModeStore } from "../../store/recmode";

// 5/23 鸿波: T (cyan/textSecondary 常量) 不再用 — RecModeToolbarButton 改用 CSS
// 变量 var(--catfish-*) 跟 TeachingToggleButton 风格统一. T 之前唯一 caller 是
// 这个按钮, 现在 import 移除 (tsc noUnusedLocals 卡 build).
import SetupModal from "./RecMode/SetupModal";
import RecordingOverlay from "./RecMode/RecordingOverlay";
import ErrorBanner from "./RecMode/ErrorBanner";
import PreviewBanner from "./RecMode/PreviewBanner";

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
      title={isActive ? "录制中, 看右下角浮层" : "🎬 教鲶鱼一遍 (RecMode 录屏+录音)"}
      style={{
        // 5/23 鸿波: 之前 border: "none" + transparent, 跟旁边 📎 / 🎓 / 🎤
        // 等带框按钮风格不一致, 视觉上像散落. 跟 TeachingToggleButton 对齐:
        // 1px border + radius-sm + active 时变 cyan + cyan-dim 背景.
        padding: "6px 10px",
        border: "1px solid " + (isActive ? "var(--catfish-cyan)" : "var(--catfish-border)"),
        borderRadius: "var(--radius-sm)",
        background: isActive ? "var(--catfish-cyan-dim)" : "transparent",
        color: isActive ? "var(--catfish-cyan)" : "var(--catfish-text-muted)",
        fontSize: 14,
        fontWeight: isActive ? 600 : 400,
        cursor: disabled || isActive ? "not-allowed" : "pointer",
        opacity: disabled ? 0.5 : 1,
        lineHeight: 1,
        minHeight: 36,
        transition: "all 120ms ease",
      }}
    >
      {/* 5/23 鸿波: 🎙 → 🎬 — 跟旁边普通 🎤 (语音输入) 拉开视觉, 员工不再分不清.
       *  RecMode 是"你演一遍 catfish 学" 场景, 拍版 emoji 比麦克风更贴语义. */}
      🎬
    </button>
  );
}
