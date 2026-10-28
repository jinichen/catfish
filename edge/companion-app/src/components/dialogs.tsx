/** 代替 window.confirm / alert / prompt 的对话框 (10/28 鸿波「改成 7 天保存, 切 tab 回来又变 90 天」)。
 *
 * Tauri 在 Mac 上的 WebView 不弹这三个原生对话框: confirm() 直接当「取消」、prompt() 当没填、
 * alert() 什么都不显示 —— 写 `if (!window.confirm(...)) return` 的地方点了按钮什么都不发生, 也没有提示。
 *
 * 用法跟原生的一样, 只是要 await:
 *
 *   if (!(await confirmDialog("确定删吗?"))) return;
 *   await alertDialog("打开失败: ...");
 *   const name = await promptDialog("叫什么?", "默认值");   // 取消 → null
 *
 * 对话框由 main.tsx 里挂的 <DialogHost /> 统一画 (一次只开一个, 后来的排队)。
 * 没挂 DialogHost 的环境 (单测) 退回原生的, 行为跟以前一样。
 */

import { useEffect, useState } from "react";

import ConfirmDialog from "./ConfirmDialog";

type Kind = "confirm" | "alert" | "prompt";

interface Request {
  kind: Kind;
  title: string;
  body: string;
  initial: string;
  resolve: (value: string | null) => void;
}

const queue: Request[] = [];
let notify: (() => void) | null = null;

function ask(kind: Kind, body: string, title: string, initial = ""): Promise<string | null> {
  if (!notify) {
    if (kind === "confirm") return Promise.resolve(window.confirm(body) ? "" : null);
    if (kind === "prompt") return Promise.resolve(window.prompt(body, initial));
    window.alert(body);
    return Promise.resolve("");
  }
  return new Promise((resolve) => {
    queue.push({ kind, title, body, initial, resolve });
    notify?.();
  });
}

/** 点「确认」→ true, 「取消」→ false */
export async function confirmDialog(body: string, title = "请确认"): Promise<boolean> {
  return (await ask("confirm", body, title)) !== null;
}

/** 只有「知道了」, 点了才 resolve */
export async function alertDialog(body: string, title = "提示"): Promise<void> {
  await ask("alert", body, title);
}

/** 填的内容; 点「取消」→ null (跟 window.prompt 一样) */
export function promptDialog(body: string, defaultValue = "", title = "请填写"): Promise<string | null> {
  return ask("prompt", body, title, defaultValue);
}

export function DialogHost() {
  const [current, setCurrent] = useState<Request | null>(null);
  const [input, setInput] = useState("");

  useEffect(() => {
    notify = () => setCurrent((cur) => cur ?? queue[0] ?? null);
    notify();
    return () => {
      notify = null;
    };
  }, []);
  useEffect(() => {
    if (current) setInput(current.initial);
  }, [current]);

  if (!current) return null;
  const close = (ok: boolean) => {
    queue.shift();
    current.resolve(ok ? (current.kind === "prompt" ? input : "") : null);
    setCurrent(queue[0] ?? null);
  };

  return (
    <ConfirmDialog
      open
      title={current.title}
      body={current.body}
      showCancel={current.kind !== "alert"}
      onConfirm={() => close(true)}
      onCancel={() => close(false)}
    >
      {current.kind === "prompt" && (
        <input
          autoFocus
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") close(true);
            if (e.key === "Escape") close(false);
          }}
          style={{ width: "100%", marginBottom: "var(--space-4)", padding: "4px 8px", boxSizing: "border-box" }}
        />
      )}
    </ConfirmDialog>
  );
}
