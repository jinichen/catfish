/** 手机网页 ↔ 中央中转站 ↔ 员工电脑 (docs/MOBILE-CLIENT-DESIGN.md §2 / §4)。
 *
 * 本机 (浏览器 localStorage) 存:
 *   设备号、这台手机的长期密钥、配过的电脑 (名字 + 公钥)
 * 中转站只看到密文和设备号。电脑那边的协议在 Companion `commands/mobile_bridge/`。
 *
 * 流程:
 *   pair(电脑)   发自己的公钥 → 收电脑公钥 → 两边各算 6 位验证码 → 员工在电脑上点允许 → 记下电脑
 *   use(电脑)    每次连上都握手换临时密钥 (hello / hello_ack), 之后的请求都用临时密钥
 *   request(op)  发一条白名单请求, 回来的按 rid 分发
 */
import {
  KIND_LONG,
  KIND_SESSION,
  b64,
  keyPairFromSecret,
  kindOf,
  newKeyPair,
  open,
  Reassembler,
  sas,
  seal,
  sharedKey,
  split,
  unb64,
  type KeyPair,
} from "./crypto";

const LS = "catfish.mobile.";

export interface Peer {
  device: string;
  name: string;
  /** 跟这台手机配过 */
  paired: boolean;
  online: boolean;
}

export interface PairedPc {
  name: string;
  pk: string;
}

export type ClientEvent =
  | { type: "status"; status: ClientStatus; detail?: string }
  | { type: "peers" }
  | { type: "pair_code"; device: string; code: string }
  | { type: "pair_result"; device: string; ok: boolean; reason?: string }
  | { type: "session"; device: string; ready: boolean }
  | { type: "revoked"; device: string }
  | { type: "offline"; device: string }
  | { type: "message"; device: string; m: Record<string, unknown> };

export type ClientStatus = "connecting" | "ready" | "closed" | "error";

function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

/** 存不进 localStorage 时 (隐私模式等) 先放内存: 这次能用, 关掉页面后要重新配对 */
const memory = new Map<string, string>();

function load<T>(key: string, fallback: T): T {
  try {
    const raw = storage()?.getItem(LS + key) ?? memory.get(LS + key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function save(key: string, value: unknown) {
  const raw = JSON.stringify(value);
  memory.set(LS + key, raw);
  try {
    storage()?.setItem(LS + key, raw);
  } catch {
    // 只留在内存里
  }
}

function randomHex(n: number): string {
  const b = new Uint8Array(n);
  crypto.getRandomValues(b);
  return Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
}

export function deviceName(ua = typeof navigator === "undefined" ? "" : navigator.userAgent): string {
  if (/iPhone/.test(ua)) return "iPhone";
  if (/iPad/.test(ua)) return "iPad";
  if (/HarmonyOS/i.test(ua)) return "鸿蒙手机";
  if (/Android/.test(ua)) return "安卓手机";
  return "手机浏览器";
}

/** 同源的 /relay/ws (开发时 vite 代理到 127.0.0.1:8999, 正式环境 nginx 反代到网关) */
export function relayUrl(loc: Location = window.location): string {
  return `${loc.protocol === "https:" ? "wss" : "ws"}://${loc.host}/relay/ws`;
}

interface Session {
  shared: Uint8Array | null;
  eph: KeyPair;
  sid: string;
  sendSeq: number;
  nextMid: number;
  recv: Reassembler;
  queue: Uint8Array[];
}

export class MobileClient {
  readonly device: string;
  private keys: KeyPair;
  private ws: WebSocket | null = null;
  private listeners = new Set<(e: ClientEvent) => void>();
  private closedByUser = false;
  private backoff = 2000;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private pairing = new Map<string, Uint8Array>(); // 电脑 → 它的公钥 (等员工点允许)
  private session: { device: string; s: Session } | null = null;
  status: ClientStatus = "closed";
  peers: Peer[] = [];
  maxPhones: number | null = null;

  constructor(private getToken: () => Promise<string | null>) {
    let device = load<string>("device", "");
    if (!/^[A-Za-z0-9_-]{8,64}$/.test(device)) {
      device = `ph-${randomHex(12)}`;
      save("device", device);
    }
    this.device = device;
    const sk = load<string>("secret", "");
    if (sk) {
      this.keys = keyPairFromSecret(unb64(sk));
    } else {
      this.keys = newKeyPair();
      save("secret", b64(this.keys.secretKey));
    }
  }

  get publicKey(): string {
    return b64(this.keys.publicKey);
  }

  pairedPcs(): Record<string, PairedPc> {
    return load<Record<string, PairedPc>>("pcs", {});
  }

  forgetPc(device: string) {
    const pcs = this.pairedPcs();
    delete pcs[device];
    save("pcs", pcs);
    if (this.session?.device === device) this.session = null;
    this.mergePeers(this.peers.filter((p) => p.online));
  }

  on(fn: (e: ClientEvent) => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  private emit(e: ClientEvent) {
    for (const fn of this.listeners) fn(e);
  }

  private setStatus(status: ClientStatus, detail?: string) {
    this.status = status;
    this.emit({ type: "status", status, detail });
  }

  private send(frame: Record<string, unknown>) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(frame));
  }

  // ── 连接 ───────────────────────────────────────────────

  async connect(): Promise<void> {
    this.closedByUser = false;
    if (this.ws && this.ws.readyState <= WebSocket.OPEN) return;
    const token = await this.getToken();
    if (!token) {
      this.setStatus("error", "登录过期了, 请重新登录");
      return;
    }
    this.setStatus("connecting");
    const ws = new WebSocket(relayUrl());
    this.ws = ws;
    ws.onopen = () => {
      ws.send(JSON.stringify({ t: "auth", token, role: "phone", device: this.device, name: deviceName() }));
    };
    ws.onmessage = (ev) => {
      if (typeof ev.data === "string") void this.onFrame(ev.data);
    };
    ws.onclose = () => {
      if (this.ws !== ws) return;
      this.ws = null;
      if (this.pingTimer) clearInterval(this.pingTimer);
      if (this.session) this.session.s.shared = null;
      if (this.closedByUser) {
        if (this.status !== "error") this.setStatus("closed");
        return;
      }
      if (this.status !== "error") this.setStatus("connecting", "连接断了, 正在重连…");
      this.timer = setTimeout(() => void this.connect(), this.backoff);
      this.backoff = Math.min(this.backoff * 2, 30000);
    };
  }

  close() {
    this.closedByUser = true;
    if (this.timer) clearTimeout(this.timer);
    this.ws?.close();
  }

  /** 手机从后台切回来 (浏览器常把后台的连接掐掉): 立刻重连, 不等退避 */
  wake() {
    if (this.closedByUser) return;
    if (!this.ws || this.ws.readyState > WebSocket.OPEN) {
      if (this.timer) clearTimeout(this.timer);
      this.backoff = 2000;
      void this.connect();
    }
  }

  refreshPeers() {
    this.send({ t: "peers" });
  }

  private mergePeers(online: Peer[]) {
    const pcs = this.pairedPcs();
    const seen = new Set(online.map((p) => p.device));
    const offline = Object.entries(pcs)
      .filter(([d]) => !seen.has(d))
      .map(([device, pc]) => ({ device, name: pc.name, paired: true, online: false }));
    this.peers = [...online.map((p) => ({ ...p, paired: p.paired && !!pcs[p.device] })), ...offline];
    this.emit({ type: "peers" });
  }

  private async onFrame(raw: string) {
    let f: Record<string, unknown>;
    try {
      f = JSON.parse(raw);
    } catch {
      return;
    }
    const from = typeof f.from === "string" ? f.from : "";
    switch (f.t) {
      case "ready":
        this.backoff = 2000;
        this.maxPhones = typeof f.max_phones === "number" ? f.max_phones : null;
        this.setStatus("ready");
        this.pingTimer = setInterval(() => this.send({ t: "ping" }), 25000);
        this.refreshPeers();
        if (this.session) this.hello(this.session.device);
        break;
      case "peers": {
        const list = Array.isArray(f.peers) ? (f.peers as Array<Record<string, unknown>>) : [];
        this.mergePeers(list.map((p) => ({
          device: String(p.device ?? ""), name: String(p.name ?? "电脑"), paired: p.paired === true, online: true,
        })));
        break;
      }
      case "pair_offer":
        await this.onPairOffer(from, String(f.pk ?? ""));
        break;
      case "pair_result":
        this.onPairResult(from, f.ok === true);
        break;
      case "revoked":
        this.forgetPc(from);
        this.emit({ type: "revoked", device: from });
        break;
      case "offline":
        this.emit({ type: "offline", device: String(f.device ?? "") });
        break;
      case "msg":
        this.onMsg(from, String(f.d ?? ""));
        break;
      case "error":
        this.onError(f);
        break;
    }
  }

  private onError(f: Record<string, unknown>) {
    const code = String(f.code ?? "");
    if (code === "phone_limit") {
      for (const device of this.pairing.keys()) {
        this.emit({ type: "pair_result", device, ok: false,
          reason: `你已经配了 ${f.max_phones ?? ""} 台手机, 到了单位设置的上限。先在电脑上撤销一台。` });
      }
      this.pairing.clear();
    } else if (code === "pair_rate_limited") {
      for (const device of this.pairing.keys()) {
        this.emit({ type: "pair_result", device, ok: false, reason: "配对试得太频繁了, 过 10 分钟再试" });
      }
      this.pairing.clear();
    } else if (code === "mobile_disabled") {
      this.closedByUser = true;
      this.setStatus("error", "单位没有开放手机端");
    } else if (code === "auth_failed") {
      // 令牌不被认: 重连也没用, 停下来让员工重新登录
      this.closedByUser = true;
      this.setStatus("error", "登录失效了, 请重新登录");
    } else if (code === "not_paired") {
      const device = String(f.device ?? "");
      this.forgetPc(device);
      this.emit({ type: "revoked", device });
    }
  }

  // ── 配对 ───────────────────────────────────────────────

  pair(pc: string) {
    this.pairing.set(pc, new Uint8Array(0));
    this.send({ t: "pair_request", to: pc, pk: this.publicKey });
  }

  private async onPairOffer(pc: string, pk: string) {
    if (!this.pairing.has(pc)) return;
    let pcPk: Uint8Array;
    try {
      pcPk = unb64(pk);
    } catch {
      return;
    }
    if (pcPk.length !== 32) return;
    this.pairing.set(pc, pcPk);
    this.emit({ type: "pair_code", device: pc, code: await sas(this.keys.publicKey, pcPk) });
  }

  private onPairResult(pc: string, ok: boolean) {
    const pcPk = this.pairing.get(pc);
    this.pairing.delete(pc);
    if (ok && pcPk && pcPk.length === 32) {
      const name = this.peers.find((p) => p.device === pc)?.name ?? "电脑";
      save("pcs", { ...this.pairedPcs(), [pc]: { name, pk: b64(pcPk) } });
      this.refreshPeers();
    }
    this.emit({ type: "pair_result", device: pc, ok: ok && !!pcPk, reason: ok ? undefined : "电脑上点了拒绝" });
  }

  // ── 会话 (握手 + 加密请求) ──────────────────────────────

  private longKey(pc: string): Uint8Array | null {
    const info = this.pairedPcs()[pc];
    return info ? sharedKey(unb64(info.pk), this.keys.secretKey) : null;
  }

  use(pc: string) {
    this.session = {
      device: pc,
      s: { shared: null, eph: newKeyPair(), sid: randomHex(8), sendSeq: 0, nextMid: 0, recv: new Reassembler(), queue: [] },
    };
    this.hello(pc);
  }

  get current(): string | null {
    return this.session?.device ?? null;
  }

  private hello(pc: string) {
    const long = this.longKey(pc);
    if (!long || !this.session) return;
    const s = this.session.s;
    s.eph = newKeyPair();
    s.sid = randomHex(8);
    s.shared = null;
    s.sendSeq = 0;
    s.recv = new Reassembler();
    const body = new TextEncoder().encode(JSON.stringify({ op: "hello", eph: b64(s.eph.publicKey), sid: s.sid }));
    this.send({ t: "msg", to: pc, d: seal(long, KIND_LONG, body) });
  }

  private onMsg(pc: string, d: string) {
    const got = kindOf(d);
    if (!got || this.session?.device !== pc) return;
    const s = this.session.s;
    if (got.kind === KIND_LONG) {
      const long = this.longKey(pc);
      const plain = long && open(long, got.raw);
      if (!plain) return;
      let m: Record<string, unknown>;
      try {
        m = JSON.parse(new TextDecoder().decode(plain));
      } catch {
        return;
      }
      if (m.op === "hello_ack" && m.sid === s.sid && typeof m.eph === "string") {
        s.shared = sharedKey(unb64(m.eph), s.eph.secretKey);
        if (typeof m.name === "string" && m.name) {
          const pcs = this.pairedPcs();
          if (pcs[pc]) save("pcs", { ...pcs, [pc]: { ...pcs[pc], name: m.name } });
        }
        this.emit({ type: "session", device: pc, ready: true });
        const queued = s.queue.splice(0);
        for (const body of queued) this.sendBody(body);
      } else if (m.op === "rehello") {
        this.emit({ type: "session", device: pc, ready: false });
        this.hello(pc);
      }
      return;
    }
    if (got.kind !== KIND_SESSION || !s.shared) return;
    const plain = open(s.shared, got.raw);
    if (!plain) return;
    const body = s.recv.push(plain);
    if (!body) return;
    try {
      this.emit({ type: "message", device: pc, m: JSON.parse(new TextDecoder().decode(body)) });
    } catch {
      // 格式不对的丢掉
    }
  }

  private sendBody(body: Uint8Array) {
    if (!this.session) return;
    const { device, s } = this.session;
    if (!s.shared) {
      s.queue.push(body);
      return;
    }
    s.nextMid += 1;
    const { pieces, seq } = split(body, s.nextMid, s.sendSeq);
    s.sendSeq = seq;
    for (const p of pieces) this.send({ t: "msg", to: device, d: seal(s.shared, KIND_SESSION, p) });
  }

  /** 发一条请求; 返回 rid, 回复在 "message" 事件里按 rid 认 */
  request(op: string, payload: Record<string, unknown> = {}): string {
    const rid = randomHex(6);
    this.sendBody(new TextEncoder().encode(JSON.stringify({ ...payload, op, rid })));
    return rid;
  }
}
