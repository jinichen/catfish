/** EduPopover — 📚 教学入口 popover (P3.5.167, 7/3 鸿波拍板 B 方案).
 *
 * 3 教学场景语义 unified 叠成 1 popover:
 *   🎓 教学模式  — BL-LEAN-SESSION (5/13) LEAN inject toggle. 说"我教你"
 *                触发 catfish_teach_start + freeze 流程 (BL-MM9-FREEZE-v2).
 *   🎬 录屏演示  — BL-LEARN-RECMODE Day 2 (5/14 #64) CDP 录屏 + speech.
 *                员工独立演示 → 事后 LLM 综合抽 skill.
 *   💡 让 AI 学  — hermes v0.18 /learn (P29 patch P3.5.168 7/3). 员工描述
 *                (paths / URLs / just did) → agent 用已有 tools gather →
 *                skill_manage 写 SKILL.md 到 ~/.hermes/skills/.
 *
 * 3 场景交互模式根本不同, 但语义都是"从员工行为 distill skill":
 *   🎓 是 toggle state (进入教学模式后续 message 都 LEAN inject)
 *   🎬 是 click-to-modal (setup 弹 modal → 开录制)
 *   💡 是 click-to-textarea-prefill (弹 "/learn " 到输入框员工继续输描述)
 *
 * 空间优化 (P3.5.167 鸿波"叠起来省空间"): 3 icon → 1 icon (📚). 未来加
 * kanban 学 / chat 历史学 / email 学等场景不再挤 button 栏, 塞进 popover.
 *
 * 破坏老员工 muscle memory 代价 (代码事实):
 *   老 🎓 (5/13 加) + 老 🎬 (5/14 加) 员工用 1-1.5 月 "1 click 触发". popover
 *   后变 "click 📚 → 选选项" 2 click. 鸿波拍板 B 接受此代价换长期扩展.
 *
 * 无引 UI lib (TaskPicker 5/22 comment: "不引 UI lib"). 自造 button + absolute
 * div + click-outside close, 复用 CSS var 系统 (--catfish-cyan 等).
 */

import { useEffect, useRef, useState } from "react";
import { useTeachingStore } from "../../../store/teaching";
import { useRecModeStore } from "../../../store/recmode";
import LearnModal from "./LearnModal";

interface Props {
  isStreaming: boolean;
  /** P3.5.170 (7/3 鸿波 catch UX 错): 员工点 💡 让 AI 学时不 prefill textarea
   *  显示 /learn 前缀 (违反员工主权军规, 暴露 slash 语法). 改弹 LearnModal, 员工
   *  输**纯描述** → modal 内部拼 "/learn <描述>" → 走此 callback → ChatInput
   *  onSend → hermes P29 patch translate.
   *
   *  参数 = 完整消息 (含 "/learn " 前缀), ChatInput 侧直接 onSend(text, []). */
  onStartLearn: (fullText: string) => void;
}

export default function EduPopover({ isStreaming, onStartLearn }: Props) {
  const [open, setOpen] = useState(false);
  // P3.5.170: LearnModal 独立 state, popover close 后 modal 独立管理生命周期.
  // 内嵌 useState (无独立 zustand store) — modal 只从 popover 触发, 无跨组件
  // 状态需求, 参 SetupModal 从 useRecModeStore 拿 setup state 的原因是跨
  // SetupModal / RecordingOverlay / PreviewBanner / ErrorBanner 4 modal 共享
  // 生命周期, LearnModal 无此需求, 内嵌 state 简化.
  const [learnOpen, setLearnOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // 老 store 复用 — 不 duplicate state
  const teachingOn = useTeachingStore((s) => s.on);
  const toggleTeaching = useTeachingStore((s) => s.toggle);
  const recModeState = useRecModeStore((s) => s.state);
  const openRecSetup = useRecModeStore((s) => s.openSetup);

  const recModeActive =
    recModeState === "recording" || recModeState === "analyzing";
  const anyEduActive = teachingOn || recModeActive;

  // Click-outside close (popover panel 外 mousedown → 关)
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  // Esc key 关 popover — 键盘友好
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open]);

  const handleTeaching = () => {
    toggleTeaching();
    setOpen(false);
  };

  const handleRecMode = () => {
    if (recModeActive) return; // 录制中不允许再开
    openRecSetup();
    setOpen(false);
  };

  const handleLearn = () => {
    // P3.5.170 (7/3 鸿波 catch): 弹 LearnModal 不 prefill textarea. 员工看不到
    // /learn slash 语法, 只看"想让 AI 学什么" 输入框. modal 内部拼
    // "/learn <描述>" → onStartLearn callback → ChatInput onSend.
    setLearnOpen(true);
    setOpen(false);
  };

  return (
    <div ref={containerRef} style={{ position: "relative" }}>
      <button
        onClick={() => setOpen((v) => !v)}
        disabled={isStreaming}
        title={
          anyEduActive
            ? teachingOn
              ? "📚 教学入口 · 🎓 教学模式 ON"
              : "📚 教学入口 · 🎬 录制中"
            : "📚 教学入口 — 让鲶鱼学新流程 (🎓 教学模式 / 🎬 录屏演示 / 💡 让 AI 学)"
        }
        style={{
          padding: "6px 10px",
          border:
            "1px solid " +
            (anyEduActive
              ? "var(--catfish-cyan)"
              : "var(--catfish-border)"),
          borderRadius: "var(--radius-sm)",
          background: anyEduActive
            ? "var(--catfish-cyan-dim)"
            : "transparent",
          color: anyEduActive
            ? "var(--catfish-cyan)"
            : "var(--catfish-text-muted)",
          fontSize: 14,
          fontWeight: anyEduActive ? 600 : 400,
          cursor: isStreaming ? "default" : "pointer",
          lineHeight: 1,
          minHeight: 36,
          transition: "all 120ms ease",
        }}
      >
        📚
      </button>

      {open && (
        <div
          role="menu"
          style={{
            position: "absolute",
            bottom: 44,
            left: 0,
            minWidth: 280,
            padding: 6,
            border: "1px solid var(--catfish-border)",
            borderRadius: "var(--radius-sm)",
            background: "var(--catfish-bg)",
            boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
            zIndex: 100,
            display: "flex",
            flexDirection: "column",
            gap: 2,
          }}
        >
          {/* 🎓 教学模式 (toggle) */}
          <EduOption
            emoji="🎓"
            label="教学模式"
            hint={
              teachingOn
                ? "已开启 · 点关闭"
                : "关掉 9 个干扰 inject, 说'我教你' 触发 catfish_teach_start"
            }
            active={teachingOn}
            onClick={handleTeaching}
          />

          {/* 🎬 录屏演示 (弹 modal) */}
          <EduOption
            emoji="🎬"
            label="录屏演示"
            hint={
              recModeActive
                ? "录制中 · 看右下角浮层"
                : "你演一遍 catfish 学 (CDP 录屏 + 语音)"
            }
            active={recModeActive}
            onClick={handleRecMode}
            disabled={recModeActive}
          />

          {/* 💡 让 AI 学 (prefill /learn) */}
          <EduOption
            emoji="💡"
            label="让 AI 学"
            hint={"输 '/learn <描述>' — AI 读代码/URL/上次操作抽 skill"}
            active={false}
            onClick={handleLearn}
          />

          {/* P3.5.170 (7/3 鸿波 catch): 删掉 popover 里 3 例子 (含 /learn 前缀,
              暴露 slash 语法违反员工主权). 3 例子改在 LearnModal 里显示 (纯描述
              无 /learn 前缀). popover 保持简洁, 只 3 option label + hint. */}
        </div>
      )}

      {/* P3.5.170: LearnModal 独立 render, popover close 后 modal 生命周期独立.
          onStart(description) → 内部拼 "/learn " → onStartLearn callback →
          ChatInput onSend → hermes 8642 → P29 patch translate. 员工全程看不到
          /learn slash 语法. */}
      {learnOpen && (
        <LearnModal
          onClose={() => setLearnOpen(false)}
          onStart={(description) => {
            const fullText = `/learn ${description}`;
            onStartLearn(fullText);
            setLearnOpen(false);
          }}
        />
      )}
    </div>
  );
}

// ─── 内部 sub-component EduOption ────────────────────────────────

interface EduOptionProps {
  emoji: string;
  label: string;
  hint: string;
  active: boolean;
  onClick: () => void;
  disabled?: boolean;
}

function EduOption({
  emoji,
  label,
  hint,
  active,
  onClick,
  disabled = false,
}: EduOptionProps) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 8,
        padding: "8px 10px",
        border: "none",
        borderRadius: "var(--radius-sm)",
        background: active
          ? "var(--catfish-cyan-dim)"
          : "transparent",
        color: active
          ? "var(--catfish-cyan)"
          : "var(--catfish-text)",
        cursor: disabled ? "not-allowed" : "pointer",
        textAlign: "left",
        opacity: disabled ? 0.5 : 1,
        transition: "background 120ms ease",
      }}
      onMouseEnter={(e) => {
        if (!disabled && !active) {
          (e.currentTarget as HTMLElement).style.background =
            "var(--catfish-bg-hover, rgba(255,255,255,0.05))";
        }
      }}
      onMouseLeave={(e) => {
        if (!active) {
          (e.currentTarget as HTMLElement).style.background = "transparent";
        }
      }}
    >
      <span style={{ fontSize: 16, lineHeight: 1.2 }}>{emoji}</span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            fontSize: 13,
            fontWeight: active ? 600 : 500,
            lineHeight: 1.3,
          }}
        >
          {label}
        </div>
        <div
          style={{
            fontSize: 11,
            color: "var(--catfish-text-muted)",
            lineHeight: 1.4,
            marginTop: 2,
          }}
        >
          {hint}
        </div>
      </div>
    </button>
  );
}
