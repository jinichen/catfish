// @vitest-environment jsdom
/** 群输入框里「@小鲶 …」: 不发群, 回答只有自己看得到 (11/15)。 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const roomAssist = vi.fn();
vi.mock("../../lib/tauri_colleague_rooms", () => ({ roomAssist: (...a: unknown[]) => roomAssist(...a) }));

import { AskCards, XiaonianMemberRow, askQuestion, postText, useAskXiaonian } from "./RoomAskXiaonian";

afterEach(() => { cleanup(); roomAssist.mockReset(); });

function Harness({ onPost = () => {}, onEdit = () => {} }: { onPost?: (a: unknown) => void; onEdit?: (a: unknown) => void }) {
  const xn = useAskXiaonian("r1");
  return (
    <div>
      <button type="button" onClick={() => xn.ask("干净版和修订版的区别?", null)}>ask</button>
      <AskCards asks={xn.asks} onPost={onPost} onEdit={onEdit} onDismiss={xn.dismiss} />
    </div>
  );
}

describe("askQuestion", () => {
  it("以 @小鲶 开头才算问小鲶, 去掉前缀和标点", () => {
    expect(askQuestion("@小鲶 干净版和修订版的区别?")).toBe("干净版和修订版的区别?");
    expect(askQuestion("  @小鲶，总结一下")).toBe("总结一下");
    expect(askQuestion("@小鲶")).toBe("");
    expect(askQuestion("@张三 @小鲶 看下")).toBeNull();
    expect(askQuestion("普通消息")).toBeNull();
  });
  it("发到群里: 标明是小鲶答的, 不超长", () => {
    expect(postText({ id: 1, q: "q", answer: "修订版带痕迹" }, 4000)).toBe("🐟 小鲶: 修订版带痕迹");
    expect(postText({ id: 1, q: "q", answer: "x".repeat(5000) }, 4000)).toHaveLength(4000);
  });
});

describe("AskCards", () => {
  it("问了先显示在看, 回来后显示回答, 标「只有你看得到」; 发到群里 / 放进输入框 / 关掉", async () => {
    let done: (v: { text: string; kind: string }) => void = () => {};
    roomAssist.mockReturnValue(new Promise((r) => { done = r; }));
    const onPost = vi.fn();
    const onEdit = vi.fn();
    render(<Harness onPost={onPost} onEdit={onEdit} />);
    fireEvent.click(screen.getByText("ask"));
    expect(roomAssist).toHaveBeenCalledWith("r1", "ask", "干净版和修订版的区别?", null);
    expect(screen.getByText(/小鲶在查你电脑上的资料和群记录/)).toBeTruthy();
    expect(screen.getByText(/只有你看得到/)).toBeTruthy();
    expect(screen.queryByText("发到群里")).toBeNull();
    await act(async () => { done({ text: "修订版标出每处改动, 干净版是全部采纳后的成稿。", kind: "ask",
      sources: [{ kind: "知识库", title: "ITSS运维服务能力成熟度一级", where: "wiki/e.md" }, { kind: "本机文件", title: "提交清单.xlsx", where: "/x" }] } as never); });
    expect(screen.getByText("依据 (你电脑上的): 知识库「ITSS运维服务能力成熟度一级」 · 文件「提交清单.xlsx」")).toBeTruthy();
    expect(screen.getByText("修订版标出每处改动, 干净版是全部采纳后的成稿。")).toBeTruthy();
    fireEvent.click(screen.getByText("发到群里"));
    expect(onPost).toHaveBeenCalledWith(expect.objectContaining({ answer: "修订版标出每处改动, 干净版是全部采纳后的成稿。" }));
    fireEvent.click(screen.getByText("放进输入框"));
    expect(onEdit).toHaveBeenCalled();
    fireEvent.click(screen.getByText("关掉"));
    expect(screen.queryByTestId("xiaonian-ask")).toBeNull();
  });
  it("出错: 显示原因, 没有发到群里", async () => {
    roomAssist.mockRejectedValue(new Error("没登录 (拿不到访问令牌), 登录后再让小鲶帮忙"));
    render(<Harness />);
    await act(async () => { fireEvent.click(screen.getByText("ask")); });
    expect(screen.getByText(/没登录/)).toBeTruthy();
    expect(screen.queryByText("发到群里")).toBeNull();
  });
});

describe("XiaonianMemberRow", () => {
  it("成员栏里的小鲶: 点了开始问", () => {
    const onAsk = vi.fn();
    render(<XiaonianMemberRow onAsk={onAsk} />);
    fireEvent.click(screen.getByText(/你的助手 · 只有你看得到/));
    expect(onAsk).toHaveBeenCalled();
  });
});
