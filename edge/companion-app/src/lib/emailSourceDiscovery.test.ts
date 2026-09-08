import { describe, expect, it } from "vitest";

import { parseEmailSourceDiscovery, readyEmailSources } from "./emailSourceDiscovery";

describe("email source discovery", () => {
  it("parses independent Outlook and Foxmail statuses", () => {
    const result = parseEmailSourceDiscovery(JSON.stringify({
      platform: "Windows",
      ready_client: "foxmail-win",
      selected_client: "foxmail-win",
      sources: [
        { client: "outlook-win", status: "unavailable", accounts: [], root: null, reason: "未配置" },
        { client: "foxmail-win", status: "ready", accounts: [], root: "E:\\mail", reason: null },
      ],
    }));

    expect(readyEmailSources(result)).toHaveLength(1);
    expect(readyEmailSources(result)[0].client).toBe("foxmail-win");
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
