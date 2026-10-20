/** EduPopover — 📚 教学入口 popover (P3.5.167, 7/3 鸿波拍板 B 方案).
 *
 * 两项:
 *   🎓 教学模式  — BL-LEAN-SESSION (5/13) LEAN inject toggle. 说"我教你"
 *                触发 catfish_teach_start + freeze 流程 (BL-MM9-FREEZE-v2).
 *   登录密码     — 8/18, 教学用的站点密码 (改密码 / 补入口)。
 *
 * 10/20: 原来还有 🎬 录屏演示 (RecMode, CDP 录屏 + 事后综合) 和 💡 让 AI 学
 * (hermes /learn)。鸿波 10/20 定: 两者 mac / Windows 都从没用过, 拿掉。
 * 教学走「我教你 → 凝固 → 再跑」这一条。
 *
 * 空间优化 (P3.5.167 鸿波"叠起来省空间"): 未来加别的学习场景塞进 popover,
 * 不挤 button 栏。
 *
 * 无引 UI lib (TaskPicker 5/22 comment: "不引 UI lib"). 自造 button + absolute
 * div + click-outside close, 复用 CSS var 系统 (--catfish-cyan 等).
 */

import { useEffect, useRef, useState } from "react";
import type { Icon } from "@phosphor-icons/react";
import { BookOpenText, GraduationCap } from "@phosphor-icons/react";
import { useTeachingStore } from "../../../store/teaching";
import CredentialModal from "./CredentialModal";

interface Props {
  isStreaming: boolean;
}

export default function EduPopover({ isStreaming }: Props) {
  const [open, setOpen] = useState(false);
  const [credentialOpen, setCredentialOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // 老 store 复用 — 不 duplicate state
  const teachingOn = useTeachingStore((s) => s.on);
  const toggleTeaching = useTeachingStore((s) => s.toggle);

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

  return (
    <div ref={containerRef} className="chat-composer__popover-anchor">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={isStreaming}
        aria-haspopup="menu"
        aria-expanded={open}
        className="chat-composer__tool-button chat-composer__tool-button--advanced"
        data-active={teachingOn || undefined}
        title={
          teachingOn
            ? "📚 教学入口 · 🎓 教学模式 ON"
            : "📚 教学入口 — 让鲶鱼学新流程 (🎓 教学模式 / 登录密码)"
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
          {/* P3.5.171 (7/3 鸿波 catch): hint 员工语义化, 去技术抽象 —— 只说
              "员工做什么" + "鲶鱼做什么", 不露内部术语. */}

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

          {/* 8/18 改成**改密码**入口。
              第一次存密码不再走这儿 —— 教学过程中遇到要密码时就地弹框存
              (InlineCredentialPrompt), 站点自动绑当前页。这里留给"以后改密码"
              和"补个入口": 改密码不用重新教一遍, 因为 resolve_secret 每次都
              现查钥匙串 (没有缓存), 改完下一次跑就是新的。 */}
          <EduOption
            icon={BookOpenText}
            label="登录密码"
            hint="改密码 / 加入口 — 改完不用重新教一遍"
            active={false}
            onClick={() => {
              setCredentialOpen(true);
              setOpen(false);
            }}
          />
        </div>
      )}

      {credentialOpen && (
        <CredentialModal onClose={() => setCredentialOpen(false)} />
      )}
    </div>
  );
}

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
          ? "var(--catfish-cyan-tint)"
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
