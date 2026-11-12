// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../../lib/tauri_wiki_people", async (orig) => ({
  ...(await orig<typeof import("../../../lib/tauri_wiki_people")>()),
  wikiPeopleCached: vi.fn(async () => [
    { name: "迟小璐", aliases: [], email: "cxl@ex.cn", sources: ["人员与邮箱对照"] },
    { name: "陈秀平", aliases: [], email: "cxp@ex.cn", sources: ["人员与邮箱对照"] },
  ]),
}));

import RecipientInput from "./RecipientInput";

afterEach(cleanup);

function Host() {
  const [v, setV] = useState("");
  return <><RecipientInput ariaLabel="收件人" value={v} onChange={setV} /><output data-testid="v">{v}</output></>;
}

describe("RecipientInput", () => {
  it("打名字给知识库候选 (带来源), 点一下换成邮箱; 回车选第一个", async () => {
    await act(async () => { render(<Host />); });
    const input = screen.getByLabelText("收件人");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "迟" } });
    expect(screen.getByText("来自「人员与邮箱对照」")).toBeTruthy();
    fireEvent.mouseDown(screen.getByText("迟小璐"));
    expect(screen.getByTestId("v").textContent).toBe("cxl@ex.cn, ");
    fireEvent.change(input, { target: { value: "cxl@ex.cn, 陈" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(screen.getByTestId("v").textContent).toBe("cxl@ex.cn, cxp@ex.cn, ");
  });
});
