import { describe, expect, it } from "vitest";

import {
  needsEmailSourceSetup,
  parseEmailSourceDiscovery,
  readyEmailSources,
} from "./emailSourceDiscovery";

describe("email source discovery", () => {
  it("parses independent Outlook and .eml-dir statuses", () => {
    const result = parseEmailSourceDiscovery(JSON.stringify({
      platform: "Windows",
      ready_client: "eml-dir",
      selected_client: "eml-dir",
      sources: [
        { client: "outlook-win", status: "unavailable", accounts: [], root: null, reason: "未配置" },
        { client: "eml-dir", status: "ready", accounts: [], root: "E:\\邮件导出", reason: null },
      ],
    }));

    expect(readyEmailSources(result)).toHaveLength(1);
    expect(readyEmailSources(result)[0].client).toBe("eml-dir");
  });

  it("rejects malformed discovery output", () => {
    expect(() => parseEmailSourceDiscovery("{}")).toThrow("缺少来源信息");
  });

  it("rejects an unknown selected client", () => {
    expect(() => parseEmailSourceDiscovery(JSON.stringify({
      platform: "Windows",
      ready_client: null,
      selected_client: "unknown",
      sources: [],
    }))).toThrow("已选来源无效");
  });
});

/** macOS 上 discovery 的真实形态: 它只认得 imap / outlook-win / eml-dir,
 *  **Apple Mail 根本不在这个列表里** —— 这正是判据换掉的原因。 */
const MAC = parseEmailSourceDiscovery(JSON.stringify({
  platform: "Darwin",
  ready_client: null,
  selected_client: null,
  sources: [
    { client: "imap", status: "unavailable", accounts: [], root: null, reason: "未配置" },
  ],
}));

describe("needsEmailSourceSetup —— 判据是有没有拿到邮件, 不是认得几个来源", () => {
  it("Apple Mail 好好收着 342 封信时不该摆配置卡片", () => {
    // 鸿波 catch "现在同时从客户端和 IMAP 一起吗？会打架的" 就是这一条:
    // 老判据 readySourceCount === 0 在 macOS 上永远成立 (Apple Mail 不在
    // discovery 的列表里), 于是卡片一直显示, 邀请员工去制造来源冲突。
    expect(needsEmailSourceSetup({
      discovery: MAC, noMailAtAll: false, failed: false,
    })).toBe(false);
  });

  it("一封都没拿到才摆出来", () => {
    expect(needsEmailSourceSetup({
      discovery: MAC, noMailAtAll: true, failed: false,
    })).toBe(true);
  });

  it("拉取失败也摆出来 —— 那时员工最需要另一条路", () => {
    expect(needsEmailSourceSetup({
      discovery: MAC, noMailAtAll: false, failed: true,
    })).toBe(true);
  });

  it("还没探测完就先别摆, 免得闪一下", () => {
    expect(needsEmailSourceSetup({
      discovery: null, noMailAtAll: true, failed: true,
    })).toBe(false);
  });

  it("认得好几个来源又没选过时要让员工挑", () => {
    const two = parseEmailSourceDiscovery(JSON.stringify({
      platform: "Windows",
      ready_client: null,
      selected_client: null,
      sources: [
        { client: "outlook-win", status: "ready", accounts: [], root: null, reason: null },
        { client: "eml-dir", status: "ready", accounts: [], root: "E:\\x", reason: null },
      ],
    }));
    expect(needsEmailSourceSetup({
      discovery: two, noMailAtAll: false, failed: false,
    })).toBe(true);
  });

  it("已经选过就不再打扰", () => {
    const chosen = parseEmailSourceDiscovery(JSON.stringify({
      platform: "Windows",
      ready_client: "eml-dir",
      selected_client: "eml-dir",
      sources: [
        { client: "outlook-win", status: "ready", accounts: [], root: null, reason: null },
        { client: "eml-dir", status: "ready", accounts: [], root: "E:\\x", reason: null },
      ],
    }));
    expect(needsEmailSourceSetup({
      discovery: chosen, noMailAtAll: false, failed: false,
    })).toBe(false);
  });
});
