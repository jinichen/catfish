// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";

import { DEFAULT_TEMPLATE_ID, type MinutesTemplate } from "../../lib/tauri_meeting";
import { EXAMPLE_TEMPLATE, lastTemplateId, rememberTemplateId } from "./minutesTemplates";

const t = (id: string): MinutesTemplate => ({ id, name: id, body: "x", updated_at: "" });

describe("纪要模版 (10/3)", () => {
  beforeEach(() => {
    // 自带一份内存存储: 这套环境里 Node 的实验性 localStorage 盖住了 jsdom 的, 没有 clear()
    const data = new Map<string, string>();
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      value: { getItem: (k: string) => data.get(k) ?? null, setItem: (k: string, v: string) => void data.set(k, v) },
    });
  });

  it("记住上次选的; 被删了就回缺省", () => {
    expect(lastTemplateId([t("tpl_a")])).toBe(DEFAULT_TEMPLATE_ID);
    rememberTemplateId("tpl_a");
    expect(lastTemplateId([t("tpl_a"), t("tpl_b")])).toBe("tpl_a");
    expect(lastTemplateId([t("tpl_b")])).toBe(DEFAULT_TEMPLATE_ID);
  });

  it("示例模版用的占位符都是后端认的那几个", () => {
    const used = [...EXAMPLE_TEMPLATE.matchAll(/\{\{\s*([^{}]+?)\s*\}\}/g)].map((m) => m[1]);
    for (const p of used) expect(["会议标题", "日期", "参会人", "参会人数", "时长"]).toContain(p);
  });
});
