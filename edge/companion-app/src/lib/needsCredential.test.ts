/**
 * 「这个站点还没存过密码」信号的判据。
 *
 * 重点在**认错的代价不对称**: 漏认 = 员工看到一条报错，自己去 📚 里存，能走通;
 * 误认 = 凭空弹一个密码输入框。后者是要人交出密码的界面，宽一点都不行。
 */
import { describe, expect, it } from "vitest";

import { parseNeedsCredential, retryPrompt } from "./needsCredential";

/** tool-bridge 真实返回的形状 (edge/tool-bridge 的 _browser_fill_impl)。 */
const REAL = {
  type: "error",
  error: "neis.ffcs.cn 还没保存过登录密码",
  needs_credential: true,
  site: "neis.ffcs.cn",
  page_url: "http://neis.ffcs.cn/cas/login?service=x",
  page_title: "福建电信 - 统一身份认证",
  selector: "#password",
  known_sites: ["eis.ffcs.cn"],
  summary: "⚠ neis.ffcs.cn 还没存过密码. 请在下面存一次, 我再接着填.",
};

describe("认出来", () => {
  it("真实返回 (对象)", () => {
    const got = parseNeedsCredential(REAL);
    expect(got).toEqual({
      site: "neis.ffcs.cn",
      pageUrl: "http://neis.ffcs.cn/cas/login?service=x",
      pageTitle: "福建电信 - 统一身份认证",
      selector: "#password",
      knownSites: ["eis.ffcs.cn"],
    });
  });

  it("真实返回 (JSON 字符串)", () => {
    // ★ runOneRound 给 ChatToolCall 的是 JSON.stringify 之后的字符串,
    //   不是对象 —— 只测对象那条会漏掉真正走的那条路。
    expect(parseNeedsCredential(JSON.stringify(REAL))?.site).toBe("neis.ffcs.cn");
  });

  it("可选字段缺了也认，只是空", () => {
    const got = parseNeedsCredential({ needs_credential: true, site: "a.b.cn" });
    expect(got).toEqual({
      site: "a.b.cn",
      pageUrl: "",
      pageTitle: "",
      selector: "",
      knownSites: [],
    });
  });

  it("known_sites 里的脏值滤掉", () => {
    const got = parseNeedsCredential({
      needs_credential: true,
      site: "a.b.cn",
      known_sites: ["x.cn", "", null, 42, "  y.cn  "],
    });
    expect(got?.knownSites).toEqual(["x.cn", "y.cn"]);
  });
});

describe("不许认错", () => {
  it("needs_credential 必须恰好是 true", () => {
    // 字符串 "true" / 1 说明来源不是我们这条路。形状对不上就不猜。
    for (const v of ["true", 1, "1", {}, [], "yes"]) {
      expect(parseNeedsCredential({ needs_credential: v, site: "a.b.cn" })).toBeNull();
    }
  });

  it("没有 site 就不认", () => {
    // 没站点存不进去 (Rust 侧也会拒) —— 弹一个存不进去的框更糟
    expect(parseNeedsCredential({ needs_credential: true })).toBeNull();
    expect(parseNeedsCredential({ needs_credential: true, site: "   " })).toBeNull();
  });

  it("普通的 tool 报错不认", () => {
    expect(
      parseNeedsCredential({
        type: "error",
        error: "等不到 '#password' 可写 (超时 10000ms)",
      }),
    ).toBeNull();
  });

  it("★ 文本里提到这几个字不算", () => {
    // 这条就是不用正则的理由。员工在聊天里打出这几个字、或者某个报错文本
    // 里带上它, 用正则就会凭空弹一个密码输入框。
    for (const s of [
      "needs_credential",
      '这个工具会返回 needs_credential: true, site: "x.cn"',
      "报错: needs_credential=true",
    ]) {
      expect(parseNeedsCredential(s)).toBeNull();
    }
  });

  it("垃圾输入一律 null, 不抛", () => {
    for (const v of [null, undefined, "", "   ", "不是 JSON", "{坏的", 42, [REAL], true]) {
      expect(() => parseNeedsCredential(v)).not.toThrow();
      expect(parseNeedsCredential(v)).toBeNull();
    }
  });
});

describe("重试提示", () => {
  it("说清是哪个站点", () => {
    expect(retryPrompt("neis.ffcs.cn")).toContain("neis.ffcs.cn");
  });

  it("★★★ 绝不能带密码", () => {
    // 这句话会作为普通用户消息进聊天、进历史库、进下一轮模型上下文。
    // 密码只走 Tauri IPC 到系统凭据库。
    //
    // 这条测试挡的是"顺手把密码拼进去更方便"那种改法 —— 一旦拼进去,
    // 密码就同时落在 state.db、SSE 流和模型上下文里, 撤销不了。
    const text = retryPrompt("eis.ffcs.cn");
    expect(text).not.toMatch(/密码是|password=|凭据值|[:：]\s*\S*[Pp]assw/);
    // 函数签名里就没有密码参数 —— 想拼也拼不进来
    expect(retryPrompt.length).toBe(1);
  });
});
