import { describe, expect, it } from "vitest";
import type { ChatMessage } from "../types/chat";
import { artifactKind, artifactLabel, lastRoundArtifacts, messageArtifactPaths, sessionArtifactPaths } from "./artifactKinds";

const msg = (m: Partial<ChatMessage> & Pick<ChatMessage, "role">): ChatMessage => ({ id: Math.random().toString(36), content: "", ts: "2026-11-07T10:00:00.000Z", ...m });

describe("artifactKind", () => {
  it("跟 Rust 端 kind_of 同一张表", () => {
    expect(artifactKind("/a/b.HTML")).toBe("html");
    expect(artifactKind("/a/b.htm")).toBe("html");
    expect(artifactKind("/a/b.pdf")).toBe("pdf");
    expect(artifactKind("/a/b.svg")).toBe("image");
    expect(artifactKind("/a/b.md")).toBe("markdown");
    expect(artifactKind("/a/b.tsv")).toBe("table");
    expect(artifactKind("/a/b.json")).toBe("text");
    expect(artifactKind("/a/b.xlsm")).toBe("office");
    expect(artifactKind("/a/b.doc")).toBeNull();
    expect(artifactKind("/a/b.zip")).toBeNull();
    expect(artifactKind("/a/b")).toBeNull();
  });

  it("Office 文件的类型名说具体是哪种", () => {
    expect(artifactLabel("office", "xlsx")).toBe("Excel");
    expect(artifactLabel("office", "pptx")).toBe("PPT");
    expect(artifactLabel("office", "docx")).toBe("Word");
    expect(artifactLabel("html", "html")).toBe("网页");
  });
});

describe("messageArtifactPaths", () => {
  it("回答正文 + 已完成工具结果里的; 压缩包这类不算; 用户消息不算", () => {
    const m = msg({
      role: "assistant",
      content: "做好了: /Users/x/out/report.html , 原始数据 /Users/x/out/raw.zip",
      tool_calls: [
        { id: "1", name: "write_file", args: {}, status: "done", result: { path: "/Users/x/out/chart.png" } },
        { id: "2", name: "write_file", args: {}, status: "running", result: "/Users/x/out/half.md" },
      ],
    });
    expect(messageArtifactPaths(m)).toEqual(["/Users/x/out/report.html", "/Users/x/out/chart.png"]);
    expect(messageArtifactPaths(msg({ role: "user", content: "看看 /Users/x/a.pdf" }))).toEqual([]);
  });
});

describe("sessionArtifactPaths", () => {
  it("最新的在前、去重", () => {
    const list = sessionArtifactPaths([
      msg({ role: "assistant", content: "/Users/x/a.md 和 /Users/x/b.csv" }),
      msg({ role: "user", content: "再改改" }),
      msg({ role: "assistant", content: "/Users/x/a.md 改好了, 另有 /Users/x/c.pdf" }),
    ]);
    expect(list).toEqual(["/Users/x/c.pdf", "/Users/x/a.md", "/Users/x/b.csv"]);
  });
});

describe("lastRoundArtifacts", () => {
  it("只看最后一条用户消息之后的, 带上那条消息的时间", () => {
    const r = lastRoundArtifacts([
      msg({ role: "assistant", content: "/Users/x/old.html" }),
      msg({ role: "user", content: "做个图表", ts: "2026-11-07T10:05:00.000Z" }),
      msg({ role: "assistant", content: "", tool_calls: [{ id: "1", name: "x", args: {}, status: "done", result: "/Users/x/data.csv" }] }),
      msg({ role: "assistant", content: "图表在 /Users/x/chart.html" }),
    ]);
    expect(r).toEqual({ paths: ["/Users/x/chart.html", "/Users/x/data.csv"], since: Date.parse("2026-11-07T10:05:00.000Z") });
  });

  it("这一轮没提到文件 / 没有用户消息 → null", () => {
    expect(lastRoundArtifacts([msg({ role: "user", content: "hi" }), msg({ role: "assistant", content: "好的" })])).toBeNull();
    expect(lastRoundArtifacts([msg({ role: "assistant", content: "/Users/x/a.md" })])).toBeNull();
  });
});
