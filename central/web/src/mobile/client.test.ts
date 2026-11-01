/** 手机端连接逻辑: 假中转站 + 用 crypto.ts 模拟的电脑 (电脑那头的真实现在 Companion mobile_bridge, 格式由固定向量钉住)。 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  KIND_LONG, KIND_SESSION, b64, kindOf, newKeyPair, open, Reassembler, sas, seal, sharedKey, split, unb64,
} from "./crypto";
import { MobileClient, type ClientEvent } from "./client";

class FakeWS {
  static OPEN = 1;
  static last: FakeWS | null = null;
  readyState = 0;
  sent: Record<string, unknown>[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  constructor(public url: string) {
    FakeWS.last = this;
    queueMicrotask(() => {
      this.readyState = 1;
      this.onopen?.();
    });
  }
  send(s: string) {
    this.sent.push(JSON.parse(s));
  }
  close() {
    this.readyState = 3;
    this.onclose?.();
  }
  deliver(frame: unknown) {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }
  take(t: string) {
    const i = this.sent.findIndex((f) => f.t === t);
    expect(i, `应该发了 ${t}`).toBeGreaterThanOrEqual(0);
    return this.sent.splice(i, 1)[0];
  }
}

const tick = () => new Promise((r) => setTimeout(r, 0));

beforeEach(() => {
  (globalThis as Record<string, unknown>).WebSocket = FakeWS;
  (globalThis as Record<string, unknown>).window = { location: { protocol: "https:", host: "catfish.test" } };
});
afterEach(() => {
  delete (globalThis as Record<string, unknown>).window;
});

describe("MobileClient", () => {
  it("登录帧带令牌, 地址同源 wss", async () => {
    const c = new MobileClient(async () => "tok");
    await c.connect();
    await tick();
    const ws = FakeWS.last!;
    expect(ws.url).toBe("wss://catfish.test/relay/ws");
    expect(ws.take("auth")).toMatchObject({ token: "tok", role: "phone", device: c.device });
  });

  it("配对 → 握手 → 请求回复; 电脑重启后重新握手", async () => {
    const c = new MobileClient(async () => "tok");
    const events: ClientEvent[] = [];
    c.on((e) => events.push(e));
    await c.connect();
    await tick();
    const ws = FakeWS.last!;
    ws.deliver({ t: "ready", device: c.device, max_phones: 3 });
    ws.deliver({ t: "peers", peers: [{ device: "pc-1", name: "办公室电脑", paired: false }], max_phones: 3 });
    expect(c.peers).toEqual([{ device: "pc-1", name: "办公室电脑", paired: false, online: true }]);

    // 配对: 手机的公钥 → 电脑回公钥 → 两边验证码一样
    const pc = newKeyPair();
    c.pair("pc-1");
    const req = ws.take("pair_request");
    const phonePk = unb64(String(req.pk));
    ws.deliver({ t: "pair_offer", from: "pc-1", pk: b64(pc.publicKey) });
    await tick();
    await tick();
    const code = events.find((e) => e.type === "pair_code");
    expect(code).toMatchObject({ code: await sas(phonePk, pc.publicKey) });
    ws.deliver({ t: "pair_result", from: "pc-1", ok: true });
    expect(events.at(-1)).toMatchObject({ type: "pair_result", ok: true });

    // 握手: 发 hello (长期钥匙)
    c.use("pc-1");
    const long = sharedKey(phonePk, pc.secretKey);
    const helloRaw = kindOf(String(ws.take("msg").d))!;
    expect(helloRaw.kind).toBe(KIND_LONG);
    const hello = JSON.parse(new TextDecoder().decode(open(long, helloRaw.raw)!));
    expect(hello.op).toBe("hello");

    // 握手前发的请求先排队
    const rid = c.request("ping");
    expect(ws.sent.filter((f) => f.t === "msg")).toHaveLength(0);

    const eph = newKeyPair();
    const ack = { op: "hello_ack", eph: b64(eph.publicKey), sid: hello.sid, name: "办公室电脑" };
    ws.deliver({ t: "msg", from: "pc-1", d: seal(long, KIND_LONG, new TextEncoder().encode(JSON.stringify(ack))) });
    expect(events.at(-1)).toEqual({ type: "session", device: "pc-1", ready: true });

    // 排队的请求现在发出, 电脑用临时钥匙解得开
    const sess = sharedKey(unb64(hello.eph), eph.secretKey);
    const reqRaw = kindOf(String(ws.take("msg").d))!;
    expect(reqRaw.kind).toBe(KIND_SESSION);
    const body = new Reassembler().push(open(sess, reqRaw.raw)!)!;
    expect(JSON.parse(new TextDecoder().decode(body))).toEqual({ op: "ping", rid });

    // 电脑回一条大消息 (分段), 手机拼回来按 rid 交出去
    const big = { op: "result", rid, data: "鲶".repeat(3000) };
    const { pieces } = split(new TextEncoder().encode(JSON.stringify(big)), 1, 0);
    for (const p of pieces) ws.deliver({ t: "msg", from: "pc-1", d: seal(sess, KIND_SESSION, p) });
    expect(events.at(-1)).toEqual({ type: "message", device: "pc-1", m: big });

    // 电脑重启 (会话没了) → rehello → 手机重新 hello
    ws.deliver({ t: "msg", from: "pc-1", d: seal(long, KIND_LONG, new TextEncoder().encode('{"op":"rehello"}')) });
    expect(events.at(-1)).toEqual({ type: "session", device: "pc-1", ready: false });
    const again = kindOf(String(ws.take("msg").d))!;
    expect(JSON.parse(new TextDecoder().decode(open(long, again.raw)!)).op).toBe("hello");
  });

  it("电脑撤销 → 忘掉这台电脑", async () => {
    const c = new MobileClient(async () => "tok");
    const events: ClientEvent[] = [];
    c.on((e) => events.push(e));
    await c.connect();
    await tick();
    FakeWS.last!.deliver({ t: "revoked", from: "pc-1" });
    expect(events.at(-1)).toEqual({ type: "revoked", device: "pc-1" });
    expect(c.pairedPcs()["pc-1"]).toBeUndefined();
  });

  it("超上限: 配对失败并说清楚", async () => {
    const c = new MobileClient(async () => "tok");
    const events: ClientEvent[] = [];
    c.on((e) => events.push(e));
    await c.connect();
    await tick();
    c.pair("pc-1");
    FakeWS.last!.deliver({ t: "error", code: "phone_limit", max_phones: 3 });
    const r = events.at(-1);
    expect(r).toMatchObject({ type: "pair_result", ok: false });
    expect((r as { reason: string }).reason).toContain("3 台");
  });
});
