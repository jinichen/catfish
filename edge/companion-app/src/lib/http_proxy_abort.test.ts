import { describe, expect, it, vi } from "vitest";

const invokeMock = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({
  invoke: (...args: unknown[]) => invokeMock(...args),
}));

import { httpProxy } from "./http_proxy";

describe("abortable HTTP proxy", () => {
  it("把 AbortSignal 传成 Rust request id 并中止同一个请求", async () => {
    let rejectRequest: ((reason?: unknown) => void) | undefined;
    invokeMock.mockImplementation((command: string) => {
      if (command === "http_proxy_abortable") {
        return new Promise((_resolve, reject) => {
          rejectRequest = reject;
        });
      }
      if (command === "http_proxy_abort") {
        rejectRequest?.("请求已取消");
        return Promise.resolve(true);
      }
      throw new Error(`unexpected command: ${command}`);
    });

    const controller = new AbortController();
    const request = httpProxy("http://127.0.0.1:8642/v1/chat/completions", {
      method: "POST",
      body: JSON.stringify({ stream: false }),
      signal: controller.signal,
    });
    await vi.waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith(
        "http_proxy_abortable",
        expect.objectContaining({ requestId: expect.any(String) }),
      );
    });

    controller.abort();
    await expect(request).rejects.toMatchObject({ name: "AbortError" });
    const abortable = invokeMock.mock.calls.find(([name]) => name === "http_proxy_abortable");
    expect(invokeMock).toHaveBeenCalledWith("http_proxy_abort", {
      requestId: abortable?.[1].requestId,
    });
  });
});
