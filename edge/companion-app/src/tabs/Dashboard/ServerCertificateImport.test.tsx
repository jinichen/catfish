// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const invokeMock = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({ invoke: (...a: unknown[]) => invokeMock(...a) }));

import { config } from "../../lib/env";
import { ServerCertificateImport } from "./ServerCertificateImport";

afterEach(() => { cleanup(); invokeMock.mockReset(); });

describe("证书不受信任时用接入码导入 (10/14)", () => {
  it("把当前门户地址和接入码交给 Rust 核对, 通过后告诉员工", async () => {
    config.webUrl = "https://127.0.0.1";
    invokeMock.mockResolvedValue("C:\\Users\\x\\.catfish\\server-ca.pem");
    render(<ServerCertificateImport />);
    fireEvent.change(screen.getByPlaceholderText(/3F9A-1C2B/), { target: { value: "88dc-a0b2-b193-3921" } });
    await act(async () => { fireEvent.click(screen.getByText("导入")); });
    expect(invokeMock).toHaveBeenCalledWith("pair_server_certificate", { webUrl: "https://127.0.0.1", code: "88dc-a0b2-b193-3921" });
    expect(screen.getByRole("status").textContent).toContain("核对通过");
  });

  it("对不上: 说没有导入和原因", async () => {
    config.webUrl = "https://127.0.0.1";
    invokeMock.mockRejectedValue("接入码对不上, 没有导入。");
    render(<ServerCertificateImport />);
    fireEvent.change(screen.getByPlaceholderText(/3F9A-1C2B/), { target: { value: "0000-0000-0000-0000" } });
    await act(async () => { fireEvent.click(screen.getByText("导入")); });
    expect(screen.getByRole("status").textContent).toContain("没有导入");
  });
});
