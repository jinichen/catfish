import { describe, expect, it } from "vitest";

import { formatBytes, labelsOf, passphraseHint, type MigrationEntry } from "./deviceMigration";

describe("deviceMigration helpers", () => {
  it("口令提示: 短的说至少 8 位, 纯数字 / 偏短提示偏弱, 够强不提示", () => {
    expect(passphraseHint("")).toBeNull();
    expect(passphraseHint("1234567")).toBe("至少 8 位");
    expect(passphraseHint("123456789012")).toMatch(/偏弱/);
    expect(passphraseHint("abc12345")).toMatch(/偏弱/);
    expect(passphraseHint("滨江花园三期-2026-塔吊")).toBeNull();
  });

  it("大小和同类合并", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(3 * 1024 * 1024)).toBe("3.0 MB");
    const e = (label: string): MigrationEntry => ({ root: "catfish", name: label, class: "required", label, files: 1, bytes: 1 });
    expect(labelsOf([e("记忆"), e("画像"), e("记忆")])).toBe("记忆、画像");
  });
});
