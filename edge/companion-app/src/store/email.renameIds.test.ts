import { describe, expect, it, vi } from "vitest";

vi.mock("../lib/tauri", () => ({ emailIdMigrationMap: vi.fn(), emailUrgencyMap: vi.fn() }));

import { renameIds } from "./email";

describe("renameIds (9/27 邮件 id 迁移)", () => {
  it("renames listed ids, keeps the value already stored under the new id", () => {
    const mapping = { "imap|INBOX|0|1": "imap|INBOX|1|1", "imap|INBOX|0|2": "imap|INBOX|1|2" };
    const out = renameIds(
      { "imap|INBOX|0|1": "急", "imap|INBOX|0|2": "低", "imap|INBOX|1|2": "中", "apple|x": "低" },
      mapping,
    );
    expect(out).toEqual({ "imap|INBOX|1|1": "急", "imap|INBOX|1|2": "中", "apple|x": "低" });
  });
});
