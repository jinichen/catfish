// @vitest-environment jsdom
/** 11/14: 资源市场里不列自己发布的专家 (原来显示成「你自己搭的专家里有同 id 的, 装不了」) */
import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const invoke = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({ invoke: (...a: unknown[]) => invoke(...a) }));
vi.mock("../../lib/me", () => ({ getCurrentUserEmail: () => Promise.resolve("Me@Example.com") }));

import { MarketSection } from "./ExpertPackUi";

const pack = (id: string, by: string, extra: Record<string, unknown> = {}) => ({ id, version: "1.0.0", name: id, description: "", status: "approved",
  scope: "department", departments: ["安全部"], submitted_by: by, submitted_at: "", reviewed_by: "boss@x", ...extra });

afterEach(cleanup);

describe("MarketSection", () => {
  it("自己发布的不列, 说一句去「我提交的」看; 同事的照常", async () => {
    invoke.mockResolvedValue({ experts: [pack("qual", "me@example.com", { local_conflict: true }), pack("safety", "li@x")] });
    const { container } = render(<MarketSection onInstall={() => undefined} />);
    await waitFor(() => expect(container.textContent).toContain("你自己发布的 1 个不在这里列"));
    expect(container.textContent).toContain("safety");
    expect(container.textContent).not.toContain("装不了");
  });

  it("只有自己发布的: 说市场里没有同事的", async () => {
    invoke.mockResolvedValue({ experts: [pack("qual", "me@example.com", { local_conflict: true })] });
    const { container } = render(<MarketSection onInstall={() => undefined} />);
    await waitFor(() => expect(container.textContent).toContain("市场里还没有同事发布、你能装的专家"));
    expect(container.textContent).toContain("你自己发布的 1 个不在这里列");
  });
});
