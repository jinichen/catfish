// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";

const invokeMock = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({
  invoke: (...args: unknown[]) => invokeMock(...args),
}));

import { MAX_UPLOAD_BYTES, stageFile, UPLOAD_CHUNK_BYTES } from "./uploadStage";
import { fileToAttachment } from "../tabs/Chat/components/attachmentHelpers";

/** 不真分配 200MB: 用 size 假装很大的 Blob */
function fakeBig(size: number, name: string, type: string): File {
  const f = new File(["x"], name, { type });
  Object.defineProperty(f, "size", { value: size });
  return f;
}

beforeEach(() => invokeMock.mockReset());

describe("stageFile (10/14 上传上限统一 200MB, 分块写临时文件)", () => {
  it("按 4MB 一块依次写, offset 连续, 拼起来就是原文件", async () => {
    invokeMock.mockResolvedValue(0);
    const data = new Uint8Array(UPLOAD_CHUNK_BYTES * 2 + 10).map((_, i) => i % 251);
    const id = await stageFile(new Blob([data]));
    const calls = invokeMock.mock.calls.filter((c) => c[0] === "upload_stage_chunk");
    expect(calls.map((c) => c[1].offset)).toEqual([0, UPLOAD_CHUNK_BYTES, UPLOAD_CHUNK_BYTES * 2]);
    expect(new Set(calls.map((c) => c[1].uploadId))).toEqual(new Set([id]));
    const joined = calls.map((c) => Uint8Array.from(atob(c[1].chunkB64), (ch) => ch.charCodeAt(0)));
    expect(joined.reduce((n, a) => n + a.length, 0)).toBe(data.length);
    expect(joined[2]).toEqual(data.slice(UPLOAD_CHUNK_BYTES * 2));
    for (const c of calls) expect(c[1].chunkB64.length).toBeLessThan(8 * 1024 * 1024); // 低于超大载荷告警
  });

  it("中途失败: 清掉半截临时文件, 错误照抛", async () => {
    invokeMock.mockImplementation((cmd: string, args: { offset: number }) =>
      cmd === "upload_stage_chunk" && args.offset > 0 ? Promise.reject("磁盘满了") : Promise.resolve(0));
    await expect(stageFile(new Blob([new Uint8Array(UPLOAD_CHUNK_BYTES + 1)]))).rejects.toBe("磁盘满了");
    expect(invokeMock.mock.calls.some((c) => c[0] === "upload_stage_discard")).toBe(true);
  });

  it("超过 200MB 一块都不传", async () => {
    await expect(stageFile(fakeBig(MAX_UPLOAD_BYTES + 1, "big.pdf", "application/pdf"))).rejects.toThrow("200MB");
    expect(invokeMock).not.toHaveBeenCalled();
  });
});

describe("聊天附件的上限", () => {
  it("150MB 的 PDF 和录音都收 (原来 20MB / 100MB)", async () => {
    invokeMock.mockImplementation((cmd: string) => {
      if (cmd === "parse_file_from_staged")
        return Promise.resolve({ filename: "大.pdf", ext: "pdf", kind: "pdf", preview_text: "x", preview_chars: 1, meta: {}, kept_path: "/k" });
      if (cmd === "transcribe_audio_from_staged")
        return Promise.resolve({ text: "会议内容", duration_sec: 3600, original_filename: "会.m4a" });
      return Promise.resolve(0);
    });
    const pdf = await fileToAttachment(new File([new Uint8Array(10)], "大.pdf", { type: "application/pdf" }));
    expect(pdf.keptPath).toBe("/k");
    const audio = await fileToAttachment(new File([new Uint8Array(10)], "会.m4a", { type: "audio/mp4" }));
    expect(audio.previewText).toBe("会议内容");
    await expect(fileToAttachment(fakeBig(150 * 1024 * 1024, "x.pdf", "application/pdf"))).resolves.toBeDefined();
  });

  it("201MB 拒收, 说清楚上限", async () => {
    await expect(fileToAttachment(fakeBig(201 * 1024 * 1024, "x.pdf", "application/pdf"))).rejects.toThrow("> 200MB");
  });

  it("缩不了又超过 20MB 的图片不往对话里塞", async () => {
    await expect(fileToAttachment(fakeBig(30 * 1024 * 1024, "图.svg", "image/svg+xml"))).rejects.toThrow("没法缩小");
  });
});
