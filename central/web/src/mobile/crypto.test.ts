/** 手机端加密层 —— 跟 Companion crypto.rs 互相钉住格式 (同一组固定向量, 两边测试都断言)。 */
import { describe, expect, it } from "vitest";

import { b64, kindOf, keyPairFromSecret, open, Reassembler, sas, seal, sharedKey, split, KIND_SESSION } from "./crypto";

const skA = new Uint8Array(32).fill(1);
const skB = new Uint8Array(32).fill(2);

describe("mobile crypto", () => {
  it("公钥跟 Rust 那边一致", () => {
    expect(b64(keyPairFromSecret(skA).publicKey)).toBe("pOCSkrZRwni5dyxWn1+puxPZBrRqtoyd+dwrRAn4ogk=");
  });

  it("能解开固定向量 (Rust 测试解的是同一串)", () => {
    const d = "AgMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA2d1x9clQSLyZsnX8rBUkKiASgtpQmVS";
    const got = kindOf(d)!;
    expect(got.kind).toBe(2);
    const k = sharedKey(keyPairFromSecret(skA).publicKey, skB);
    expect(new TextDecoder().decode(open(k, got.raw)!)).toBe("catfish");
  });

  it("加密解密来回, 别人的钥匙解不开", () => {
    const a = keyPairFromSecret(skA);
    const b = keyPairFromSecret(skB);
    const d = seal(sharedKey(b.publicKey, a.secretKey), KIND_SESSION, new TextEncoder().encode("你好"));
    const got = kindOf(d)!;
    expect(new TextDecoder().decode(open(sharedKey(a.publicKey, b.secretKey), got.raw)!)).toBe("你好");
    const c = keyPairFromSecret(new Uint8Array(32).fill(5));
    expect(open(sharedKey(a.publicKey, c.secretKey), got.raw)).toBeNull();
  });

  it("验证码跟 Rust 一致 (7…/9… 那组)", async () => {
    expect(await sas(new Uint8Array(32).fill(7), new Uint8Array(32).fill(9))).toBe("025159");
  });

  it("大消息分段再拼回, 重放丢掉", () => {
    const body = new TextEncoder().encode("鲶".repeat(3000));
    const { pieces, seq } = split(body, 5, 0);
    expect(pieces.length).toBe(3);
    expect(seq).toBe(3);
    const r = new Reassembler();
    expect(r.push(pieces[0])).toBeNull();
    expect(r.push(pieces[1])).toBeNull();
    expect(r.push(pieces[2])).toEqual(body);
    expect(r.push(pieces[2])).toBeNull();
  });
});
