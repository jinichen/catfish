/** 启动期诊断 —— 让白屏说话。
 *
 * # 为什么加这个 (8/4 鸿波 Windows 上启动后一片空白)
 *
 * 现场: mac 正常, Windows 装完启动是一整片白, 标题栏有"鲶鱼 Companion",
 * 窗口也开了 —— 但页面什么都没有, 零提示。
 *
 * 静态排查了一轮, 排除了 CSP、CI 没构建前端、当天改的 TS、bootstrapEndpoints
 * 抛异常、Windows 不启动后台服务。剩下两个可能 (bootstrapEndpoints **挂住**、
 * 渲染期抛错) **都看不见**:
 *
 *   · release 版 devtools 是关的 (lib.rs:484), 拿不到 console
 *   · main.tsx 只有 <React.StrictMode><App /></React.StrictMode>, **没有
 *     ErrorBoundary**
 *   · 前端一处 window.onerror / unhandledrejection 都没有
 *
 * 于是任何渲染期异常的表现就是 —— 一片白, 零信息。
 * **白屏是这个 UI 唯一的报错方式。**
 *
 * 这跟 8/3~8/4 查了两天的那些 bug 是同一个病的极端形态: 出事了不出声。
 * `--exclude` 不匹配静默成功、停止按钮少一句检查不报错、frontmatter 缺一行
 * `---` 照常返回一个 title=slug 的条目 —— 都是"什么都没发生"。
 * 白屏只是把它推到了极限: 连"发生了什么"这个问题本身都问不出口。
 *
 * # 这个文件做三件事
 *
 * 1. ErrorBoundary —— 渲染崩了显示错误 + 堆栈, 不是白屏
 * 2. 全局 window.onerror / unhandledrejection —— 兜住 React 之外的异常
 * 3. 兜底看门狗 —— 启动 8 秒后如果 #root 还是空的, 直接在页面上说出来
 *
 * 第 3 条是关键: 它连"代码根本没跑到"这种情况都能覆盖 —— 比如
 * bootstrapEndpoints 挂住导致 render 永远不执行。ErrorBoundary 抓不到"没发生
 * 的事", 看门狗可以。
 */

import React from "react";

const PANEL: React.CSSProperties = {
  position: "fixed",
  inset: 0,
  zIndex: 999999,
  background: "#fff",
  color: "#1a1a1a",
  padding: "24px 28px",
  overflow: "auto",
  font: "13px/1.6 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
};

const TITLE: React.CSSProperties = {
  font: "600 15px/1.5 ui-sans-serif, -apple-system, sans-serif",
  marginBottom: 10,
};

const HINT: React.CSSProperties = {
  font: "13px/1.7 ui-sans-serif, -apple-system, sans-serif",
  color: "#555",
  marginBottom: 16,
};

const PRE: React.CSSProperties = {
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
  background: "#f6f6f6",
  border: "1px solid #e0e0e0",
  borderRadius: 6,
  padding: "10px 12px",
  maxHeight: "50vh",
  overflow: "auto",
};

function LogPathHint() {
  return (
    <div style={HINT}>
      完整日志在：
      <br />
      Windows <code>%APPDATA%\com.catfish.companion\logs\</code>
      <br />
      macOS <code>~/Library/Logs/com.catfish.companion/</code>
      <br />
      把上面这段和日志一起发给我们，比截图有用得多。
    </div>
  );
}

/** 渲染期异常 —— 显示出来, 不要变成白屏。 */
export class StartupErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { error: Error | null; info: string }
> {
  constructor(props: { children: React.ReactNode }) {
    super(props);
    this.state = { error: null, info: "" };
  }

  static getDerivedStateFromError(error: Error) {
    return { error, info: "" };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    this.setState({ error, info: info.componentStack || "" });
    // 同时进 Rust 日志 (tauri-plugin-log 会落盘), 员工发日志时就带上了
    // eslint-disable-next-line no-console
    console.error("[startup] 渲染崩溃:", error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div style={PANEL}>
        <div style={TITLE}>界面没能启动起来</div>
        <LogPathHint />
        <pre style={PRE}>
          {this.state.error.message}
          {"\n\n"}
          {this.state.error.stack || ""}
          {this.state.info ? `\n\n组件栈:${this.state.info}` : ""}
        </pre>
      </div>
    );
  }
}

/** 在 #root 之外画一层, 用于 React 还没跑起来的情况。 */
function showOverlay(title: string, detail: string) {
  if (document.getElementById("catfish-startup-overlay")) return;
  const el = document.createElement("div");
  el.id = "catfish-startup-overlay";
  Object.assign(el.style, PANEL as unknown as CSSStyleDeclaration);
  el.innerHTML =
    `<div style="font:600 15px/1.5 ui-sans-serif,-apple-system,sans-serif;margin-bottom:10px"></div>` +
    `<div style="font:13px/1.7 ui-sans-serif,-apple-system,sans-serif;color:#555;margin-bottom:16px"></div>` +
    `<pre style="white-space:pre-wrap;word-break:break-word;background:#f6f6f6;` +
    `border:1px solid #e0e0e0;border-radius:6px;padding:10px 12px;max-height:50vh;overflow:auto"></pre>`;
  const [h, hint, pre] = Array.from(el.children) as HTMLElement[];
  h.textContent = title;
  hint.textContent =
    "完整日志: Windows %APPDATA%\\com.catfish.companion\\logs\\ · " +
    "macOS ~/Library/Logs/com.catfish.companion/";
  pre.textContent = detail;
  document.body.appendChild(el);
}

/** 装全局错误钩子 + 白屏看门狗。在 main.tsx 最早处调一次。 */
export function installStartupDiagnostics(): void {
  window.addEventListener("error", (e) => {
    // eslint-disable-next-line no-console
    console.error("[startup] window.onerror:", e.message, e.filename, e.lineno);
    showOverlay(
      "界面没能启动起来",
      `${e.message}\n${e.filename}:${e.lineno}:${e.colno}\n\n${e.error?.stack || ""}`,
    );
  });

  window.addEventListener("unhandledrejection", (e) => {
    // eslint-disable-next-line no-console
    console.error("[startup] unhandledrejection:", e.reason);
    const r = e.reason;
    showOverlay(
      "界面没能启动起来",
      typeof r === "string" ? r : `${r?.message || r}\n\n${r?.stack || ""}`,
    );
  });

  // 白屏看门狗 —— 这条才是覆盖"代码根本没跑到"的那一层。
  //
  // ErrorBoundary 只能抓**发生了的异常**。如果 main.tsx 里
  // `bootstrapEndpoints().finally(render)` 的 promise 永远不 settle, render 就
  // 永远不执行 —— 没有异常, 没有拒绝, 什么都没有, 就是一片白。这种情况只能靠
  // "过了这么久还是空的"来发现。
  //
  // 8 秒: 慢机器上首屏 2-3 秒够了, 8 秒还空基本可以断定不是慢, 是没跑。
  window.setTimeout(() => {
    const root = document.getElementById("root");
    if (root && root.childElementCount === 0) {
      showOverlay(
        "界面 8 秒没渲染出来",
        "#root 一直是空的 —— 说明 React 根本没开始渲染, 不是渲染中途出错。\n\n" +
          "最可能的原因: main.tsx 里 bootstrapEndpoints() 挂住了 (它内部有 4 个\n" +
          "await invoke(...), 一个超时都没有), 导致 .finally 里的 render 永远不执行。\n\n" +
          "请把日志目录里的最新 .log 发给我们。",
      );
    }
  }, 8000);
}
