/** 应用内确认对话框 (7/30 五改).
 *
 * ## 为什么不用 window.confirm / window.prompt
 *
 * 全仓有 21 处在用浏览器原生的 confirm / prompt / alert。它们的问题不只是
 * 长得跟界面两套:
 *
 *   · **样式完全不受控** —— 位置、字体、按钮文案(「确定/取消」)、按钮顺序
 *     全由浏览器决定, 中英文环境下还不一样
 *   · **会被浏览器静音** —— 用户勾过"阻止此页面创建更多对话框"之后, 后续
 *     confirm 一律返回 false、prompt 一律返回 null。表现是"点删除没反应",
 *     而代码里看不出任何异常
 *   · **阻塞主线程**, 期间页面完全冻结
 *   · **放不下东西** —— 只能纯文本, 没法强调后果、没法给徽章或链接
 *
 * ## 输错时必须说出来
 *
 * 模型页原来的写法是:
 *
 *     const typed = window.prompt("确认请输入模型 ID: " + m.name);
 *     if (typed !== m.name) return;         // ← 输错了就静默什么也不做
 *
 * 输错一个字母, 点「确定」, **页面毫无反应** —— 没有提示、没有报错, 看起来
 * 像是删除功能坏了。这里改成: 输入不匹配时确认按钮就是禁用的, 旁边还写着
 * 差在哪。让人在点之前就知道点不动, 而不是点完了猜。
 */

import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import { BTN, BTN_DANGER, BTN_PRIMARY } from "./DataTable";

export function ConfirmDialog({
  title,
  children,
  confirmLabel = "确定",
  danger = false,
  requireText,
  busy = false,
  onConfirm,
  onCancel,
}: {
  title: string;
  /** 正文. 可以放粗体、代码、列表 —— 这正是原生 confirm 做不到的 */
  children?: ReactNode;
  confirmLabel?: string;
  /** 红色确认按钮. 删除这类不可逆操作用 */
  danger?: boolean;
  /** 要求原样输入这段文字才能确认 (删模型这种"手滑代价很大"的操作用).
   *  不传则只需点一下。 */
  requireText?: string;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const [typed, setTyped] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const ok = requireText == null || typed === requireText;

  useEffect(() => {
    // 打开时焦点进对话框, 关闭时还回去 —— 不还的话焦点会落回 body,
    // 键盘用户得从页面顶部重新 Tab 一遍。
    const prev = document.activeElement as HTMLElement | null;
    (inputRef.current ?? confirmRef.current)?.focus();
    return () => prev?.focus?.();
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onCancel();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onCancel]);

  return (
    <div
      onMouseDown={(e) => {
        // 只在真的点在遮罩上时才关 —— 在面板里按下、拖到遮罩上才松手
        // (选中文字时很常见) 不该被当成"取消"。
        if (e.target === e.currentTarget) onCancel();
      }}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0, 0, 0, 0.35)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
        padding: 16,
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        style={{
          background: "var(--bg-elev)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius-md)",
          padding: 16,
          width: "min(440px, 100%)",
          boxShadow: "0 8px 32px rgba(0, 0, 0, 0.18)",
        }}
      >
        <h3 id={titleId} style={{ margin: 0, fontSize: 14, fontWeight: 600 }}>
          {title}
        </h3>

        {children ? (
          <div
            style={{
              fontSize: 12,
              lineHeight: 1.7,
              color: "var(--text-muted)",
              marginTop: 8,
            }}
          >
            {children}
          </div>
        ) : null}

        {requireText != null ? (
          <div style={{ marginTop: 10 }}>
            <label style={{ fontSize: 11, color: "var(--text-muted)", display: "block" }}>
              确认请输入 <code style={{ color: "var(--text)" }}>{requireText}</code>
            </label>
            <input
              ref={inputRef}
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && ok && !busy) onConfirm();
              }}
              autoComplete="off"
              spellCheck={false}
              style={{
                width: "100%",
                boxSizing: "border-box",
                marginTop: 4,
                padding: "4px 8px",
                fontFamily: "var(--font-mono)",
                fontSize: 12,
                border: `1px solid ${
                  typed && !ok ? "var(--status-warn)" : "var(--border)"
                }`,
                borderRadius: 3,
                background: "var(--bg)",
                color: "var(--text)",
              }}
            />
            {/* 输错时说出来。原来是静默 return, 点了没反应看起来像功能坏了。 */}
            {typed && !ok ? (
              <div style={{ fontSize: 11, color: "var(--status-warn)", marginTop: 3 }}>
                跟上面不一致，确认按钮点不动。
              </div>
            ) : null}
          </div>
        ) : null}

        <div
          style={{
            display: "flex",
            justifyContent: "flex-end",
            gap: 8,
            marginTop: 14,
          }}
        >
          <button style={BTN} onClick={onCancel} disabled={busy}>
            取消
          </button>
          <button
            ref={confirmRef}
            style={{
              ...(danger ? BTN_DANGER : BTN_PRIMARY),
              ...(ok && !busy ? null : { opacity: 0.45, cursor: "not-allowed" }),
            }}
            disabled={!ok || busy}
            onClick={onConfirm}
          >
            {busy ? "处理中…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
