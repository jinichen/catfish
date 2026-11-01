/** 手机网页 `/m` (docs/MOBILE-CLIENT-DESIGN.md §7 第一期): 跟自己电脑上的小鲶对话、批准、看早安简报。
 *
 * 内容端到端加密, 经中央中转站到员工电脑; 中央看不到。电脑关机 / 休眠时连不上, 页面直说。
 * 第一期范围就这三样 (§11 定稿), 别的在电脑上做。
 */
import { Component, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { SimpleMarkdown } from "../components/SimpleMarkdown";
import { getCurrentUser, getIdToken, login } from "../lib/auth";
import { MobileClient, type ClientEvent, type Peer } from "./client";
import { installPwaTags, needsHomeScreenHint, requestPersistentStorage } from "./pwa";

type Tab = "chat" | "briefing";

interface ChatMsg {
  role: "user" | "assistant";
  text: string;
  status?: string;
  error?: string;
}

interface Approval {
  runId: string;
  command: string;
  description: string;
  state: "pending" | "sending" | "done";
  result?: string;
}

interface SessionItem {
  id: string;
  title: string;
  startedAt: string;
}

interface Briefing {
  computedAt: string | null;
  mainTasks: { title: string; urgency: string; reason?: string }[];
  handled: { category: string; count: number }[];
}

type Handler = (m: Record<string, unknown>) => void;

const page: React.CSSProperties = {
  height: "100dvh", display: "flex", flexDirection: "column", background: "var(--bg)",
  maxWidth: 640, margin: "0 auto",
};
const bar: React.CSSProperties = {
  display: "flex", alignItems: "center", gap: 8, padding: "10px 16px",
  borderBottom: "1px solid var(--border)", background: "var(--bg-elev)",
};
const btn: React.CSSProperties = {
  padding: "8px 14px", borderRadius: "var(--radius-md)", border: "1px solid var(--border)",
  background: "var(--bg-elev)", fontSize: 15,
};
const primary: React.CSSProperties = { ...btn, background: "var(--accent)", color: "#fff", border: "none" };
const muted: React.CSSProperties = { color: "var(--text-muted)", fontSize: 13 };
const URGENCY: Record<string, string> = { high: "var(--status-err)", medium: "var(--status-warn)", low: "var(--border)" };

/** 先确认登录 (没登录就跳登录页, 登完回 /m), 再进手机页。不调 /api/me。 */
export function MobileRoot() {
  const [state, setState] = useState<"checking" | "ok" | "insecure">(
    window.isSecureContext ? "checking" : "insecure");
  useEffect(() => {
    if (state !== "checking") return;
    void getCurrentUser().then((u) => (u ? setState("ok") : login("/m")));
  }, [state]);
  if (state === "insecure") {
    return (
      <div style={{ padding: 24 }}>
        这个页面要用 https 打开 (浏览器只在安全连接下提供加密功能)。请让 IT 给出 https 地址。
      </div>
    );
  }
  if (state === "checking") return <div style={{ padding: 24, color: "var(--text-muted)" }}>正在登录…</div>;
  return <ErrorShield><MobileApp /></ErrorShield>;
}

/** 页面出错时把原因显示出来 (11/01: 点「连接」后整页空白, 员工和我们都看不到为什么) */
class ErrorShield extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div style={{ padding: 24 }}>
        <p>页面出错了, 请截图发给 IT:</p>
        <pre style={{ whiteSpace: "pre-wrap", fontSize: 12, color: "var(--status-err)" }}>
          {String(this.state.error?.stack ?? this.state.error)}
        </pre>
        <button onClick={() => window.location.reload()}>重新打开</button>
      </div>
    );
  }
}

export function MobileApp() {
  const client = useMemo(() => new MobileClient(getIdToken), []);
  const [, force] = useState(0);
  const rerender = useCallback(() => force((n) => n + 1), []);
  const [statusText, setStatusText] = useState<string | undefined>();
  const [ready, setReady] = useState(false);
  const [picking, setPicking] = useState(true);
  const [pairCode, setPairCode] = useState<{ device: string; code: string } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("chat");
  const [homeHint, setHomeHint] = useState(() => {
    try {
      return needsHomeScreenHint() && localStorage.getItem("catfish.mobile.homeHintDismissed") !== "1";
    } catch {
      return needsHomeScreenHint();
    }
  });
  const handlers = useRef(new Map<string, Handler>());
  // 电脑上 (不是这部手机发起的) 对话在等批准的步骤 —— Companion 定时推过来, 不带 rid
  const [pcApprovals, setPcApprovals] = useState<Approval[]>([]);
  // 手机自己发起的对话里出现过的 runId: 同一步不在顶上再显示一遍
  const chatRunIds = useRef(new Set<string>());

  useEffect(() => {
    installPwaTags();
    requestPersistentStorage();
    const off = client.on((e: ClientEvent) => {
      switch (e.type) {
        case "status":
          setStatusText(e.detail);
          rerender();
          break;
        case "peers":
          rerender();
          break;
        case "pair_code":
          setPairCode({ device: e.device, code: e.code });
          break;
        case "pair_result":
          setPairCode(null);
          if (e.ok) {
            setNotice("配好了");
            // 记住这台, 下次打开直接连
            localStorage.setItem("catfish.mobile.last", e.device);
            client.use(e.device);
            setPicking(false);
          } else {
            setNotice(e.reason ?? "没有配上");
          }
          break;
        case "session":
          setReady(e.ready);
          break;
        case "revoked":
          setNotice("这台电脑已经撤销了跟这部手机的连接, 要用请重新配对");
          setReady(false);
          setPicking(true);
          break;
        case "offline":
          if (e.device === client.current) {
            setNotice("电脑不在线 (可能关机或休眠了)");
            for (const h of handlers.current.values()) h({ op: "error", message: "电脑不在线" });
          }
          break;
        case "message": {
          const rid = String(e.m.rid ?? "");
          if (!rid && (e.m.op === "approval" || e.m.op === "approval_gone")) {
            const m = e.m;
            setPcApprovals((all) => applyPcApproval(all, m, chatRunIds.current));
          } else {
            handlers.current.get(rid)?.(e.m);
          }
          break;
        }
      }
    });
    void client.connect();
    const onVis = () => { if (document.visibilityState === "visible") client.wake(); };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      off();
      document.removeEventListener("visibilitychange", onVis);
      client.close();
    };
  }, [client, rerender]);

  // 上次用的电脑在线就直接连上
  useEffect(() => {
    if (client.status !== "ready" || client.current) return;
    const last = localStorage.getItem("catfish.mobile.last");
    const peer = client.peers.find((p) => p.device === last && p.paired && p.online);
    if (peer) {
      client.use(peer.device);
      setPicking(false);
    }
  });

  const request = useCallback((op: string, payload: Record<string, unknown>, h: Handler): string => {
    const rid = client.request(op, payload);
    handlers.current.set(rid, (m) => {
      if (m.op === "done" || m.op === "error" || m.op === "result" || m.op === "pong") handlers.current.delete(rid);
      h(m);
    });
    return rid;
  }, [client]);

  const choose = (p: Peer) => {
    setNotice(null);
    if (p.paired) {
      localStorage.setItem("catfish.mobile.last", p.device);
      client.use(p.device);
      setPicking(false);
    } else {
      client.pair(p.device);
    }
  };

  const currentName = client.peers.find((p) => p.device === client.current)?.name
    ?? client.pairedPcs()[client.current ?? ""]?.name;

  return (
    <div style={page}>
      <div style={bar}>
        <img src="/catfish-mascot.svg" alt="" width={28} height={28} />
        <b style={{ fontSize: 17 }}>小鲶</b>
        <span style={{ ...muted, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {client.status !== "ready" ? (statusText ?? "正在连接…") : currentName ? `· ${currentName}${ready ? "" : " (连接中)"}` : ""}
        </span>
        {!picking && <button style={btn} onClick={() => setPicking(true)}>换电脑</button>}
      </div>
      {notice && (
        <div role="status" onClick={() => setNotice(null)}
          style={{ padding: "8px 16px", background: "var(--bg-secondary)", fontSize: 14 }}>{notice}</div>
      )}
      {homeHint && (
        <div style={{ padding: "8px 16px", background: "var(--bg-secondary)", fontSize: 13, display: "flex", gap: 8 }}>
          <span style={{ flex: 1 }}>
            建议加到主屏幕再用: 点 Safari 下方的「分享」→「添加到主屏幕」。直接在浏览器里用的话,
            一周不打开 Safari 会清掉配对, 要重新配。
          </span>
          <button style={{ ...btn, padding: "4px 10px", fontSize: 13 }} onClick={() => {
            setHomeHint(false);
            try { localStorage.setItem("catfish.mobile.homeHintDismissed", "1"); } catch { /* 不记也行 */ }
          }}>知道了</button>
        </div>
      )}
      {client.status === "error" && (
        <div style={{ padding: 16 }}>
          <p>{statusText}</p>
          <button style={primary} onClick={() => void login("/m")}>重新登录</button>
        </div>
      )}
      {pairCode ? (
        <PairCodeView code={pairCode.code} onCancel={() => setPairCode(null)} />
      ) : picking ? (
        <PcPicker client={client} onChoose={choose} />
      ) : (
        <>
          <div style={{ flex: 1, minHeight: 0, display: tab === "chat" ? "flex" : "none", flexDirection: "column" }}>
            <PcApprovals items={pcApprovals} setItems={setPcApprovals} request={request} />
            <ChatView ready={ready} request={request} onApproval={(runId) => {
              chatRunIds.current.add(runId);
              setPcApprovals((all) => all.filter((a) => a.runId !== runId));
            }} />
          </div>
          {tab === "briefing" && <BriefingView ready={ready} request={request} />}
          <div style={{ ...bar, borderTop: "1px solid var(--border)", borderBottom: "none", justifyContent: "space-around",
            paddingBottom: "calc(10px + env(safe-area-inset-bottom))" }}>
            {(["chat", "briefing"] as Tab[]).map((t) => (
              <button key={t} onClick={() => setTab(t)}
                style={{ ...btn, border: "none", fontWeight: tab === t ? 600 : 400, color: tab === t ? "var(--accent)" : "var(--text)" }}>
                {t === "chat" ? "💬 对话" : "☀️ 早安"}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function PcPicker({ client, onChoose }: { client: MobileClient; onChoose: (p: Peer) => void }) {
  useEffect(() => {
    client.refreshPeers();
    const t = setInterval(() => client.refreshPeers(), 10000);
    return () => clearInterval(t);
  }, [client]);
  const online = client.peers.filter((p) => p.online);
  const offline = client.peers.filter((p) => !p.online);
  return (
    <div style={{ padding: 16, overflowY: "auto", flex: 1 }}>
      <h3 style={{ marginTop: 0 }}>连哪台电脑</h3>
      {online.length === 0 && (
        <p style={muted}>
          现在没有在线的电脑。在电脑上打开鲶鱼 Companion → 设置 → 隐私 / 本机数据 → 手机,
          打开「允许手机连这台电脑」。电脑要开着、联网、没休眠。
        </p>
      )}
      {online.map((p) => (
        <div key={p.device} style={{ display: "flex", alignItems: "center", gap: 8, padding: "12px 0",
          borderBottom: "1px solid var(--border-soft)" }}>
          <span style={{ flex: 1 }}>🖥️ {p.name || "电脑"}</span>
          <button style={p.paired ? primary : btn} onClick={() => onChoose(p)}>{p.paired ? "连接" : "配对"}</button>
        </div>
      ))}
      {offline.map((p) => (
        <div key={p.device} style={{ display: "flex", alignItems: "center", gap: 8, padding: "12px 0", ...muted }}>
          <span style={{ flex: 1 }}>🖥️ {p.name} · 不在线</span>
          <button style={btn} onClick={() => {
            if (window.confirm(`在这部手机上删掉「${p.name}」? 电脑那边的记录要在电脑上撤销。`)) client.forgetPc(p.device);
          }}>删掉</button>
        </div>
      ))}
      <p style={{ ...muted, marginTop: 24 }}>
        内容加密后经公司中央转发, 中央看不到、不保存。
        {client.maxPhones != null && ` 每人最多配 ${client.maxPhones} 部手机。`}
      </p>
    </div>
  );
}

function PairCodeView({ code, onCancel }: { code: string; onCancel: () => void }) {
  return (
    <div style={{ padding: 24, textAlign: "center", flex: 1 }}>
      <p>在电脑上会弹出一个验证码, 跟这里的一样就在电脑上点「允许」:</p>
      <div aria-label="验证码" style={{ fontSize: 40, letterSpacing: 10, fontWeight: 600, fontFamily: "var(--font-mono)",
        margin: "24px 0" }}>{code}</div>
      <p style={muted}>数字对不上就别点允许 —— 那说明连接被人动过。</p>
      <button style={btn} onClick={onCancel}>取消</button>
    </div>
  );
}

type RequestFn = (op: string, payload: Record<string, unknown>, h: Handler) => string;

/** 电脑推来的「等批准」/「已处理掉」 → 顶上那排卡片。手机自己对话里已经显示的不重复。 */
export function applyPcApproval(all: Approval[], m: Record<string, unknown>, inChat: Set<string>): Approval[] {
  const runId = String(m.runId ?? "");
  if (!runId) return all;
  if (m.op === "approval_gone") return all.filter((a) => a.runId !== runId || a.state === "done");
  if (inChat.has(runId) || all.some((a) => a.runId === runId)) return all;
  return [...all, { runId, command: String(m.command ?? ""), description: String(m.description ?? ""), state: "pending" }];
}

/** 电脑上的对话在等批准 (手机能处理, 只能单次)。处理完 3 秒后收起。 */
function PcApprovals({ items, setItems, request }: {
  items: Approval[];
  setItems: React.Dispatch<React.SetStateAction<Approval[]>>;
  request: RequestFn;
}) {
  if (items.length === 0) return null;
  const decide = (a: Approval, choice: "once" | "deny") => {
    setItems((all) => all.map((x) => (x.runId === a.runId ? { ...x, state: "sending" } : x)));
    request("approve", { runId: a.runId, choice }, (m) => {
      const failed = m.op === "error";
      setItems((all) => all.map((x) => (x.runId === a.runId ? {
        ...x, state: failed ? "pending" : "done",
        result: failed ? String(m.message) : choice === "once" ? "已允许这一次" : "已拒绝",
      } : x)));
      if (!failed) setTimeout(() => setItems((all) => all.filter((x) => x.runId !== a.runId)), 3000);
    });
  };
  return (
    <div style={{ padding: "8px 16px 0", maxHeight: "45%", overflowY: "auto" }}>
      {items.map((a) => (
        <div key={a.runId} style={{ border: "1px solid var(--status-warn)", borderRadius: 12, padding: 12, marginBottom: 8,
          background: "var(--bg-elev)" }}>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>电脑上的小鲶在等你批准</div>
          {a.description && <div style={{ fontSize: 14 }}>{a.description}</div>}
          {a.command && <code style={{ display: "block", fontSize: 12, margin: "6px 0", wordBreak: "break-all" }}>{a.command}</code>}
          {a.state === "done" ? <div style={muted}>{a.result}</div> : (
            <>
              <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                <button style={primary} disabled={a.state === "sending"} onClick={() => decide(a, "once")}>允许这一次</button>
                <button style={btn} disabled={a.state === "sending"} onClick={() => decide(a, "deny")}>拒绝</button>
              </div>
              {a.result && <div style={{ color: "var(--status-err)", fontSize: 13, marginTop: 4 }}>{a.result}</div>}
              <div style={{ ...muted, marginTop: 6 }}>「始终允许」只能在电脑上点</div>
            </>
          )}
        </div>
      ))}
    </div>
  );
}

function ChatView({ ready, request, onApproval }: {
  ready: boolean;
  request: RequestFn;
  onApproval: (runId: string) => void;
}) {
  const [msgs, setMsgs] = useState<ChatMsg[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [text, setText] = useState("");
  const [busyRid, setBusyRid] = useState<string | null>(null);
  const [sid, setSid] = useState<string | null>(null);
  const [sessions, setSessions] = useState<SessionItem[] | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => { endRef.current?.scrollIntoView?.({ block: "end" }); }, [msgs, approvals]);

  const patchLast = (f: (m: ChatMsg) => ChatMsg) =>
    setMsgs((all) => all.map((m, i) => (i === all.length - 1 && m.role === "assistant" ? f(m) : m)));

  const send = () => {
    const t = text.trim();
    if (!t || busyRid || !ready) return;
    setText("");
    setMsgs((all) => [...all, { role: "user", text: t }, { role: "assistant", text: "", status: "发给电脑…" }]);
    let heard = false;
    const rid = request("chat", { text: t, sid }, (m) => {
      heard = true;
      if (m.op === "delta") patchLast((x) => ({ ...x, text: x.text + String(m.text ?? ""), status: undefined }));
      else if (m.op === "status") patchLast((x) => ({ ...x, status: String(m.text ?? "") }));
      else if (m.op === "approval") {
        onApproval(String(m.runId));
        setApprovals((a) => [...a, { runId: String(m.runId), command: String(m.command ?? ""),
          description: String(m.description ?? ""), state: "pending" }]);
        patchLast((x) => ({ ...x, status: "等你批准…" }));
      } else if (m.op === "done") {
        if (typeof m.sid === "string") setSid(m.sid);
        patchLast((x) => ({ ...x, status: undefined }));
        setBusyRid(null);
      } else if (m.op === "error") {
        patchLast((x) => ({ ...x, status: undefined, error: String(m.message ?? "出错了") }));
        setBusyRid(null);
      }
    });
    setBusyRid(rid);
    setTimeout(() => {
      if (!heard) patchLast((x) => (x.status ? { ...x, status: "电脑还没回应 (可能休眠了), 再等等…" } : x));
    }, 20000);
  };

  const stop = () => {
    if (!busyRid) return;
    request("stop", { target: busyRid }, () => undefined);
    setBusyRid(null);
    patchLast((x) => ({ ...x, status: undefined }));
  };

  const decide = (a: Approval, choice: "once" | "deny") => {
    setApprovals((all) => all.map((x) => (x.runId === a.runId ? { ...x, state: "sending" } : x)));
    request("approve", { runId: a.runId, choice }, (m) => {
      setApprovals((all) => all.map((x) => (x.runId === a.runId ? {
        ...x, state: m.op === "error" ? "pending" : "done",
        result: m.op === "error" ? String(m.message) : choice === "once" ? "已允许这一次" : "已拒绝",
      } : x)));
      // 批准送到后小鲶接着做 (可能要几分钟), 把「等你批准…」换掉
      if (m.op !== "error") {
        setMsgs((all) => all.map((x, i) => (i === all.length - 1 && x.role === "assistant" && x.status === "等你批准…"
          ? { ...x, status: choice === "once" ? "已批准, 小鲶接着做…" : "已拒绝, 小鲶在收尾…" } : x)));
      }
    });
  };

  const openSessions = () => {
    setSessions([]);
    request("sessions", {}, (m) => {
      if (m.op === "result" && Array.isArray(m.data)) setSessions(m.data as SessionItem[]);
    });
  };

  const pickSession = (s: SessionItem | null) => {
    setSessions(null);
    setApprovals([]);
    setSid(s?.id ?? null);
    setMsgs([]);
    if (!s) return;
    request("history", { sid: s.id }, (m) => {
      if (m.op === "result" && Array.isArray(m.data)) {
        setMsgs((m.data as { role: "user" | "assistant"; content: string }[]).map((x) => ({ role: x.role, text: x.content })));
      }
    });
  };

  if (sessions) {
    return (
      <div style={{ padding: 16, overflowY: "auto", flex: 1 }}>
        <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
          <button style={primary} onClick={() => pickSession(null)}>+ 新对话</button>
          <button style={btn} onClick={() => setSessions(null)}>返回</button>
        </div>
        {sessions.length === 0 && <p style={muted}>读取中…</p>}
        {sessions.map((s) => (
          <div key={s.id} onClick={() => pickSession(s)}
            style={{ padding: "12px 0", borderBottom: "1px solid var(--border-soft)", cursor: "pointer" }}>
            <div style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{s.title}</div>
            <div style={muted}>{new Date(s.startedAt).toLocaleString()}</div>
          </div>
        ))}
      </div>
    );
  }

  return (
    <>
      <div style={{ display: "flex", gap: 8, padding: "8px 16px" }}>
        <button style={btn} onClick={openSessions} disabled={!ready}>历史对话</button>
        <button style={btn} onClick={() => pickSession(null)} disabled={!!busyRid}>新对话</button>
      </div>
      <div style={{ flex: 1, overflowY: "auto", padding: "0 16px" }}>
        {msgs.length === 0 && <p style={{ ...muted, textAlign: "center", marginTop: 40 }}>跟电脑上的小鲶说点什么</p>}
        {msgs.map((m, i) => (
          <div key={i} style={{ display: "flex", justifyContent: m.role === "user" ? "flex-end" : "flex-start", margin: "8px 0" }}>
            <div style={{ maxWidth: m.role === "user" ? "85%" : "100%", minWidth: 0, padding: "8px 12px", borderRadius: 12,
              whiteSpace: m.role === "user" ? "pre-wrap" : "normal", wordBreak: "break-word",
              background: m.role === "user" ? "var(--accent)" : "var(--bg-elev)", color: m.role === "user" ? "#fff" : "var(--text)",
              border: m.role === "user" ? "none" : "1px solid var(--border)", fontSize: 15 }}>
              {m.role === "assistant" && m.text.trim() ? <SimpleMarkdown source={m.text.trim()} breaks /> : m.text}
              {m.status && <div style={{ ...muted, marginTop: m.text ? 4 : 0 }}>{m.status}</div>}
              {m.error && <div style={{ color: "var(--status-err)", fontSize: 13 }}>{m.error}</div>}
            </div>
          </div>
        ))}
        {approvals.map((a) => (
          <div key={a.runId} style={{ border: "1px solid var(--status-warn)", borderRadius: 12, padding: 12, margin: "8px 0",
            background: "var(--bg-elev)" }}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>小鲶要做一件需要你同意的事</div>
            {a.description && <div style={{ fontSize: 14 }}>{a.description}</div>}
            {a.command && <code style={{ display: "block", fontSize: 12, margin: "6px 0", wordBreak: "break-all" }}>{a.command}</code>}
            {a.state === "done" ? (
              <div style={muted}>{a.result}</div>
            ) : (
              <>
                <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                  <button style={primary} disabled={a.state === "sending"} onClick={() => decide(a, "once")}>允许这一次</button>
                  <button style={btn} disabled={a.state === "sending"} onClick={() => decide(a, "deny")}>拒绝</button>
                </div>
                {a.result && <div style={{ color: "var(--status-err)", fontSize: 13, marginTop: 4 }}>{a.result}</div>}
                <div style={{ ...muted, marginTop: 6 }}>「始终允许」只能在电脑上点</div>
              </>
            )}
          </div>
        ))}
        <div ref={endRef} />
      </div>
      <div style={{ display: "flex", gap: 8, padding: "8px 16px", borderTop: "1px solid var(--border)" }}>
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={2} placeholder={ready ? "说点什么…" : "正在连接电脑…"}
          disabled={!ready} style={{ flex: 1, fontSize: 16, padding: 8, borderRadius: 8, border: "1px solid var(--border)",
            resize: "none", fontFamily: "inherit" }} />
        {busyRid ? <button style={btn} onClick={stop}>停止</button>
          : <button style={primary} onClick={send} disabled={!ready || !text.trim()}>发送</button>}
      </div>
    </>
  );
}

function BriefingView({ ready, request }: { ready: boolean; request: RequestFn }) {
  const [data, setData] = useState<Briefing | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    setError(null);
    request("briefing", {}, (m) => {
      if (m.op === "result") setData(m.data as Briefing);
      else if (m.op === "error") setError(String(m.message));
    });
  }, [request]);
  useEffect(() => { if (ready) load(); }, [ready, load]);
  return (
    <div style={{ flex: 1, overflowY: "auto", padding: 16 }}>
      <div style={{ display: "flex", alignItems: "center", marginBottom: 12 }}>
        <h3 style={{ margin: 0, flex: 1 }}>☀️ 早安简报</h3>
        <button style={btn} onClick={load} disabled={!ready}>刷新</button>
      </div>
      {error && <p style={{ color: "var(--status-err)" }}>{error}</p>}
      {!data ? <p style={muted}>{ready ? "读取中…" : "正在连接电脑…"}</p> : !data.computedAt ? (
        <p style={muted}>电脑上还没生成今天的简报。在电脑上打开一次「早安」页就会生成。</p>
      ) : (
        <>
          <p style={muted}>{new Date(data.computedAt).toLocaleString()} 生成 · 细节和处理在电脑上</p>
          {data.mainTasks.map((t, i) => (
            <div key={i} style={{ borderLeft: `4px solid ${URGENCY[t.urgency] ?? "var(--border)"}`, padding: "8px 12px",
              background: "var(--bg-elev)", borderRadius: 8, marginBottom: 8 }}>
              <div style={{ fontWeight: 600 }}>{t.title}</div>
              {t.reason && <div style={{ ...muted, marginTop: 2 }}>{t.reason}</div>}
            </div>
          ))}
          {data.handled.length > 0 && (
            <>
              <h4 style={{ marginBottom: 4 }}>已经替你处理的</h4>
              {data.handled.map((h, i) => <div key={i} style={muted}>· {h.category} {h.count} 件</div>)}
            </>
          )}
        </>
      )}
    </div>
  );
}
