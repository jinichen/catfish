/**
 * BL-COMPANION-FILE-PATH-CLICKABLE 单测.
 *
 * 跑法: cd edge/companion-app && pnpm test linkify_paths
 *
 * 红线 cover:
 *   - 普通路径 + 含空格 + 含中文 (catfish skill 输出常含)
 *   - 不能误吃 URL / 相对路径 / 没扩展名的 /etc/passwd
 *   - 跨行截断 (一段 markdown 里下一行别黏进路径)
 */

import { describe, expect, it } from "vitest";
import { __test__ } from "./linkify_paths";

const { INLINE_PATH_RE, splitTextWithLinks } = __test__;

function matchAll(text: string): string[] {
  INLINE_PATH_RE.lastIndex = 0;
  const out: string[] = [];
  let m: RegExpExecArray | null;
  while ((m = INLINE_PATH_RE.exec(text)) !== null) {
    out.push(m[1]);
  }
  return out;
}

describe("INLINE_PATH_RE — 应匹配", () => {
  it("普通绝对路径 .xlsx", () => {
    expect(matchAll("已生成 /Users/foo/bar.xlsx 文件")).toEqual([
      "/Users/foo/bar.xlsx",
    ]);
  });

  it("路径里有空格 (常见: '周报 - 鸿波.xlsx')", () => {
    const t = "周报已生成: /Users/chenhongbo/.catfish/output/周报 - 鸿波.xlsx 请查收";
    expect(matchAll(t)).toEqual([
      "/Users/chenhongbo/.catfish/output/周报 - 鸿波.xlsx",
    ]);
  });

  it("路径里有中文字符", () => {
    expect(matchAll("文件路径: /Users/chenhongbo/周报-鸿波.xlsx")).toEqual([
      "/Users/chenhongbo/周报-鸿波.xlsx",
    ]);
  });

  it("真实 catfish 路径 (日期 + 时间戳 + 中文标题)", () => {
    const t =
      "周报已生成:\n/Users/chenhongbo/.catfish/output/2026-05-19/145051_周报 - 鸿波/周报 - 鸿波 -20260519.xlsx\n请查收";
    expect(matchAll(t)).toEqual([
      "/Users/chenhongbo/.catfish/output/2026-05-19/145051_周报 - 鸿波/周报 - 鸿波 -20260519.xlsx",
    ]);
  });

  it("一段里多个路径", () => {
    const t = "生成了 /Users/a/x.docx 和 /Users/a/y.pdf";
    expect(matchAll(t)).toEqual(["/Users/a/x.docx", "/Users/a/y.pdf"]);
  });

  it("~/Desktop home 路径", () => {
    expect(matchAll("文件: ~/Users/me/note.md")).toEqual([
      "~/Users/me/note.md",
    ]);
  });

  it("/tmp 临时路径", () => {
    expect(matchAll("dumped /tmp/debug.log")).toEqual(["/tmp/debug.log"]);
  });

  it("/var 路径", () => {
    expect(matchAll("/var/folders/abc/report.xlsx 已写入")).toEqual([
      "/var/folders/abc/report.xlsx",
    ]);
  });
});

describe("INLINE_PATH_RE — 不应匹配", () => {
  it("相对路径", () => {
    expect(matchAll("bar.xlsx 文件")).toEqual([]);
    expect(matchAll("./local/foo.docx")).toEqual([]);
  });

  it("URL", () => {
    expect(matchAll("https://example.com/foo.pdf")).toEqual([]);
    expect(matchAll("file:///Users/foo.docx")).toEqual([]);
  });

  it("没扩展名 /etc/passwd 类", () => {
    expect(matchAll("/etc/passwd 这种没扩展名")).toEqual([]);
    expect(matchAll("/Users/foo/bar")).toEqual([]);
  });

  it("敏感 /etc 根虽然带 ext 也不识别 (不在白名单根)", () => {
    expect(matchAll("/etc/foo.conf")).toEqual([]);
  });

  it("import 路径 (go / py module)", () => {
    expect(matchAll("import pkg/path/foo.go")).toEqual([]);
  });

  it(".docx2 / .pdfa 伪扩展", () => {
    expect(matchAll("/Users/a/x.docx2 bad")).toEqual([]);
  });

  it("跨行不能黏进路径", () => {
    const t = "看 /Users/a/x.docx\n后面是别的内容.txt";
    // 第一行的路径应识别; 第二行的相对路径不识别 (没 /Users 前缀)
    expect(matchAll(t)).toEqual(["/Users/a/x.docx"]);
  });
});

describe("splitTextWithLinks — 序列化", () => {
  it("无路径 → 原样返回", () => {
    const out = splitTextWithLinks("普通一段文字, 没文件", "k");
    expect(out).toEqual(["普通一段文字, 没文件"]);
  });

  it("单路径 → [前文, link, 后文]", () => {
    const out = splitTextWithLinks(
      "周报: /Users/a/x.xlsx 已生成",
      "k",
    );
    expect(out.length).toBe(3);
    expect(out[0]).toBe("周报: ");
    expect(out[2]).toBe(" 已生成");
    // 中间是 React 元素
    expect(typeof out[1]).toBe("object");
  });

  it("路径在末尾 → 不加尾部文字段", () => {
    const out = splitTextWithLinks("生成了 /Users/a/x.xlsx", "k");
    expect(out.length).toBe(2);
    expect(out[0]).toBe("生成了 ");
  });

  it("两个路径", () => {
    const out = splitTextWithLinks("a /Users/a/x.docx b /Users/a/y.pdf c", "k");
    // ["a ", <link>, " b ", <link>, " c"]
    expect(out.length).toBe(5);
    expect(out[0]).toBe("a ");
    expect(out[2]).toBe(" b ");
    expect(out[4]).toBe(" c");
  });
});
