/**
 * 最小的 Chrome 调试协议 (CDP) 客户端 —— 对话右侧「浏览器」页用 (11/07)。
 *
 * 连浏览器级地址 (ws://127.0.0.1:<端口>/devtools/browser/<id>), 用 flatten 会话挂到具体标签页上,
 * 一条连接管所有标签页。小鲶 (Playwright) 同时连着同一个 Chrome, 互不影响: CDP 允许多个客户端。
 */

type Handler = (params: Record<string, unknown>, sessionId?: string) => void;

interface Pending {
  resolve: (v: Record<string, unknown>) => void;
  reject: (e: Error) => void;
}

export class CdpClient {
  private ws: WebSocket;
  private nextId = 1;
  private pending = new Map<number, Pending>();
  private handlers = new Map<string, Set<Handler>>();
  readonly opened: Promise<void>;
  onClose: (() => void) | null = null;

  constructor(url: string, WS: typeof WebSocket = WebSocket) {
    this.ws = new WS(url);
    this.opened = new Promise((resolve, reject) => {
      this.ws.onopen = () => resolve();
      this.ws.onerror = () => reject(new Error("连不上小鲶的浏览器"));
    });
    this.ws.onmessage = (ev) => this.receive(String(ev.data));
    this.ws.onclose = () => {
      for (const p of this.pending.values()) p.reject(new Error("和浏览器的连接断了"));
      this.pending.clear();
      this.onClose?.();
    };
  }

  private receive(raw: string) {
    let msg: { id?: number; method?: string; params?: Record<string, unknown>; result?: Record<string, unknown>; error?: { message: string }; sessionId?: string };
    try {
      msg = JSON.parse(raw);
    } catch {
      return;
    }
    if (msg.id !== undefined) {
      const p = this.pending.get(msg.id);
      if (!p) return;
      this.pending.delete(msg.id);
      if (msg.error) p.reject(new Error(msg.error.message));
      else p.resolve(msg.result ?? {});
      return;
    }
    if (msg.method) for (const h of this.handlers.get(msg.method) ?? []) h(msg.params ?? {}, msg.sessionId);
  }

  send<T = Record<string, unknown>>(method: string, params: Record<string, unknown> = {}, sessionId?: string): Promise<T> {
    if (this.ws.readyState !== 1) return Promise.reject(new Error("和浏览器的连接断了"));
    const id = this.nextId++;
    const body: Record<string, unknown> = { id, method, params };
    if (sessionId) body.sessionId = sessionId;
    return new Promise<T>((resolve, reject) => {
      this.pending.set(id, { resolve: resolve as (v: Record<string, unknown>) => void, reject });
      this.ws.send(JSON.stringify(body));
    });
  }

  on(method: string, handler: Handler): () => void {
    if (!this.handlers.has(method)) this.handlers.set(method, new Set());
    this.handlers.get(method)!.add(handler);
    return () => this.handlers.get(method)?.delete(handler);
  }

  close() {
    this.onClose = null;
    this.ws.close();
  }
}

/** CDP 的修饰键位: Alt=1 Ctrl=2 Meta(⌘)=4 Shift=8 */
export function cdpModifiers(e: { altKey: boolean; ctrlKey: boolean; metaKey: boolean; shiftKey: boolean }): number {
  return (e.altKey ? 1 : 0) | (e.ctrlKey ? 2 : 0) | (e.metaKey ? 4 : 0) | (e.shiftKey ? 8 : 0);
}

/** 不产生文字的按键 → Windows 虚拟键码 (Chrome 靠它认 Enter / 退格 / 方向键…) */
const VK: Record<string, number> = {
  Backspace: 8, Tab: 9, Enter: 13, Escape: 27, " ": 32, PageUp: 33, PageDown: 34, End: 35, Home: 36,
  ArrowLeft: 37, ArrowUp: 38, ArrowRight: 39, ArrowDown: 40, Delete: 46,
};

/** ⌘ / Ctrl 组合键 → Chrome 的编辑命令 (mac 上光发按键事件, 输入框不认全选 / 复制 / 粘贴) */
const EDIT_COMMANDS: Record<string, string> = { a: "selectAll", c: "copy", x: "cut", v: "paste", z: "undo" };

export interface KeyPlan {
  /** 发给 Input.dispatchKeyEvent 的 keyDown 参数; keyUp 用同样的 key / code */
  down: Record<string, unknown>;
  up: Record<string, unknown>;
}

/**
 * 这个按键要不要当「按键」转发, 怎么转发。返回 null = 是普通文字, 交给输入框的 input / 输入法事件走 insertText
 * (中文输入法拼好的字、粘贴进来的字都走那条路, 不在这里一个键一个键发)。
 */
export function planKey(e: { key: string; code: string; altKey: boolean; ctrlKey: boolean; metaKey: boolean; shiftKey: boolean; isComposing?: boolean }, isMac: boolean): KeyPlan | null {
  if (e.isComposing || e.key === "Process" || e.key === "Dead") return null;
  const modifiers = cdpModifiers(e);
  const primary = isMac ? e.metaKey : e.ctrlKey;
  const lower = e.key.length === 1 ? e.key.toLowerCase() : "";
  if (primary && lower) {
    const command = EDIT_COMMANDS[lower];
    const vk = lower.toUpperCase().charCodeAt(0);
    const base = { key: e.key, code: e.code, modifiers, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk };
    return { down: { type: "rawKeyDown", ...base, ...(command ? { commands: [command] } : {}) }, up: { type: "keyUp", ...base } };
  }
  const vk = VK[e.key];
  if (vk === undefined || e.key === " ") return null; // 空格当文字 (insertText) 走
  const base = { key: e.key, code: e.code, modifiers, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk };
  // Enter 要带 text 才会触发表单提交 / 换行
  const text = e.key === "Enter" ? { text: "\r", unmodifiedText: "\r" } : {};
  return { down: { type: e.key === "Enter" ? "keyDown" : "rawKeyDown", ...base, ...text }, up: { type: "keyUp", ...base } };
}
