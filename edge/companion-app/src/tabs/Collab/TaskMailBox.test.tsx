// @vitest-environment jsdom
/** 交回的是文件: 提示在哪封邮件草稿里, 点了打开草稿 (11/5)。 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import MailBox from "./TaskMailBox";

afterEach(cleanup);

describe("TaskMailBox", () => {
  it("还没交回: 先去草稿箱发; 点按钮打开那封草稿", () => {
    const onOpen = vi.fn();
    render(<MailBox mail={{ draft_id: "d-9", files: ["拜访.xlsx", "周报.docx"] }} owner="zhou@example.com" sent={false} onOpen={onOpen} />);
    expect(screen.getByText(/文件在发给 zhou@example.com 的邮件草稿里: 拜访.xlsx、周报.docx/)).toBeTruthy();
    expect(screen.getByText(/邮件不会自己发出去/)).toBeTruthy();
    fireEvent.click(screen.getByText("打开邮件草稿"));
    expect(onOpen).toHaveBeenCalledWith("d-9");
  });
  it("交回后: 提醒确认邮件发出去了; 没有草稿 id 不给按钮", () => {
    render(<MailBox mail={{ draft_id: "", files: ["a.pdf"] }} owner="zhou@example.com" sent onOpen={() => {}} />);
    expect(screen.getByText(/记得确认那封邮件已经发出去了/)).toBeTruthy();
    expect(screen.queryByText("打开邮件草稿")).toBeNull();
  });
});
