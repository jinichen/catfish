import { describe, expect, it } from "vitest";
import type { RoomMember } from "../../lib/tauri_colleague_rooms";
import { deliveryLines, fileState, pickFiles, sizeLabel, stageFiles } from "./RoomsDelivery";

const m = (email: string, name: string): RoomMember => ({ email, name, state: "joined", owner: false, me: false });
const members = [m("zhang@example.com", "张三"), m("li@example.com", "李四"), m("wang@example.com", "")];

describe("deliveryLines (11/07 没送到的挂在消息下面)", () => {
  it("按原因分组说人话, 没送到的排最前", () => {
    const rows = [
      { email: "zhang@example.com", state: "nokey" as const, error: "x", left: 1 },
      { email: "li@example.com", state: "failed" as const, error: "403", left: 1 },
      { email: "wang@example.com", state: "failed" as const, error: "403", left: 1 },
    ];
    expect(deliveryLines(rows, members)).toEqual([
      { state: "failed", text: "李四、wang没收到", emails: ["li@example.com", "wang@example.com"] },
      { state: "nokey", text: "张三还没打开新版鲶鱼, 打开后自动补发", emails: ["zhang@example.com"] },
    ]);
  });
  it("发文件: 正在发; 人多只列 3 个", () => {
    const rows = ["a", "b", "c", "d"].map((x) => ({ email: `${x}@example.com`, state: "sending" as const, error: "", left: 9 }));
    expect(deliveryLines(rows, [], true)[0].text).toBe("正在把文件发给 a、b、c 等 4 人");
    expect(deliveryLines([], members)).toEqual([]);
  });
});

describe("pickFiles / stageFiles (11/07 大文件: 单个 / 合计 200MB, 发送时 1MB 一段交给本机)", () => {
  const file = (name: string, size: number) => new File([new Uint8Array(size)], name);
  it("只记下来不读; 超个数 / 超大小 / 空文件当场说", async () => {
    const r = await pickFiles([file("a.txt", 3)], []);
    expect(r.files.map((f) => [f.name, f.size])).toEqual([["a.txt", 3]]);
    const many = await pickFiles(Array.from({ length: 6 }, (_, i) => file(`${i}.txt`, 1)), []);
    expect(many.files).toHaveLength(5);
    expect(many.error).toMatch(/最多带 5 个/);
    const huge = { name: "录像.mp4", size: 200 * 1024 * 1024 + 1 } as File;
    expect((await pickFiles([huge], [])).error).toMatch(/单个最多 200MB/);
    const two = [{ name: "a", size: 150 * 1024 * 1024 } as File, { name: "b", size: 60 * 1024 * 1024 } as File];
    const r2 = await pickFiles(two, []);
    expect(r2.files).toHaveLength(1);
    expect(r2.error).toMatch(/合计最多 200MB/);
    expect((await pickFiles([file("empty", 0)], [])).error).toMatch(/空文件/);
  });
  it("一段段传, 本机说 offset 对不上就从它有的地方续", async () => {
    const f = file("a.bin", 2 * 1024 * 1024 + 5);
    const calls: [string | null, number, number][] = [];
    let have = 0;
    const stage = async (id: string | null, offset: number, data: string) => {
      const len = atob(data).length;
      calls.push([id, offset, len]);
      if (calls.length === 2) return { ok: false, stage_id: "s_1", size: have };   // 第二段: 本机说「我这有这么多」
      have = offset + len;
      return { ok: true, stage_id: "s_1", size: have };
    };
    const progress: number[] = [];
    const out = await stageFiles([{ name: "a.bin", size: f.size, file: f }], (d) => progress.push(d), stage);
    expect(out).toEqual([{ name: "a.bin", stage_id: "s_1" }]);
    expect(calls.map((c) => c[1])).toEqual([0, 1048576, 1048576, 2097152]);
    expect(progress.at(-1)).toBe(f.size);
  });
  it("sizeLabel / fileState", () => {
    expect([sizeLabel(10), sizeLabel(2048), sizeLabel(3 * 1024 * 1024)]).toEqual(["10 B", "2 KB", "3.0 MB"]);
    const base = { id: "f_1", name: "x", size: 1, sha256: "", n: 40 };
    expect(fileState({ ...base, big: "uploading", got: 12 })).toBe("上传中 12/40");
    expect(fileState({ ...base, big: "waiting" })).toBe("等对方传完");
    expect(fileState({ ...base, big: "gone" })).toMatch(/中央已删/);
    expect(fileState({ ...base, big: "ready", have: true })).toBe("");
    expect(fileState({ ...base, have: false, got: 3 })).toBe("接收中 3/40");
  });
});
