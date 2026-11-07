/**
 * 右侧栏「浏览器」页 (11/07 鸿波「浏览器两步一起做」)。
 *
 * 看: 小鲶正在操作的网页实时画面 (lib/browserSession.ts 连鲶鱼 Chrome, 跟着小鲶在用的标签页走)。
 * 接手: 点「我来操作」后, 你在画面上的点击 / 滚动 / 按键 / 输入都转给那个页面 —— 登录、验证码、
 *   小鲶不该替你点的确认按钮这类事。打字走一个看不见的输入框: 中文输入法拼好的字、粘贴的字整段送过去
 *   (Input.insertText); Enter / 退格 / 方向键 / ⌘A ⌘C ⌘V 这类当按键送 (lib/cdp.ts planKey)。
 * 默认只看不动, 免得你随手一点打断小鲶。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowClockwise, ArrowLeft, ArrowRight, ArrowsIn, ArrowsOut, Camera, HandPointing, Eye } from "@phosphor-icons/react";
import { BrowserSession, type FrameInfo, type PageTarget } from "../../../lib/browserSession";
import { cdpModifiers, planKey } from "../../../lib/cdp";
import { browserViewEndpoint } from "../../../lib/tauri_artifacts";
import { chromeLaunch } from "../../../lib/tauri_services";
import { Notice } from "./ArtifactViews";

const isMac = typeof navigator !== "undefined" && /Mac/.test(navigator.userAgent);
const BUTTON = ["left", "middle", "right"] as const;
/** 画面传多宽: 按右栏实际显示的宽度 (设备像素), 「原始大小」时按网页宽度 —— 见 BrowserSession.setFrameWidth */
const dpr = () => window.devicePixelRatio || 1;

type Status = { kind: "connecting" } | { kind: "ready" } | { kind: "error"; message: string; notRunning: boolean };

export default function BrowserPane() {
  const [status, setStatus] = useState<Status>({ kind: "connecting" });
  const [pages, setPages] = useState<PageTarget[]>([]);
  const [current, setCurrent] = useState<string | null>(null);
  const [following, setFollowing] = useState(true);
  const [hasFrame, setHasFrame] = useState(false);
  const [takeover, setTakeover] = useState(false);
  const [address, setAddress] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [launching, setLaunching] = useState(false);
  /** 适应右栏宽度 (默认) / 按网页原始大小 (字太小不好点时切过去, 可以左右滚) */
  const [fit, setFit] = useState(true);
  const [pageWidth, setPageWidth] = useState(0);
  const session = useRef<BrowserSession | null>(null);
  const frame = useRef<FrameInfo | null>(null);
  const img = useRef<HTMLImageElement>(null);
  const screen = useRef<HTMLDivElement>(null);
  const keys = useRef<HTMLTextAreaElement>(null);
  const composing = useRef(false);

  // 连接 (打开浏览器页 / 点「重试」时)
  useEffect(() => {
    let dead = false;
    setStatus({ kind: "connecting" });
    setHasFrame(false);
    setTakeover(false); // 重新连上默认只看
    void (async () => {
      let ws: string;
      try {
        ws = await browserViewEndpoint();
      } catch (e) {
        if (!dead) setStatus({ kind: "error", message: String(e), notRunning: true });
        return;
      }
      const s = new BrowserSession(ws, {
        onPages: (p, cur, f) => {
          setPages(p);
          setCurrent(cur);
          setFollowing(f);
        },
        onFrame: (f) => {
          frame.current = f;
          setHasFrame(true);
          setPageWidth(f.width);
          const el = img.current;
          if (!el) return;
          // 直接换图, 不走 React 重渲染; 画上去之后才让 Chrome 发下一帧 (最多等 0.5 秒)
          return new Promise<void>((resolve) => {
            const done = () => { el.onload = null; el.onerror = null; resolve(); };
            el.onload = done;
            el.onerror = done;
            window.setTimeout(done, 500);
            el.src = f.src;
          });
        },
        onClosed: (message) => setStatus({ kind: "error", message, notRunning: false }),
      });
      session.current = s;
      const box = screen.current?.getBoundingClientRect();
      if (box) void s.setPanel(box.width, box.height);
      try {
        await s.start(Math.max(box?.width || 600, 320) * dpr(), 8000);
        if (!dead) setStatus({ kind: "ready" });
      } catch (e) {
        if (!dead) setStatus({ kind: "error", message: String(e instanceof Error ? e.message : e), notRunning: false });
      }
    })();
    return () => {
      dead = true;
      session.current?.dispose();
      session.current = null;
    };
  }, [attempt]);

  // 右栏显示区大小变了 (拖宽 / 窗口缩放): 让页面按新比例排版, 铺满右栏
  useEffect(() => {
    const el = screen.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    let t = 0;
    const ro = new ResizeObserver(() => {
      window.clearTimeout(t);
      t = window.setTimeout(() => {
        const r = el.getBoundingClientRect();
        void session.current?.setPanel(r.width, r.height);
        if (fitRef.current) void session.current?.setFrameWidth(r.width * dpr());
      }, 250);
    });
    ro.observe(el);
    return () => {
      ro.disconnect();
      window.clearTimeout(t);
    };
  }, []);

  // 缩小画面上的位置 → 页面 CSS 坐标
  const toPage = useCallback((clientX: number, clientY: number) => {
    const r = img.current?.getBoundingClientRect();
    const f = frame.current;
    if (!r || !f || !r.width || !r.height) return null;
    return { x: ((clientX - r.left) * f.width) / r.width, y: ((clientY - r.top) * f.height) / r.height };
  }, []);

  // 滚轮: 接手时转给页面 (要能 preventDefault, React 的 onWheel 是 passive, 用原生监听)
  useEffect(() => {
    const el = img.current;
    if (!el || !takeover) return;
    const onWheel = (e: WheelEvent) => {
      const p = toPage(e.clientX, e.clientY);
      if (!p) return;
      e.preventDefault();
      void session.current?.mouse({ type: "mouseWheel", ...p, deltaX: e.deltaX, deltaY: e.deltaY, modifiers: cdpModifiers(e) });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [takeover, toPage, hasFrame]);

  // 适应宽度 ↔ 原始大小: 画面按显示宽度重新传
  const fitRef = useRef(fit);
  useEffect(() => {
    fitRef.current = fit;
    const w = fit ? (screen.current?.getBoundingClientRect().width ?? 600) : pageWidth || 1600;
    void session.current?.setFrameWidth(w * dpr());
  }, [fit, pageWidth]);

  // 我来操作: 链接改在本页打开, 免得 Chrome 窗口跳到最前面
  useEffect(() => {
    void session.current?.setTakeover(takeover);
  }, [takeover]);

  const lastMove = useRef(0);
  const onMouse = (type: "mousePressed" | "mouseReleased" | "mouseMoved") => (e: React.MouseEvent) => {
    if (!takeover) return;
    if (type === "mouseMoved") {
      const now = performance.now();
      if (now - lastMove.current < (e.buttons ? 30 : 100)) return; // 拖动时 30 次 / 秒, 光移动 10 次 / 秒
      lastMove.current = now;
    } else {
      e.preventDefault();
      // 看不见的输入框挪到点的位置: 中文输入法的候选框跟着它出现, 不会飘到右栏左上角
      const box = screen.current;
      if (keys.current && box) {
        const r = box.getBoundingClientRect();
        keys.current.style.left = `${e.clientX - r.left + box.scrollLeft}px`;
        keys.current.style.top = `${e.clientY - r.top + box.scrollTop}px`;
      }
      keys.current?.focus({ preventScroll: true });
    }
    const p = toPage(e.clientX, e.clientY);
    if (!p) return;
    void session.current?.mouse({
      type, ...p, button: type === "mouseMoved" && !e.buttons ? "none" : BUTTON[e.button] ?? "left",
      buttons: e.buttons, clickCount: type === "mouseMoved" ? 0 : e.detail || 1, modifiers: cdpModifiers(e),
    });
  };

  const flushText = () => {
    const el = keys.current;
    if (!el || composing.current || !el.value) return;
    const text = el.value;
    el.value = "";
    void session.current?.insertText(text);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    const plan = planKey({ ...e, isComposing: e.nativeEvent.isComposing || composing.current }, isMac);
    if (!plan) return; // 普通文字: 让它进输入框, 由 input / 输入法结束事件整段送
    e.preventDefault();
    void session.current?.key(plan.down).then(() => session.current?.key(plan.up));
  };

  const launch = async () => {
    setLaunching(true);
    try {
      await chromeLaunch();
      setAttempt((n) => n + 1);
    } catch (e) {
      setStatus({ kind: "error", message: `打开失败：${String(e)}`, notRunning: true });
    } finally {
      setLaunching(false);
    }
  };

  const page = pages.find((p) => p.targetId === current) ?? null;
  const s = session.current;

  return (
    <>
      <div className="artifact-panel__toolbar browser-bar">
        <div className="artifact-panel__actions">
          <button type="button" onClick={() => void s?.history(-1)} title="后退" aria-label="后退" disabled={!page}><ArrowLeft size={16} /></button>
          <button type="button" onClick={() => void s?.history(1)} title="前进" aria-label="前进" disabled={!page}><ArrowRight size={16} /></button>
          <button type="button" onClick={() => void s?.reload()} title="重新加载" aria-label="重新加载" disabled={!page}><ArrowClockwise size={16} /></button>
        </div>
        <form className="browser-bar__address" onSubmit={(e) => { e.preventDefault(); if (address !== null) void s?.navigate(address); setAddress(null); }}>
          <input
            value={address ?? page?.url ?? ""}
            onChange={(e) => setAddress(e.target.value)}
            onFocus={(e) => { setAddress(page?.url ?? ""); e.currentTarget.select(); }}
            onBlur={() => setAddress(null)}
            placeholder="网址"
            aria-label="网址"
            disabled={!page}
            spellCheck={false}
          />
        </form>
        <button
          type="button"
          className="browser-bar__takeover"
          data-on={takeover}
          onClick={() => { setTakeover((v) => !v); keys.current?.focus({ preventScroll: true }); }}
          disabled={!hasFrame}
          title={takeover ? "交还给小鲶，画面只看不动" : "在画面上直接点、滚动、打字（登录、验证码这类）"}
        >
          {takeover ? <><Eye size={14} />只看</> : <><HandPointing size={14} />我来操作</>}
        </button>
      </div>
      {(pages.length > 1 || !following) && status.kind === "ready" && (
        <div className="artifact-panel__meta browser-tabs">
          <select value={current ?? ""} onChange={(e) => s?.pick(e.target.value)} aria-label={`标签页，共 ${pages.length} 个`}>
            {pages.map((p) => <option key={p.targetId} value={p.targetId}>{p.title || p.url}</option>)}
          </select>
          {following ? <span>跟着小鲶</span> : <button type="button" onClick={() => s?.follow()}>跟着小鲶</button>}
        </div>
      )}
      <div className="artifact-panel__body">
        {status.kind === "error" && (
          <div className="browser-empty">
            <Notice tone={status.notRunning ? "muted" : "error"}>{status.notRunning ? "小鲶的浏览器还没打开。小鲶用到网页时会自动打开，也可以现在打开。" : status.message}</Notice>
            <div className="browser-empty__actions">
              {status.notRunning && <button type="button" className="browser-primary" onClick={() => void launch()} disabled={launching}>{launching ? "正在打开…" : "打开小鲶的浏览器"}</button>}
              <button type="button" onClick={() => setAttempt((n) => n + 1)}>重试</button>
            </div>
          </div>
        )}
        {status.kind === "connecting" && <Notice>正在连接小鲶的浏览器…</Notice>}
        {status.kind === "ready" && !page && (
          <div className="browser-empty">
            <Notice>小鲶的浏览器开着，但一个网页都没打开（窗口可能被关了）。小鲶用到网页时会自己开，也可以现在开一个。</Notice>
            <div className="browser-empty__actions">
              <button type="button" className="browser-primary" onClick={() => void s?.newTab()}>新开一个网页</button>
            </div>
          </div>
        )}
        <div ref={screen} className="browser-screen" data-takeover={takeover} hidden={status.kind !== "ready" || !page}>
          <img
            ref={img}
            alt={page ? `小鲶正在看：${page.title || page.url}` : ""}
            draggable={false}
            hidden={!hasFrame}
            style={fit || !pageWidth ? undefined : { width: pageWidth, maxWidth: "none" }}
            onMouseDown={onMouse("mousePressed")}
            onMouseUp={onMouse("mouseReleased")}
            onMouseMove={onMouse("mouseMoved")}
            onContextMenu={(e) => takeover && e.preventDefault()}
          />
          {!hasFrame && page && <Notice>正在取画面…</Notice>}
          <textarea
            ref={keys}
            className="browser-keys"
            aria-label="输入到网页"
            tabIndex={takeover ? 0 : -1}
            disabled={!takeover}
            onKeyDown={onKeyDown}
            onInput={flushText}
            onCompositionStart={() => { composing.current = true; }}
            onCompositionEnd={() => { composing.current = false; queueMicrotask(flushText); }}
          />
        </div>
        {status.kind === "ready" && page && hasFrame && (
          <div className="browser-screen__tools">
            <button type="button" onClick={() => setFit((v) => !v)}
              title={fit ? "按网页原始大小看 (字太小时用, 可以左右滚)" : "缩到右栏宽度"} aria-label={fit ? "原始大小" : "适应宽度"}>
              {fit ? <ArrowsOut size={14} /> : <ArrowsIn size={14} />}
            </button>
            <button type="button" onClick={() => void s?.snapshot()} title="画面没跟上时点一下" aria-label="刷新画面">
              <Camera size={14} />
            </button>
          </div>
        )}
        {/* 浮在画面上 (放在滚动区外, 画面再长也看得见), 不挤动画面 —— 挤一下你就点偏了 */}
        {takeover && hasFrame && <div className="browser-takeover-note">你在操作这个页面 · 小鲶这时也可能在动它，办完点「只看」交还</div>}
      </div>
    </>
  );
}
