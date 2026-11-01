/** 手机端加密层 —— 跟 Companion `src-tauri/src/commands/mobile_bridge/crypto.rs` 一字不差地对应
 * (docs/MOBILE-CLIENT-DESIGN.md §4)。
 *
 * 中转站看到的 `d` = base64( 类型 1 字节 | nonce 24 字节 | NaCl box 密文 )。
 *   类型 1: 配对时交换的长期公钥加密, 只用于握手 (hello / hello_ack / rehello)
 *   类型 2: 每次连上换的临时密钥, 断开后旧消息解不开
 * 类型 2 的明文 = seq(u32) | mid(u32) | idx(u16) | cnt(u16) | 一段 UTF-8 JSON (大消息拆段)。
 */
import nacl from "tweetnacl";

export const KIND_LONG = 1;
export const KIND_SESSION = 2;
export const PIECE_BYTES = 4000;
const HEADER = 12;

export interface KeyPair {
  publicKey: Uint8Array;
  secretKey: Uint8Array;
}

export function b64(bytes: Uint8Array): string {
  let s = "";
  for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
  return btoa(s);
}

export function unb64(s: string): Uint8Array {
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export const newKeyPair = (): KeyPair => nacl.box.keyPair();
export const keyPairFromSecret = (sk: Uint8Array): KeyPair => nacl.box.keyPair.fromSecretKey(sk);

/** 预先算好的共享钥匙 (对方公钥 + 自己私钥) */
export const sharedKey = (peerPk: Uint8Array, mySk: Uint8Array): Uint8Array => nacl.box.before(peerPk, mySk);

export function seal(shared: Uint8Array, kind: number, plain: Uint8Array): string {
  const nonce = nacl.randomBytes(24);
  const ct = nacl.box.after(plain, nonce, shared);
  const out = new Uint8Array(1 + 24 + ct.length);
  out[0] = kind;
  out.set(nonce, 1);
  out.set(ct, 25);
  return b64(out);
}

/** 解开前先看类型 (决定用哪把钥匙) */
export function kindOf(d: string): { kind: number; raw: Uint8Array } | null {
  try {
    const raw = unb64(d);
    if (raw.length < 1 + 24 + 16) return null;
    return { kind: raw[0], raw };
  } catch {
    return null;
  }
}

export function open(shared: Uint8Array, raw: Uint8Array): Uint8Array | null {
  return nacl.box.open.after(raw.subarray(25), raw.subarray(1, 25), shared);
}

/** 配对验证码 (6 位): sha256("catfish-pair-v1" | 手机公钥 | 电脑公钥) 前 4 字节 mod 1000000 */
export async function sas(phonePk: Uint8Array, pcPk: Uint8Array): Promise<string> {
  const tag = new TextEncoder().encode("catfish-pair-v1");
  const buf = new Uint8Array(tag.length + 64);
  buf.set(tag, 0);
  buf.set(phonePk, tag.length);
  buf.set(pcPk, tag.length + 32);
  const h = new Uint8Array(await crypto.subtle.digest("SHA-256", buf));
  const n = ((h[0] << 24) >>> 0) + (h[1] << 16) + (h[2] << 8) + h[3];
  return String(n % 1_000_000).padStart(6, "0");
}

/** 一条消息 → 若干段 (每段一个新 seq); 返回新的 seq */
export function split(body: Uint8Array, mid: number, seq: number): { pieces: Uint8Array[]; seq: number } {
  const chunks: Uint8Array[] = [];
  if (body.length === 0) chunks.push(new Uint8Array(0));
  for (let i = 0; i < body.length; i += PIECE_BYTES) chunks.push(body.subarray(i, i + PIECE_BYTES));
  const pieces = chunks.map((c, i) => {
    seq += 1;
    const out = new Uint8Array(HEADER + c.length);
    const v = new DataView(out.buffer);
    v.setUint32(0, seq);
    v.setUint32(4, mid);
    v.setUint16(8, i);
    v.setUint16(10, chunks.length);
    out.set(c, HEADER);
    return out;
  });
  return { pieces, seq };
}

/** 收端: 查 seq (防重放)、拼段。拼齐返回整条, 没齐 / 不对返回 null。 */
export class Reassembler {
  private lastSeq = 0;
  private parts = new Map<number, (Uint8Array | null)[]>();

  push(plain: Uint8Array): Uint8Array | null {
    if (plain.length < HEADER) return null;
    const v = new DataView(plain.buffer, plain.byteOffset, plain.byteLength);
    const seq = v.getUint32(0);
    const mid = v.getUint32(4);
    const idx = v.getUint16(8);
    const cnt = v.getUint16(10);
    if (seq <= this.lastSeq || cnt === 0 || idx >= cnt || cnt > 256) return null;
    this.lastSeq = seq;
    const body = plain.slice(HEADER);
    if (cnt === 1) return body;
    if (!this.parts.has(mid) && this.parts.size >= 8) this.parts.clear();
    let slot = this.parts.get(mid);
    if (!slot) {
      slot = new Array(cnt).fill(null);
      this.parts.set(mid, slot);
    }
    if (slot.length !== cnt) {
      this.parts.delete(mid);
      return null;
    }
    slot[idx] = body;
    if (slot.every((p) => p !== null)) {
      this.parts.delete(mid);
      const total = slot.reduce((n, p) => n + p!.length, 0);
      const out = new Uint8Array(total);
      let off = 0;
      for (const p of slot) {
        out.set(p!, off);
        off += p!.length;
      }
      return out;
    }
    return null;
  }
}
