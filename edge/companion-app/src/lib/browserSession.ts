/**
 * 右侧「浏览器」页的控制器 (11/07): 连小鲶的 Chrome、跟着它正在操作的标签页、收画面、转发你的操作。
 *
 * 跟哪个标签页:
 *   - 默认「跟着小鲶」: 哪个标签页新开了 / 网址或标题变了 (= 小鲶刚在操作它), 就切过去;
 *     起步时挑第一个不是空白页的标签页 (小鲶的 Playwright 也是用第一个标签页)。
 *   - 你在顶上手动选了某个标签页, 就停在那个上, 直到点「跟着小鲶」。
 * 画面: Page.startScreencast (页面有变化才来一帧); 刚挂上时先截一张, 不用干等。
 * 铺满右栏 (11/07 鸿波「为什么是这么一截」): 小鲶的 Chrome 窗口又宽又矮, 原样缩进右栏只剩一条。
 *   挂上时让这个标签页按右栏的比例排版 —— 宽度不变 (网页布局不变), 高度按右栏拉长
 *   (Emulation.setDeviceMetricsOverride)。这个设置跟着本连接走: 右栏关了 / 切走 / Companion 退出,
 *   Chrome 自动恢复原尺寸 (测试 Chrome 里验过 1600x763 → 1600x2600 → 断开回 1600x763)。
 */
import { CdpClient } from "./cdp";

export interface PageTarget {
  targetId: string;
  url: string;
  title: string;
}

export interface FrameInfo {
  /** data:image/jpeg;base64,... */
  src: string;
  /** 页面视口的 CSS 像素宽高 —— 把你在缩小画面上点的位置换算回页面坐标用 */
  width: number;
  height: number;
}

export interface BrowserSessionCallbacks {
  onPages: (pages: PageTarget[], currentId: string | null, following: boolean) => void;
  onFrame: (frame: FrameInfo) => void;
  onClosed: (reason: string) => void;
}

/** 小鲶会去的网页 (自动跟随只跟这些; chrome:// 设置页、空白页这类浏览器自己开的不跟, 但列表里还能手动选) */
const isWeb = (url: string) => /^(https?|file):/i.test(url);

const isPage = (t: { type?: string; url?: string }) =>
  t.type === "page" && !String(t.url ?? "").startsWith("devtools://") && !String(t.url ?? "").startsWith("chrome-extension://");

export class BrowserSession {
  private cdp: CdpClient;
  private pages = new Map<string, PageTarget>();
  private current: string | null = null;
  private session: string | null = null;
  private following = true;
  private viewport = { width: 0, height: 0 };
  private maxSize = { width: 1280, height: 1600 };
  /** 右栏显示区的大小 (只用比例) */
  private panel = { width: 0, height: 0 };
  /** 这个标签页没被我们拉高之前的视口 */
  private natural = { width: 0, height: 0 };
  private disposed = false;
  private attachSeq = 0;

  constructor(wsUrl: string, private cb: BrowserSessionCallbacks, WS?: typeof WebSocket) {
    this.cdp = new CdpClient(wsUrl, WS);
    this.cdp.onClose = () => !this.disposed && cb.onClosed("和小鲶的浏览器断开了 (浏览器可能被关了)");
  }

  async start(maxWidth: number, maxHeight: number) {
    this.maxSize = { width: Math.round(maxWidth), height: Math.round(maxHeight) };
    await this.cdp.opened;
    this.cdp.on("Target.targetCreated", (p) => this.upsert(p.targetInfo as Record<string, string>, true));
    this.cdp.on("Target.targetInfoChanged", (p) => this.upsert(p.targetInfo as Record<string, string>, false));
    this.cdp.on("Target.targetDestroyed", (p) => this.remove(String(p.targetId)));
    this.cdp.on("Page.screencastFrame", (p, sid) => this.frame(p, sid));
    await this.cdp.send("Target.setDiscoverTargets", { discover: true });
    const { targetInfos } = await this.cdp.send<{ targetInfos: Record<string, string>[] }>("Target.getTargets");
    for (const t of targetInfos.filter(isPage)) this.pages.set(t.targetId, { targetId: t.targetId, url: t.url, title: t.title });
    const all = [...this.pages.values()];
    const first = all.find((p) => isWeb(p.url)) ?? all[0];
    this.emitPages();
    if (first) await this.attach(first.targetId);
  }

  private emitPages() {
    this.cb.onPages([...this.pages.values()], this.current, this.following);
  }

  private upsert(info: Record<string, string>, created: boolean) {
    if (!info || !isPage(info)) return;
    const prev = this.pages.get(info.targetId);
    this.pages.set(info.targetId, { targetId: info.targetId, url: info.url, title: info.title });
    const changed = created || !prev || prev.url !== info.url;
    if (this.following && changed && info.targetId !== this.current && isWeb(info.url)) void this.attach(info.targetId);
    else this.emitPages();
  }

  private remove(targetId: string) {
    if (!this.pages.delete(targetId)) return;
    if (targetId === this.current) {
      this.current = null;
      this.session = null;
      const next = [...this.pages.values()].pop();
      if (next) void this.attach(next.targetId);
    }
    this.emitPages();
  }

  private async attach(targetId: string) {
    const seq = ++this.attachSeq;
    const old = this.session;
    this.current = targetId;
    this.session = null;
    this.emitPages();
    if (old) {
      void this.cdp.send("Page.stopScreencast", {}, old).catch(() => {});
      void this.cdp.send("Target.detachFromTarget", { sessionId: old }).catch(() => {});
    }
    try {
      const { sessionId } = await this.cdp.send<{ sessionId: string }>("Target.attachToTarget", { targetId, flatten: true });
      if (seq !== this.attachSeq || this.disposed) {
        void this.cdp.send("Target.detachFromTarget", { sessionId }).catch(() => {});
        return;
      }
      this.session = sessionId;
      await this.cdp.send("Page.enable", {}, sessionId);
      await this.refreshViewport();
      this.natural = { ...this.viewport };
      await this.applyFit();
      await this.snapshot();
      await this.cdp.send("Page.startScreencast", { format: "jpeg", quality: 75, maxWidth: this.maxSize.width, maxHeight: this.maxSize.height, everyNthFrame: 1 }, sessionId);
    } catch {
      // 标签页刚好被关了: targetDestroyed 会接着处理
    }
  }

  private async refreshViewport() {
    if (!this.session) return;
    const m = await this.cdp.send<{ cssVisualViewport?: { clientWidth: number; clientHeight: number } }>("Page.getLayoutMetrics", {}, this.session);
    if (m.cssVisualViewport) this.viewport = { width: m.cssVisualViewport.clientWidth, height: m.cssVisualViewport.clientHeight };
  }

  /** 右栏显示区大小变了 (打开 / 拖宽 / 窗口缩放): 按新比例重排 */
  async setPanel(width: number, height: number) {
    if (width <= 0 || height <= 0) return;
    this.panel = { width, height };
    if (this.session) {
      await this.applyFit();
      await this.snapshot().catch(() => {});
    }
  }

  private async applyFit() {
    const sid = this.session;
    const { width, height } = this.natural;
    if (!sid || !width || !height || !this.panel.width) return;
    const want = Math.min(4000, Math.round((width * this.panel.height) / this.panel.width));
    if (want <= height) {
      // 右栏本来就比窗口「扁」: 不用拉, 恢复原样
      await this.cdp.send("Emulation.clearDeviceMetricsOverride", {}, sid).catch(() => {});
      this.viewport = { ...this.natural };
      return;
    }
    await this.cdp.send("Emulation.setDeviceMetricsOverride", { width, height: want, deviceScaleFactor: 0, mobile: false }, sid).catch(() => {});
    this.viewport = { width, height: want };
  }

  /** 截一张当前画面 (刚挂上时 / 点「刷新画面」; 后台标签页 Chrome 不推 screencast 帧, 靠这个兜底) */
  async snapshot() {
    const sid = this.session;
    if (!sid) return;
    const { data } = await this.cdp.send<{ data: string }>("Page.captureScreenshot", { format: "jpeg", quality: 75 }, sid);
    if (sid === this.session) this.cb.onFrame({ src: `data:image/jpeg;base64,${data}`, ...this.viewport });
  }

  private frame(p: Record<string, unknown>, sid?: string) {
    if (!sid) return;
    void this.cdp.send("Page.screencastFrameAck", { sessionId: p.sessionId }, sid).catch(() => {});
    if (sid !== this.session) return;
    const meta = p.metadata as { deviceWidth?: number; deviceHeight?: number } | undefined;
    if (meta?.deviceWidth && meta.deviceHeight) this.viewport = { width: meta.deviceWidth, height: meta.deviceHeight };
    this.cb.onFrame({ src: `data:image/jpeg;base64,${p.data}`, ...this.viewport });
  }

  /** 你手动选了一个标签页: 停在它上面, 不再跟着小鲶跳 */
  pick(targetId: string) {
    this.following = false;
    if (targetId !== this.current) void this.attach(targetId);
    else this.emitPages();
  }

  follow() {
    this.following = true;
    this.emitPages();
  }

  // ── 你的操作 ─────────────────────────────────────────────

  private input(method: string, params: Record<string, unknown>) {
    if (!this.session) return Promise.resolve();
    return this.cdp.send(method, params, this.session).catch(() => {});
  }

  mouse(params: { type: "mousePressed" | "mouseReleased" | "mouseMoved" | "mouseWheel"; x: number; y: number; button?: string; buttons?: number; clickCount?: number; deltaX?: number; deltaY?: number; modifiers?: number }) {
    return this.input("Input.dispatchMouseEvent", { button: "none", ...params });
  }

  key(params: Record<string, unknown>) {
    return this.input("Input.dispatchKeyEvent", params);
  }

  insertText(text: string) {
    return text ? this.input("Input.insertText", { text }) : Promise.resolve();
  }

  async navigate(raw: string) {
    const text = raw.trim();
    if (!text || !this.session) return;
    const url = /^[a-z][a-z0-9+.-]*:/i.test(text) ? text : `https://${text}`;
    await this.cdp.send("Page.navigate", { url }, this.session);
  }

  async history(delta: -1 | 1) {
    if (!this.session) return;
    const h = await this.cdp.send<{ currentIndex: number; entries: { id: number }[] }>("Page.getNavigationHistory", {}, this.session);
    const entry = h.entries[h.currentIndex + delta];
    if (entry) await this.cdp.send("Page.navigateToHistoryEntry", { entryId: entry.id }, this.session);
  }

  reload() {
    return this.session ? this.cdp.send("Page.reload", {}, this.session).then(() => {}) : Promise.resolve();
  }

  /** 新开一个标签页 (小鲶的 Chrome 窗口被关光了时用 —— mac 上进程还在, 开一页配置就回来); 跟着小鲶时会自动切过去 */
  async newTab(url = "about:blank") {
    const { targetId } = await this.cdp.send<{ targetId: string }>("Target.createTarget", { url });
    this.following = true;
    await this.attach(targetId);
  }

  dispose() {
    this.disposed = true;
    if (this.session) void this.cdp.send("Page.stopScreencast", {}, this.session).catch(() => {});
    this.cdp.close();
  }
}
