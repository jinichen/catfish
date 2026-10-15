import { describe, expect, it } from "vitest";
import {
  hideDeleted, pruneTombstones, tombstoneKeys, TOMBSTONE_MAX_AGE_MS, TOMBSTONE_SETTLE_MS,
} from "./emailDeleted";

const a = { id: "imap|INBOX|1|10", message_id: "<A@x.com>" };
const b = { id: "imap|INBOX|1|11", message_id: "<b@x.com>" };

describe("删掉的邮件墓碑 (10/15 Windows 删了又冒出来)", () => {
  it("按 id 和 Message-ID 都认 —— id 换了 (迁移 / 换来源) 照样藏住", () => {
    const tombs = Object.fromEntries(tombstoneKeys(a).map((k) => [k, Date.now()]));
    expect(hideDeleted([a, b], tombs)).toEqual([b]);
    expect(hideDeleted([{ id: "eml-dir|x", message_id: "<a@X.com>" }, b], tombs)).toEqual([b]);
  });

  it("删除前发出的那轮旧列表回来: 还带着它 → 藏住, 墓碑不撤", () => {
    const now = Date.now();
    const tombs = { [a.id]: now - 5_000 };
    expect(hideDeleted([a, b], tombs)).toEqual([b]);
    expect(pruneTombstones(tombs, [a, b], true, now)).toEqual(tombs);
  });

  it("完整收件箱里已经没有它, 且过了 10 分钟 → 撤墓碑", () => {
    const now = Date.now();
    const tombs = { [a.id]: now - TOMBSTONE_SETTLE_MS - 1 };
    expect(pruneTombstones(tombs, [b], true, now)).toEqual({});
  });

  it("刚删 (10 分钟内) 不在列表里也先留着 —— 防在途的旧列表晚到", () => {
    const now = Date.now();
    const tombs = { [a.id]: now - 60_000 };
    expect(pruneTombstones(tombs, [b], true, now)).toEqual(tombs);
  });

  it("只看未读 / 别的文件夹的列表不拿来撤 (不在里面不代表删了); 30 天过期", () => {
    const now = Date.now();
    const old = { [a.id]: now - TOMBSTONE_SETTLE_MS - 1 };
    expect(pruneTombstones(old, [b], false, now)).toEqual(old);
    expect(pruneTombstones({ [a.id]: now - TOMBSTONE_MAX_AGE_MS - 1 }, [a], false, now)).toEqual({});
  });
});

describe("store: markDeleted / acceptList", () => {
  it("删了之后, 在途旧列表回来也不带它; 缓存里同步拿掉", async () => {
    const { useEmailStore } = await import("../store/email");
    useEmailStore.setState({ deleted: {}, listCache: { items: [a, b], sentItems: [], accounts: [], savedAt: 0 } });
    useEmailStore.getState().markDeleted(a);
    expect(useEmailStore.getState().listCache?.items).toEqual([b]);
    expect(useEmailStore.getState().acceptList([a, b], true)).toEqual([b]);
    expect(Object.keys(useEmailStore.getState().deleted)).toContain(a.id);   // 刚删, 墓碑还在
  });
});
