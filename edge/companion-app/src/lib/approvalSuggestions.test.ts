/**
 * 命令审批建议读取端的判据。
 *
 * 这条链路会**放宽安全策略** (加进免审批清单 = 以后静默执行), 所以测的重点是
 * 失败方向: 网络挂了/形状不对/服务端拒了, 都不能表现成"加成功了"。
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  applyApprovalPatterns,
  describeReason,
  fetchApprovalSuggestions,
} from "./approvalSuggestions";

vi.mock("./env", () => ({ config: { backendUrl: "http://127.0.0.1:8642" } }));

const fetchWithAuth = vi.fn();
vi.mock("./me", () => ({
  fetchWithAuth: (...args: unknown[]) => fetchWithAuth(...args),
}));

afterEach(() => fetchWithAuth.mockReset());

const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body });

describe("fetchApprovalSuggestions", () => {
  it("正常返回原样透出", async () => {
    fetchWithAuth.mockResolvedValue(
      ok({ ok: true, days: 90, proposals: [{ n: 1, pattern: "git push *", count: 3 }] }),
    );
    const out = await fetchApprovalSuggestions();
    expect(out.ok).toBe(true);
    expect(out.proposals[0].pattern).toBe("git push *");
  });

  it("网络挂了不抛，返 unreachable", async () => {
    fetchWithAuth.mockRejectedValue(new Error("ECONNREFUSED"));
    const out = await fetchApprovalSuggestions();
    expect(out.ok).toBe(false);
    expect(out.reason).toBe("unreachable");
    expect(out.proposals).toEqual([]);
  });

  it("形状不对不硬当成空列表", async () => {
    fetchWithAuth.mockResolvedValue(ok({ ok: true }));
    expect((await fetchApprovalSuggestions()).reason).toBe("bad_shape");
  });

  it("HTTP 错误带状态码", async () => {
    fetchWithAuth.mockResolvedValue({ ok: false, status: 503, json: async () => ({}) });
    expect((await fetchApprovalSuggestions()).reason).toBe("http_503");
  });
});

describe("applyApprovalPatterns", () => {
  it("送的是 pattern 原文，不是序号", async () => {
    fetchWithAuth.mockResolvedValue(ok({ ok: true, applied: ["git push *"], rejected: [] }));
    await applyApprovalPatterns(["git push *"]);
    const body = JSON.parse((fetchWithAuth.mock.calls[0][1] as RequestInit).body as string);
    expect(body).toEqual({ patterns: ["git push *"] });
  });

  it("服务端拒掉的条目原样带回来 —— 不能当没发生", async () => {
    fetchWithAuth.mockResolvedValue(
      ok({ ok: true, applied: ["a"], rejected: ["b"], allowlist_size: 5 }),
    );
    const out = await applyApprovalPatterns(["a", "b"]);
    expect(out.applied).toEqual(["a"]);
    expect(out.rejected).toEqual(["b"]);
  });

  it("网络挂了要把全部当成没加进去", async () => {
    fetchWithAuth.mockRejectedValue(new Error("boom"));
    const out = await applyApprovalPatterns(["a", "b"]);
    expect(out.ok).toBe(false);
    expect(out.applied).toEqual([]);
    expect(out.rejected).toEqual(["a", "b"]);
  });

  it("返回体缺字段时不假装成功", async () => {
    fetchWithAuth.mockResolvedValue(ok({ ok: true }));
    const out = await applyApprovalPatterns(["a"]);
    expect(out.applied).toEqual([]);
  });

  it("ok 字段是别的类型也不当成真", async () => {
    fetchWithAuth.mockResolvedValue(ok({ ok: "yes", applied: [], rejected: [] }));
    expect((await applyApprovalPatterns(["a"])).ok).toBe(true);
  });
});

describe("describeReason", () => {
  it.each([
    ["hermes_module_missing", "当前 hermes 版本没有这个功能"],
    ["session_db_missing", "还没有会话记录可分析"],
    ["auth_unavailable", "鉴权组件没就位，请重启 hermes"],
  ])("%s 翻成人话", (code, want) => {
    expect(describeReason(code)).toBe(want);
  });

  it("no_matching_proposal 要说清楚该怎么办", () => {
    expect(describeReason("no_matching_proposal")).toContain("重新查看");
  });

  it("认不出的原样带出来，不吞", () => {
    expect(describeReason("brand_new_code")).toContain("brand_new_code");
  });
});
