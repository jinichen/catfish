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
      Windows <code>%LOCALAPPDATA%\com.catfish.companion\logs\</code>
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
    // ⚠ console.* **不会**进日志文件。
    //
    // 我原来在这行写着"同时进 Rust 日志 (tauri-plugin-log 会落盘)" —— 假的,
    // 8/5 查证: 前端既没装 @tauri-apps/plugin-log 也没调 attachConsole(),
    // Rust 侧 log plugin 只有 LogDir + Stderr 两个 target, 收的是 **Rust** 的
    // 日志, 不是 webview 的 console。release 版 devtools 又是关的 (lib.rs:484),
    // 所以这一句实际上是喊给空气听。
    //
    // 员工唯一能看到的是**画在页面上**的那块面板 —— 所以出错信息必须进 DOM,
    // 不能只靠 console。下面的 render() 就是干这个的。
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
          {oversizedReport()}
        </pre>
      </div>
    );
  }
}

/** 在 #root 之外画一层, 用于 React 还没跑起来的情况。 */
function showOverlay(title: string, detail: string) {
  // 没有 DOM 时静默返回 —— 这个模块会被非浏览器环境 import (vitest node env),
  // 诊断代码自己把宿主炸掉是最没道理的一种失败。
  if (typeof document === "undefined") return;
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
    "完整日志: Windows %LOCALAPPDATA%\\com.catfish.companion\\logs\\ · " +
    "macOS ~/Library/Logs/com.catfish.companion/";
  pre.textContent = detail + oversizedReport();
  document.body.appendChild(el);
}

/** 把记下来的超大 invoke 拼成一段, 附在任何一块错误面板末尾。
 *
 * 真凶不一定是抛异常的那个 invoke —— 也可能是它前面某个把内存撑爆的。
 * 所以面板上要能看到"在这之前谁发过巨型载荷"。
 */
function oversizedReport(): string {
  if (oversized.length === 0) return "";
  return `\n\n── 此前的超大 IPC 载荷 (>8MB) ──\n${oversized.join("\n")}`;
}

/** IPC 载荷体检 —— 出事时说清楚是哪个 invoke、多大。
 *
 * # 为什么需要 (8/5 Windows 实测)
 *
 * 加了错误钩子之后, Windows 白屏终于说话了:
 *
 *     Failed to execute 'postMessage' on 'EmbeddedBrowserWebView':
 *     Invalid string length
 *     RangeError: Invalid string length
 *         at Object.postMessage (<anonymous>:1:102)
 *         at sendIpcMessage (<anonymous>:130:18)
 *
 * Tauri v2 在 Windows 上用 WebView2 的 postMessage 做 IPC 请求方向, mac 走另一套
 * 传输 —— 所以同一份前端代码只有 Windows 炸。`Invalid string length` 是 V8 在
 * 字符串超上限 (~512M 字符) 时抛的, 也就是**某个 invoke 的载荷大到离谱**。
 *
 * 但那个栈里**零个应用帧**, 全是 Tauri 注入的匿名脚本 —— 光看栈根本不知道是哪个
 * 命令。读代码也定位不了 (invoke 调用点几十处, 谁在启动路径上带大载荷不明显)。
 *
 * 所以包一层: 记下命令名和载荷大小, 超阈值就 warn, 抛错就把命令名一起报出来。
 * 下一个包会直接告诉我们是谁, 不用再猜。
 *
 * 阈值 8MB: 正常的 invoke 载荷是 KB 级; 真要传大文件也该走别的通道而不是
 * postMessage。8MB 远低于 V8 上限, 但足够高到不会误报日常调用。
 */
const IPC_WARN_BYTES = 8 * 1024 * 1024;

/** 超阈值的 invoke 记在这里, 出事时一并画进面板。
 *
 * 8/5 补的漏: 原来"载荷过大"只 console.warn。而 release 版 devtools 是关的
 * (lib.rs:484), 前端的 console 又**不进日志文件** (没装 plugin-log, 没调
 * attachConsole, Rust 侧 log plugin 收的是 Rust 自己的日志) —— 也就是说那条
 * 告警是喊给空气听的。
 *
 * 于是会漏掉最难查的一种情况: 某个 invoke 载荷巨大**但没抛异常**, 它把内存
 * 撑到下一个 invoke 才炸。真凶的名字在 warn 里, 而 warn 谁也看不见。
 *
 * 改成记在内存里, 面板出现时连着一起显示 —— 不引新依赖, 不多走一次 IPC
 * (为了报告 IPC 问题而再发一次 IPC, 是能把现场彻底搅浑的那种做法)。
 */
const oversized: string[] = [];
const OVERSIZED_KEEP = 20;

/** 单独导出给测试用 —— 它不碰 DOM, 不必为它引一个 jsdom 依赖。 */
export function installIpcSizeGuard(): void {
  const internals = (window as unknown as Record<string, any>).__TAURI_INTERNALS__;
  if (!internals || typeof internals.invoke !== "function") {
    // 非 Tauri 环境 (vitest / storybook) 或 Tauri 换了内部结构 —— 静默跳过,
    // 这只是诊断, 不该因为它拖垮启动。
    return;
  }
  const original = internals.invoke.bind(internals);
  const wrapped = (cmd: string, args?: unknown, opts?: unknown) => {
    let size = -1;
    try {
      size = args === undefined ? 0 : JSON.stringify(args).length;
    } catch {
      /* 序列化不了 (循环引用等) —— 交给原函数去报真正的错 */
    }
    if (size > IPC_WARN_BYTES) {
      const line = `invoke("${cmd}") 载荷 ${(size / 1024 / 1024).toFixed(1)}MB`;
      if (oversized.length < OVERSIZED_KEEP) oversized.push(line);
      // eslint-disable-next-line no-console
      console.warn(
        `[ipc] ${line} —— Windows 上 postMessage 有字符串长度上限, 过大会直接 ` +
          `RangeError 且栈里没有应用帧, 表现为白屏。`,
      );
    }
    try {
      return original(cmd, args, opts);
    } catch (e) {
      // 同步抛 (postMessage RangeError 就是这条路) —— 把命令名和大小带上,
      // 否则错误信息里只有匿名栈, 等于没说。
      const detail = `invoke("${cmd}") 失败, 载荷 ${size} 字节`;
      // eslint-disable-next-line no-console
      console.error(`[ipc] ${detail}`, e);
      showOverlay(
        "界面没能启动起来",
        `${detail}\n\n${(e as Error)?.message || e}\n\n${(e as Error)?.stack || ""}`,
      );
      throw e;
    }
  };

  // 赋值可能失败 —— ES module 是严格模式, 属性若是只读/getter, `=` 直接抛
  // TypeError。8/5 实测: 这一条曾经把整个诊断打哑 (它排在 error 钩子之前,
  // 抛了之后钩子就装不上了, 白屏且零信息 —— 比没加诊断还糟)。
  // 现在既降级又排在钩子之后, 两道都不再依赖它成功。
  try {
    internals.invoke = wrapped;
  } catch {
    try {
      Object.defineProperty(internals, "invoke", {
        value: wrapped,
        configurable: true,
        writable: true,
      });
    } catch {
      // eslint-disable-next-line no-console
      console.warn("[ipc] invoke 是只读属性, 装不上载荷体检 (不影响启动)");
    }
  }
}

/** 装全局错误钩子 + 白屏看门狗。在 main.tsx 最早处调一次。 */
export function installStartupDiagnostics(): void {
  // ⚠ 顺序是有讲究的, 别动。
  //
  // 8/5 实测教训: 我一度把 installIpcSizeGuard() 放在这一行之前, 结果它抛了
  // 一次异常, 后面的 error / unhandledrejection 钩子就再没装上 —— Windows 回到
  // 白屏且**连错误面板都没有了**, 比不加诊断还糟。
  //
  // 安全网必须最先铺。任何在它之前执行的东西, 出事时都没人接。
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

  // 体检放最后, 而且自己套一层 —— 它只是"锦上添花"的定位辅助,
  // 绝不能反过来把上面几层安全网带走。
  try {
    installIpcSizeGuard();
  } catch (e) {
    // eslint-disable-next-line no-console
    console.warn("[ipc] 载荷体检装不上 (不影响启动):", e);
  }
}
