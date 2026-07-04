/** LearnModal — 💡 让 AI 学 modal (P3.5.170, 7/3 鸿波 catch UX 错).
 *
 * 背景 (P3.5.170 军规自查):
 *   P3.5.167 EduPopover 老 UX 点 💡 → prefill textarea 成 "/learn " 前缀 →
 *   员工继续输描述. 严格违反"员工主权"军规: 员工点了按钮已表意图, 却仍看到
 *   /learn slash 语法 (hermes 内部约定, 非员工语义). 员工可能疑惑"为什么要写
 *   /learn?" — 暴露技术抽象.
 *
 * P3.5.170 修法 (A 方案 modal, 鸿波拍板):
 *   员工点 💡 → 弹本 modal → textarea 输**纯描述** (无 /learn 前缀) →
 *   "开始学" button 内部拼 /learn <描述> → onStart → ChatInput onSend →
 *   hermes 8642 → P29 patch translate → agent turn 拉 skill.
 *
 * 员工完全看不到 /learn 语法, 只看"想让 AI 学什么" 输入框 + 3 例子.
 *
 * UX 一致性: 参考 RecMode SetupModal (5/20 拆分), 复用 ModalShell + T theme
 * tokens + btnStyle helper. 员工 muscle memory 不破坏 (跟 RecMode 一样是 modal).
 *
 * 3 例子对齐 hermes v0.18 build_learn_prompt 3 场景 (agent/learn_prompt.py:5-10):
 *   dir 学 / URL 学 / just did 学.
 */

import { useState } from "react";
// P3.5.170: 复用 RecMode ModalShell + T theme tokens (UX 一致性). btnStyle 未用
// (本 modal 按钮内联 style 走 T 变量, 跟 SetupModal 保持一致模式).
import { ModalShell, T } from "../RecMode/shared";

interface Props {
  /** 员工点"开始学" → 传纯描述 (无 /learn 前缀). 上层 (EduPopover → ChatInput)
   *  拼 "/learn " + description → onSend → hermes P29 catch translate. */
  onStart: (description: string) => void;
  onClose: () => void;
}

/** 3 例子对齐 hermes agent/learn_prompt.py:5-10 描述 (dir / URL / "just did"). */
const LEARN_EXAMPLES: Array<{ label: string; text: string }> = [
  {
    label: "读代码目录",
    text: "读一下 ~/code/xxx 目录, 学一个 skill 出来",
  },
  {
    label: "抓 URL 学",
    text: "抓 https://docs.xxx.com/api, 学一个 skill",
  },
  {
    label: "学刚做的过程",
    text: "把我们刚才做的过程学成一个 skill",
  },
];

export default function LearnModal({ onStart, onClose }: Props) {
  const [description, setDescription] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [shake, setShake] = useState(false);
  const [closeHover, setCloseHover] = useState(false);
  const [submitHover, setSubmitHover] = useState(false);

  const trimmed = description.trim();
  const valid = trimmed.length > 0;

  function handleStart() {
    if (submitting) return;
    if (!valid) {
      // 空描述 — 抖一下 + focus (macOS 习惯, 参 SetupModal:44-51)
      setShake(true);
      setTimeout(() => setShake(false), 400);
      const ta = document.querySelector<HTMLTextAreaElement>(
        'textarea[data-learn-modal="1"]',
      );
      ta?.focus();
      return;
    }
    setSubmitting(true);
    // 内部拼 /learn <描述> — 员工看不到 /learn 语法.
    // 上层 EduPopover 桥接到 ChatInput onSend, hermes P29 patch catch 翻译.
    try {
      onStart(trimmed);
      // 不 reset description — onClose 关 modal 时 unmount, state 自动清.
    } finally {
      setSubmitting(false);
    }
  }

  function applyExample(idx: number) {
    setDescription(LEARN_EXAMPLES[idx].text);
  }

  return (
    <ModalShell onClose={onClose}>
      <style>{`
        @keyframes learn-shake {
          0%, 100% { transform: translateX(0); }
          25% { transform: translateX(-6px); }
          75% { transform: translateX(6px); }
        }
      `}</style>

      {/* 关闭按钮 (macOS NSCloseButton, 参 SetupModal:96-126) */}
      <button
        onClick={onClose}
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
          <path
            d="M1 1 L8 8 M8 1 L1 8"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
          />
        </svg>
      </button>

      {/* 视觉锚 — cyan 圆 + 白灯泡 (类 SetupModal mic badge) */}
      <div
        style={{
          width: 56,
          height: 56,
          borderRadius: 14,
          background: `linear-gradient(135deg, ${T.cyan}, ${T.cyanHover})`,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          marginBottom: 18,
          boxShadow: `0 6px 16px ${T.cyan}40`,
        }}
      >
        <svg
          width="28"
          height="28"
          viewBox="0 0 24 24"
          fill="none"
          stroke="white"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M9 18h6" />
          <path d="M10 22h4" />
          <path d="M12 2a7 7 0 0 0-4 12.7c.7.6 1 1.4 1 2.3v1h6v-1c0-.9.3-1.7 1-2.3A7 7 0 0 0 12 2z" />
        </svg>
      </div>

      <h3
        style={{
          margin: 0,
          fontSize: 19,
          fontWeight: 600,
          color: T.text,
          letterSpacing: "-0.015em",
          fontFamily: T.systemFont,
        }}
      >
        让 AI 学一个 skill
      </h3>
      <p
        style={{
          margin: "6px 0 0",
          fontSize: 13,
          color: T.textSecondary,
          lineHeight: 1.5,
          fontFamily: T.systemFont,
        }}
      >
        描述你想让 AI 学什么 —{" "}
        <strong style={{ color: T.text, fontWeight: 600 }}>
          代码目录 / 网页 URL / 刚才做过的事
        </strong>{" "}
        都行
      </p>

      {/* 主输入 textarea (跟 SetupModal input 风格一致) */}
      <div style={{ marginTop: 22 }}>
        <textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          onKeyDown={(e) => {
            // Cmd/Ctrl+Enter 提交 (textarea 常用快捷键, 员工可 shift+enter 换行)
            if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && !submitting) {
              e.preventDefault();
              handleStart();
            }
          }}
          placeholder="例: 读一下 ~/code/my-project 目录, 学一个 skill 出来"
          autoFocus
          rows={4}
          data-learn-modal="1"
          style={{
            width: "100%",
            padding: "11px 14px",
            border: `1px solid ${T.border}`,
            borderRadius: 8,
            background: T.bgInput,
            color: T.text,
            fontSize: 14,
            fontFamily: T.systemFont,
            outline: "none",
            boxSizing: "border-box",
            resize: "vertical",
            minHeight: 90,
            lineHeight: 1.5,
            transition: "border-color 120ms ease, box-shadow 120ms ease",
            animation: shake ? "learn-shake 0.4s ease" : undefined,
          }}
          onFocus={(e) => {
            e.currentTarget.style.borderColor = T.borderFocus;
            e.currentTarget.style.boxShadow = `0 0 0 3px ${T.cyan}25`;
          }}
          onBlur={(e) => {
            e.currentTarget.style.borderColor = T.border;
            e.currentTarget.style.boxShadow = "none";
          }}
        />
      </div>

      {/* 3 例子 inline 链接 (参 SetupModal:203-234) */}
      <div
        style={{
          marginTop: 12,
          fontSize: 12,
          color: T.textTertiary,
          fontFamily: T.systemFont,
          lineHeight: 1.6,
        }}
      >
        没思路?{" "}
        {LEARN_EXAMPLES.map((ex, i) => (
          <span key={i}>
            <button
              onClick={() => applyExample(i)}
              title={ex.text}
              style={{
                padding: 0,
                border: "none",
                background: "transparent",
                color: T.cyan,
                fontSize: 12,
                fontFamily: T.systemFont,
                cursor: "pointer",
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.color = T.cyanHover;
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.color = T.cyan;
              }}
            >
              {ex.label}
            </button>
            {i < LEARN_EXAMPLES.length - 1 && (
              <span style={{ color: T.textTertiary }}> · </span>
            )}
          </span>
        ))}
      </div>

      {/* 按钮 bar (参 SetupModal:313-362) */}
      <div
        style={{
          display: "flex",
          gap: 8,
          marginTop: 24,
          paddingTop: 18,
          borderTop: `1px solid ${T.border}`,
          alignItems: "center",
        }}
      >
        <button
          onClick={onClose}
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
          onClick={handleStart}
          onMouseEnter={() => setSubmitHover(true)}
          onMouseLeave={() => setSubmitHover(false)}
          disabled={submitting}
          style={{
            padding: "9px 22px",
            border: "none",
            borderRadius: 8,
            background: submitting
              ? T.textTertiary
              : submitHover
                ? T.cyanHover
                : T.cyan,
            color: "white",
            fontSize: 14,
            fontWeight: 600,
            fontFamily: T.systemFont,
            cursor: submitting ? "default" : "pointer",
            opacity: valid ? 1 : 0.7,
            transition: "background 120ms ease, opacity 120ms ease",
            boxShadow: valid ? `0 2px 6px ${T.cyan}40` : "none",
          }}
        >
          {submitting ? "启动中…" : "开始学 →"}
        </button>
      </div>

      {/* 快捷键 hint (macOS 习惯, 极淡) */}
      <div
        style={{
          marginTop: 10,
          fontSize: 11,
          color: T.textTertiary,
          fontFamily: T.systemFont,
          textAlign: "right",
        }}
      >
        ⌘ + Enter 提交 · Esc 关闭
      </div>
    </ModalShell>
  );
}
