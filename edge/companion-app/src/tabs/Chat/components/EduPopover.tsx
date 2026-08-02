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
 * 真边界 = **时机 + 输入源** (7/27 鸿波 catch "跟教学模式是不是重复"):
 *   🎓 🎬 **事前**开 toggle / 录制, 边做边采 (行为流)
 *   💡    **事后**补学 — 给静态资料, 或让 agent 回看**已发生**的对话
 * hermes learn_prompt.py:5-13 列 4 种 /learn 输入源: 代码目录 / API doc URL /
 * "workflow they just walked the agent through in this conversation" / 粘贴笔记.
 * 第 3 种跟 🎓 内容像但**时机相反** (回溯 vs 实时), hint 文案必须标出"事后",
 * 否则员工看不出跟教学模式的区别 (老 hint "或刚才做的事" 就没标, 7/27 改).
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
import type { CSSProperties } from "react";
import type { Icon } from "@phosphor-icons/react";
import { BookOpenText, GraduationCap, Lightbulb, VideoCamera } from "@phosphor-icons/react";
import { useTeachingStore } from "../../../store/teaching";
import { useRecModeStore } from "../../../store/recmode";
import { saveTeachingCredential } from "../../../lib/tauri";
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
  const [credentialOpen, setCredentialOpen] = useState(false);
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
    <div ref={containerRef} className="chat-composer__popover-anchor">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={isStreaming}
        aria-haspopup="menu"
        aria-expanded={open}
        className="chat-composer__tool-button chat-composer__tool-button--advanced"
        data-active={anyEduActive || undefined}
        title={
          anyEduActive
            ? teachingOn
              ? "📚 教学入口 · 🎓 教学模式 ON"
              : "📚 教学入口 · 🎬 录制中"
            : "📚 教学入口 — 让鲶鱼学新流程 (🎓 教学模式 / 🎬 录屏演示 / 💡 让 AI 学)"
        }
      >
        <BookOpenText size={18} aria-hidden="true" />
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
          {/* P3.5.171 (7/3 鸿波 catch): 3 hint 员工语义化, 去技术抽象.
              老 hint 泄露 "inject / catfish_teach_start / CDP 录屏 / /learn 语法"
              — 员工不懂内部术语, 违反员工主权军规. 新 hint 只说"员工做什么"
              + "鲶鱼做什么", 一句话讲清 3 场景差异. */}

          {/* 🎓 教学模式 (toggle) */}
          <EduOption
            icon={GraduationCap}
            label="教学模式"
            hint={
              teachingOn
                ? "已开启 · 点关闭"
                : "跟着我做 — 我一步步教鲶鱼跑新流程"
            }
            active={teachingOn}
            onClick={handleTeaching}
          />

          {/* 🎬 录屏演示 (弹 modal) */}
          <EduOption
            icon={VideoCamera}
            label="录屏演示"
            hint={
              recModeActive
                ? "录制中 · 看右下角浮层"
                : "你演一遍 — 鲶鱼看你怎么操作, 事后自学"
            }
            active={recModeActive}
            onClick={handleRecMode}
            disabled={recModeActive}
          />

          {/* 💡 让 AI 学 (弹 LearnModal)
              7/27 鸿波 catch "跟教学模式是不是重复": 三者语义都是 distill skill,
              但真差异是**时机 + 输入源**, 老 hint "或刚才做的事" 没体现出来:
                🎓 🎬 = 事前开 toggle/录制, 边做边采
                💡    = 事后补学 (给资料, 或让 agent 回看已发生的对话)
              hermes learn_prompt.py:5-13 明确列 4 种输入源 (dir / URL /
              "workflow they just walked the agent through in this conversation"
              / pasted notes), 所以"回看对话"是真能力不能删, 只是要标清是**事后**. */}
          <EduOption
            icon={Lightbulb}
            label="让 AI 学"
            hint={"事后补学 — 给资料 (代码/网页) 或让它回看这段对话"}
            active={false}
            onClick={handleLearn}
          />

          <EduOption
            icon={BookOpenText}
            label="保存登录密码"
            hint="保存到本机系统凭据库，教学时只使用安全引用"
            active={false}
            onClick={() => {
              setCredentialOpen(true);
              setOpen(false);
            }}
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
      {credentialOpen && (
        <CredentialModal onClose={() => setCredentialOpen(false)} />
      )}
    </div>
  );
}

function CredentialModal({ onClose }: { onClose: () => void }) {
  const [label, setLabel] = useState("");
  const [password, setPassword] = useState("");
  const [reference, setReference] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  const save = async () => {
    setError("");
    setSaving(true);
    try {
      setReference(await saveTeachingCredential(label, password));
      setPassword("");
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={modalBackdropStyle} role="dialog" aria-modal="true" aria-label="保存登录密码">
      <div style={credentialModalStyle}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <strong>保存登录密码</strong>
          <button type="button" onClick={onClose} style={modalCloseStyle}>关闭</button>
        </div>
        <p style={modalHintStyle}>密码只保存在本机系统凭据库，不会写入聊天、配置文件或终端。</p>
        <label style={fieldLabelStyle}>名称（例如：教学网站）
          <input value={label} onChange={(e) => setLabel(e.target.value)} autoFocus style={fieldStyle} />
        </label>
        <label style={fieldLabelStyle}>密码
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} style={fieldStyle} />
        </label>
        {error && <div style={{ color: "var(--catfish-danger, #c0392b)", fontSize: 12 }}>{error}</div>}
        {reference && (
          <div style={{ padding: 8, borderRadius: 6, background: "var(--catfish-bg-hover)" }}>
            <div style={modalHintStyle}>已保存。教学流程使用这个安全引用：</div>
            <code style={{ wordBreak: "break-all", fontSize: 12 }}>{reference}</code>
            <button type="button" onClick={() => navigator.clipboard?.writeText(reference)} style={{ ...modalCloseStyle, marginTop: 6 }}>复制引用</button>
          </div>
        )}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <button type="button" onClick={onClose} style={modalCloseStyle}>取消</button>
          <button type="button" disabled={saving || !label.trim() || !password} onClick={save} style={saveButtonStyle}>{saving ? "保存中…" : "保存到系统凭据库"}</button>
        </div>
      </div>
    </div>
  );
}

const modalBackdropStyle: CSSProperties = { position: "fixed", inset: 0, zIndex: 1000, display: "grid", placeItems: "center", background: "rgba(0,0,0,.28)" };
const credentialModalStyle: CSSProperties = { width: "min(420px, calc(100vw - 32px))", display: "flex", flexDirection: "column", gap: 12, padding: 20, borderRadius: 12, background: "var(--catfish-bg)", boxShadow: "0 12px 40px rgba(0,0,0,.25)" };
const modalHintStyle: CSSProperties = { margin: 0, color: "var(--catfish-text-muted)", fontSize: 12, lineHeight: 1.5 };
const fieldLabelStyle: CSSProperties = { display: "flex", flexDirection: "column", gap: 5, fontSize: 12, fontWeight: 600 };
const fieldStyle: CSSProperties = { padding: "8px 10px", border: "1px solid var(--catfish-border)", borderRadius: 6, background: "transparent", color: "var(--catfish-text)" };
const modalCloseStyle: CSSProperties = { padding: "6px 10px", border: "1px solid var(--catfish-border)", borderRadius: 6, background: "transparent", color: "var(--catfish-text)", cursor: "pointer" };
const saveButtonStyle: CSSProperties = { ...modalCloseStyle, borderColor: "var(--catfish-cyan)", background: "var(--catfish-cyan)", color: "white" };

// ─── 内部 sub-component EduOption ────────────────────────────────

interface EduOptionProps {
  icon: Icon;
  label: string;
  hint: string;
  active: boolean;
  onClick: () => void;
  disabled?: boolean;
}

function EduOption({
  icon: IconComponent,
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
      <IconComponent size={18} style={{ flexShrink: 0, marginTop: 1 }} aria-hidden="true" />
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
