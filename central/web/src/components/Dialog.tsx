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
  confirmDisabled = false,
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
  /** 额外的"还不能确认"条件 —— 正文里放了自己的表单时用 (比如密码没填够长度)。
   *
   * ⚠ 不传的话, 调用方只能在 onConfirm 里 `if (...) return`, 而那是
   * **点了没反应** —— 正是这个文件开头骂的那个反模式。8/1 用户页的
   * 重置密码就踩了: 空密码点「重置」什么都不发生, 连红字都不出。 */
  confirmDisabled?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const [typed, setTyped] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  // trim 再比。要输的东西通常是从界面上复制来的 (版本号 / 模型名),
  // 而复制经常会带一个尾空格 —— 那时界面上看起来一模一样, 按钮却是灰的,
  // 没有任何东西能提示"你多了个空格"。这一条不放松安全性: 中间的字符
  // 仍然要完全一致。
  const ok =
    (requireText == null || typed.trim() === requireText) && !confirmDisabled;

  useEffect(() => {
    // 打开时焦点进对话框, 关闭时还回去 —— 不还的话焦点会落回 body,
    // 键盘用户得从页面顶部重新 Tab 一遍。
    const prev = document.activeElement as HTMLElement | null;
    // 正文里如果有自己的输入框 (比如重置密码那个), 焦点该进它, 而不是
    // 停在确认按钮上 —— 否则打开对话框直接敲字是敲了个寂寞, 而按 Enter
    // 会直接触发确认。requireText 的输入框优先, 其次正文里第一个 input。
    const bodyInput = bodyRef.current?.querySelector<HTMLElement>(
      "input, textarea, select",
    );
    (inputRef.current ?? bodyInput ?? confirmRef.current)?.focus();
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
            ref={bodyRef}
            style={{
              fontSize: 12,
              lineHeight: 1.7,
              color: "var(--text-muted)",
              marginTop: 8,
            }}
          >
            {/* 正文里按 Enter 也提交。原生 prompt() 是 Enter 提交的, 换成
                对话框之后如果只能用鼠标点, 是手感退化。
                ok 为 false 时不提交 —— 跟按钮的 disabled 同一个条件。 */}
            <div
              onKeyDown={(e) => {
                if (e.key !== "Enter") return;
                const t = e.target as HTMLElement;
                if (t.tagName === "TEXTAREA") return; // 多行里 Enter 是换行
                e.preventDefault();
                if (ok && !busy) onConfirm();
              }}
            >
              {children}
            </div>
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
