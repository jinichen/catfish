// @vitest-environment jsdom
/** 11/12 发邮件弹窗: 附件超过员工设的上限 → 说清楚、当场能改上限、或不带附件 (正文写明文件在哪)。 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../../lib/tauri_briefing", () => ({ emailAccountsFetch: vi.fn(async () => "[]") }));
vi.mock("./ComposeCore", () => ({
  default: (p: { initialBody: string; initialAttachments: { name: string }[] }) => (
    <div data-testid="core">{p.initialAttachments.map((a) => a.name).join(",")}|{p.initialBody}</div>
  ),
}));

import { getAttachLimitMb, setAttachLimitMb } from "../../../lib/emailAttachLimit";
import ComposeDialog from "./ComposeDialog";

beforeEach(() => {
  const data = new Map<string, string>();
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: { getItem: (k: string) => data.get(k) ?? null, setItem: (k: string, v: string) => void data.set(k, v) },
  });
});
afterEach(cleanup);

const big = [{ path: "/dl/汇报.pptx", name: "汇报.pptx", size: 72 * 1024 * 1024 }];
const mount = () => act(async () => {
  render(<ComposeDialog title="发邮件" subject="s" body="正文" attachments={big} dropNote="文件在群里。" resetKey="k"
                        onClose={() => undefined} onDone={() => undefined} />);
});

describe("ComposeDialog 附件上限", () => {
  it("默认 25 MB: 72 MB 提示收不了; 选不带附件 → 去掉附件、正文补一句", async () => {
    await mount();
    expect(screen.getByText(/附件合计 72.0 MB, 超过你设的 25 MB/)).toBeTruthy();
    fireEvent.click(screen.getByText("不带附件, 正文里写明文件在哪"));
    expect(screen.getByTestId("core").textContent).toBe("|正文\n\n文件在群里。");
    expect(screen.queryByText(/超过你设的/)).toBeNull();
  });

  it("当场把上限改到 100 MB → 提示消失, 附件照带; 设置记住了 (5–200 夹住)", async () => {
    await mount();
    const input = screen.getByLabelText("附件合计上限 (MB)");
    fireEvent.change(input, { target: { value: "100" } });
    await act(async () => { fireEvent.blur(input); });
    expect(screen.queryByText(/超过你设的/)).toBeNull();
    expect(screen.getByTestId("core").textContent).toBe("汇报.pptx|正文");
    expect(getAttachLimitMb()).toBe(100);
    expect(setAttachLimitMb(1000)).toBe(200);
    expect(setAttachLimitMb(1)).toBe(5);
  });
});
