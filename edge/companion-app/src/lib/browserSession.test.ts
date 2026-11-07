/** 浏览器页控制器: 挑哪个标签页、跟着小鲶跳、手动选了就停、画面回执、操作发到对的标签页 */
import { describe, expect, it, vi } from "vitest";
import { BrowserSession, type FrameInfo, type PageTarget } from "./browserSession";

type Msg = { id: number; method: string; params: Record<string, unknown>; sessionId?: string };

/** 假的 Chrome: 记下发过来的命令, 按命令回结果; push() 模拟 Chrome 主动推事件 */
function fakeChrome(targets: { targetId: string; type: string; url: string; title: string }[]) {
  const sent: Msg[] = [];
  let sock: FakeWS;
  let n = 0;
  class FakeWS {
    readyState = 0;
    onopen: (() => void) | null = null;
    onmessage: ((e: { data: string }) => void) | null = null;
    onerror: (() => void) | null = null;
    onclose: (() => void) | null = null;
    constructor() {
      sock = this;
      queueMicrotask(() => { this.readyState = 1; this.onopen?.(); });
    }
    send(raw: string) {
      const m = JSON.parse(raw) as Msg;
      sent.push(m);
      const result: Record<string, unknown> =
        m.method === "Target.getTargets" ? { targetInfos: targets }
        : m.method === "Target.attachToTarget" ? { sessionId: `S-${m.params.targetId}-${++n}` }
        : m.method === "Page.getLayoutMetrics" ? { cssVisualViewport: { clientWidth: 1200, clientHeight: 800 } }
        : m.method === "Page.captureScreenshot" ? { data: "SHOT" }
        : {};
      queueMicrotask(() => this.onmessage?.({ data: JSON.stringify({ id: m.id, result }) }));
    }
    close() { this.onclose?.(); }
  }
  const push = (method: string, params: Record<string, unknown>, sessionId?: string) =>
    sock.onmessage?.({ data: JSON.stringify({ method, params, sessionId }) });
  return { WS: FakeWS as unknown as typeof WebSocket, sent, push };
}

const flush = () => new Promise((r) => setTimeout(r, 0));

function start(targets: Parameters<typeof fakeChrome>[0]) {
  const chrome = fakeChrome(targets);
  const state = { pages: [] as PageTarget[], current: null as string | null, following: true, frames: [] as FrameInfo[] };
  const s = new BrowserSession("ws://127.0.0.1:9222/devtools/browser/x", {
    onPages: (p, c, f) => Object.assign(state, { pages: p, current: c, following: f }),
    onFrame: (f) => state.frames.push(f),
    onClosed: vi.fn(),
  }, chrome.WS);
  return { s, chrome, state };
}

describe("BrowserSession", () => {
  it("起步挑第一个真网页 (跳过空白页 / 设置页 / 开发者工具), 先截一张再推画面", async () => {
    const { s, chrome, state } = start([
      { targetId: "A", type: "page", url: "about:blank", title: "" },
      { targetId: "B", type: "page", url: "chrome://settings/help", title: "设置" },
      { targetId: "D", type: "page", url: "devtools://devtools/x", title: "" },
      { targetId: "C", type: "page", url: "https://business.sohu.com/", title: "财经" },
      { targetId: "W", type: "service_worker", url: "https://x/sw.js", title: "" },
    ]);
    await s.start(2560, 4000);
    await flush();
    expect(state.current).toBe("C");
    expect(state.pages.map((p) => p.targetId)).toEqual(["A", "B", "C"]);
    expect(state.frames[0]).toEqual({ src: "data:image/jpeg;base64,SHOT", width: 1200, height: 800 });
    const sc = chrome.sent.find((m) => m.method === "Page.startScreencast")!;
    expect(sc.sessionId).toMatch(/^S-C-/);
  });

  it("跟着小鲶: 新开 / 换了网址的网页切过去; chrome:// 不跟; 手动选了就停, 点「跟着小鲶」再继续", async () => {
    const { s, chrome, state } = start([{ targetId: "C", type: "page", url: "https://a.com/", title: "A" }]);
    await s.start(2560, 4000);
    chrome.push("Target.targetCreated", { targetInfo: { targetId: "E", type: "page", url: "chrome://newtab/", title: "" } });
    await flush();
    expect(state.current).toBe("C");
    chrome.push("Target.targetCreated", { targetInfo: { targetId: "F", type: "page", url: "https://b.com/", title: "B" } });
    await flush();
    expect(state.current).toBe("F");
    s.pick("C");
    await flush();
    expect(state).toMatchObject({ current: "C", following: false });
    chrome.push("Target.targetInfoChanged", { targetInfo: { targetId: "F", type: "page", url: "https://b.com/next", title: "B2" } });
    await flush();
    expect(state.current).toBe("C");
    s.follow();
    chrome.push("Target.targetInfoChanged", { targetInfo: { targetId: "F", type: "page", url: "https://b.com/more", title: "B3" } });
    await flush();
    expect(state.current).toBe("F");
  });

  it("画面帧: 回执、换算尺寸用帧里的视口大小; 别的标签页的旧帧不显示", async () => {
    const { s, chrome, state } = start([{ targetId: "C", type: "page", url: "https://a.com/", title: "A" }]);
    await s.start(2560, 4000);
    const sid = chrome.sent.find((m) => m.method === "Page.startScreencast")!.sessionId!;
    chrome.push("Page.screencastFrame", { data: "F1", sessionId: 7, metadata: { deviceWidth: 1280, deviceHeight: 720 } }, sid);
    chrome.push("Page.screencastFrame", { data: "OLD", sessionId: 8, metadata: {} }, "S-other");
    await flush();
    expect(state.frames.at(-1)).toEqual({ src: "data:image/jpeg;base64,F1", width: 1280, height: 720 });
    expect(chrome.sent.filter((m) => m.method === "Page.screencastFrameAck").map((m) => m.params.sessionId)).toEqual([7, 8]);
  });

  it("你的操作发到当前标签页; 网址不带协议补 https", async () => {
    const { s, chrome } = start([{ targetId: "C", type: "page", url: "https://a.com/", title: "A" }]);
    await s.start(2560, 4000);
    await s.mouse({ type: "mousePressed", x: 10, y: 20, button: "left", clickCount: 1 });
    await s.insertText("鲶鱼");
    await s.navigate("example.com/login");
    const sid = chrome.sent.find((m) => m.method === "Page.startScreencast")!.sessionId;
    expect(chrome.sent.find((m) => m.method === "Input.dispatchMouseEvent")).toMatchObject({ sessionId: sid, params: { type: "mousePressed", x: 10, y: 20 } });
    expect(chrome.sent.find((m) => m.method === "Input.insertText")).toMatchObject({ sessionId: sid, params: { text: "鲶鱼" } });
    expect(chrome.sent.find((m) => m.method === "Page.navigate")!.params.url).toBe("https://example.com/login");
  });

  it("当前标签页被关了: 换到剩下的", async () => {
    const { s, chrome, state } = start([
      { targetId: "C", type: "page", url: "https://a.com/", title: "A" },
      { targetId: "G", type: "page", url: "https://g.com/", title: "G" },
    ]);
    await s.start(2560, 4000);
    chrome.push("Target.targetDestroyed", { targetId: "C" });
    await flush();
    expect(state.current).toBe("G");
  });
});
