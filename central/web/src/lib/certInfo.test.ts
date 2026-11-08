import { afterEach, describe, expect, it, vi } from "vitest";

import { certLevel, certSummary, daysLeft, fetchCertInfo } from "./certInfo";

const NOW = new Date("2026-11-08T00:00:00Z");

describe("证书到期 (门户「服务状态」)", () => {
  it("剩余天数 / 等级", () => {
    expect(daysLeft("2026-12-18T00:00:00Z", NOW)).toBe(40);
    expect(certLevel(40)).toBe("ok");
    expect(certLevel(29)).toBe("warn");
    expect(certLevel(-1)).toBe("expired");
    expect(certLevel(daysLeft("garbage", NOW))).toBe("unknown");
  });

  it("正式证书 30 天内: 提醒要手动换 (不会自动续)", () => {
    const s = certSummary("official", { not_after: "2026-11-20T00:00:00Z", names: ["catfish.example.com"] }, NOW);
    expect(s).toContain("还剩 12 天");
    expect(s).toContain("不会自动续");
  });

  it("正式证书过期: 说清楚怎么换", () => {
    const s = certSummary("official", { not_after: "2026-11-01T00:00:00Z", names: [] }, NOW);
    expect(s).toContain("已于 2026-11-01 过期");
    expect(s).toContain("TLS_CERT");
  });

  it("内部证书: 标自动续期", () => {
    expect(certSummary("internal", { not_after: "2027-12-10T00:00:00Z", names: [] }, NOW)).toContain("自动续期");
  });
});

describe("fetchCertInfo", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("HTTP 部署拿回来的是首页 HTML → null", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<html>", { headers: { "content-type": "text/html" } })));
    expect(await fetchCertInfo()).toBeNull();
  });

  it("有 json 就解析", async () => {
    const body = { internal: { not_after: "2027-12-10T00:00:00Z", names: ["10.0.0.1"] }, official: null };
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(body), { headers: { "content-type": "application/json" } })));
    expect(await fetchCertInfo()).toEqual(body);
  });
});
