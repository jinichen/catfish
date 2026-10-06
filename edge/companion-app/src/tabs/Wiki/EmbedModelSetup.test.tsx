// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const componentsList = vi.fn();
vi.mock("../../lib/tauri_components", () => ({
  componentsList: (...a: unknown[]) => componentsList(...a),
  componentsDownload: vi.fn(async () => {}),
  componentsVerify: vi.fn(async () => {}),
  componentsCancel: vi.fn(async () => {}),
  onComponentProgress: vi.fn(async () => () => {}),
}));
vi.mock("../../lib/tauri_embed_model", () => ({
  EMBED_MODEL_COMPONENT: "embed-model",
  embedModelInstall: vi.fn(async () => {}),
  onEmbedModelInstallProgress: vi.fn(async () => () => {}),
}));

import EmbedModelSetup from "./EmbedModelSetup";
import type { EmbedModelStatus } from "../../lib/tauri_embed_model";

const base: EmbedModelStatus = {
  local_supported: true,
  provider_ready: false,
  provider_remote: false,
  not_ready_reason: "本机向量模型没装齐 —— ONNX 模型 不在: ~/.catfish/models/bge-m3.onnx",
  installed: null,
  pack_ready: null,
  installing: false,
};

afterEach(() => {
  cleanup();
  componentsList.mockReset();
});

describe("EmbedModelSetup", () => {
  it("offers download when central has the pack", async () => {
    componentsList.mockResolvedValue({
      platform: "mac-arm64",
      components: [{ name: "embed-model", version: "1.0.0", platform: "any", file: "embed-model-1.0.0-any.tar.gz", size: 560_000_000, sha256: "x", status: { state: "absent" }, busy: false }],
    });
    await act(async () => { render(<EmbedModelSetup status={base} onReady={() => {}} />); });
    expect(screen.getByText("下载")).toBeTruthy();
    expect(screen.getByText(/BGE-M3 \(560 MB, 版本 1\.0\.0\)/)).toBeTruthy();
  });

  it("offers install when a verified pack is already on disk", async () => {
    componentsList.mockResolvedValue({ platform: "mac-arm64", components: [] });
    await act(async () => { render(<EmbedModelSetup status={{ ...base, pack_ready: "embed-model-1.0.0-any.tar.gz" }} onReady={() => {}} />); });
    expect(screen.getByText("安装")).toBeTruthy();
  });

  it("explains the gateway dependency on machines without a local runtime, no download button", async () => {
    componentsList.mockResolvedValue({ platform: "windows-x64", components: [] });
    await act(async () => { render(<EmbedModelSetup status={{ ...base, local_supported: false, not_ready_reason: "网关不可达" }} onReady={() => {}} />); });
    expect(screen.getByText(/靠中央网关/)).toBeTruthy();
    expect(screen.queryByText("下载")).toBeNull();
    expect(componentsList).not.toHaveBeenCalled();
  });

  it("shows the manual hint when central has not published the pack", async () => {
    componentsList.mockResolvedValue({ platform: "mac-arm64", components: [] });
    await act(async () => { render(<EmbedModelSetup status={base} onReady={() => {}} />); });
    expect(screen.getByText(/还没有发布向量模型包/)).toBeTruthy();
    expect(screen.getByText(/bge-m3\.onnx/)).toBeTruthy();
  });
});
