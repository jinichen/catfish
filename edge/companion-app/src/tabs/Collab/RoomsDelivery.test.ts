import { describe, expect, it } from "vitest";
import type { RoomMember } from "../../lib/tauri_colleague_rooms";
import { deliveryLines, pickFiles, sizeLabel } from "./RoomsDelivery";

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

describe("pickFiles", () => {
  const file = (name: string, size: number) => new File([new Uint8Array(size)], name);
  it("读成 base64, 超个数 / 超大小当场说", async () => {
    const r = await pickFiles([file("a.txt", 3)], []);
    expect(r.files).toEqual([{ name: "a.txt", size: 3, data: "AAAA" }]);
    const many = await pickFiles(Array.from({ length: 6 }, (_, i) => file(`${i}.txt`, 1)), []);
    expect(many.files).toHaveLength(5);
    expect(many.error).toMatch(/最多带 5 个/);
    const big = await pickFiles([file("big.zip", 5 * 1024 * 1024 + 1)], []);
    expect(big.files).toHaveLength(0);
    expect(big.error).toMatch(/合计最多 5MB/);
    expect((await pickFiles([file("empty", 0)], [])).error).toMatch(/空文件/);
  });
  it("sizeLabel", () => {
    expect([sizeLabel(10), sizeLabel(2048), sizeLabel(3 * 1024 * 1024)]).toEqual(["10 B", "2 KB", "3.0 MB"]);
  });
});
