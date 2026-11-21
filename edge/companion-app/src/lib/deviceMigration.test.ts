import { describe, expect, it } from "vitest";

import { formatBytes, normalizeCode, passphraseHint, percent, restorable, type MigrationEntry } from "./deviceMigration";

describe("deviceMigration helpers", () => {
  it("口令提示: 短的说至少 8 位, 纯数字 / 偏短提示偏弱, 够强不提示", () => {
    expect(passphraseHint("")).toBeNull();
    expect(passphraseHint("1234567")).toBe("至少 8 位");
    expect(passphraseHint("123456789012")).toMatch(/偏弱/);
    expect(passphraseHint("abc12345")).toMatch(/偏弱/);
    expect(passphraseHint("滨江花园三期-2026-塔吊")).toBeNull();
  });

  it("大小", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(3 * 1024 * 1024)).toBe("3.0 MB");
  });

  it("配对码: 去空格横线、转大写、4 位一组; 进度百分比", () => {
    expect(normalizeCode(" abcd efgh ")).toBe("ABCD-EFGH");
    expect(normalizeCode("abcd-efghXYZ")).toBe("ABCD-EFGH");
    expect(normalizeCode("ab")).toBe("AB");
    expect(percent(0, 0)).toBe(0);
    expect(percent(1, 3)).toBe(33);
    expect(percent(5, 3)).toBe(100);
  });

  it("误删保护: 外发记录 / 审计类不还原 (跟 Rust snapshots::restorable 一致)", () => {
    const e = (name: string): MigrationEntry => ({ root: "catfish", name, class: "required", label: name, files: 1, bytes: 1 });
    expect(restorable(e("wiki"))).toBe(true);
    expect(restorable(e("employee_journal.md"))).toBe(true);
    expect(restorable(e("outbound_log.db"))).toBe(false);
    expect(restorable(e("approval_audit.jsonl"))).toBe(false);
  });
});
