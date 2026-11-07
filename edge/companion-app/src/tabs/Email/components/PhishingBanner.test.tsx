// @vitest-environment jsdom
/** 钓鱼红条 · 标注 (11/07): 「不是钓鱼」→ 换成一行「你标过 · 撤销」; 没报的信「标为钓鱼」。 */
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ emailPhishingMarks: vi.fn(), emailPhishingMark: vi.fn(), emailPhishingScanNow: vi.fn() }));
vi.mock("../../../lib/tauri_services", async (orig) => ({ ...(await orig<typeof import("../../../lib/tauri_services")>()), ...api }));

import type { PhishingScanResult } from "../../../lib/tauri";
import { usePhishingMarksStore } from "../../../store/phishingMarks";
import PhishingBanner, { MarkPhishingButton } from "./PhishingBanner";

afterEach(cleanup);
const NIC = "信息安全中心 <ff_nic@chinatelecom.cn>";
const msg = { id: "m1", sender: NIC, subject: "OpenSSH 漏洞预警" };
const list = [{ ...msg, account: "", folder: "", date: "", is_read: true, has_attachments: false, body_text: "" },
              { id: "m2", sender: NIC, subject: "APEC 保障", account: "", folder: "", date: "", is_read: true, has_attachments: false, body_text: "" }];
const flagged = { messageId: "m1", scannedAt: "", highestSeverity: "high", llmVerdict: "suspicious", llmReason: "需核实",
  flags: [{ ruleId: "PHISH-001-display-name-mismatch", severity: "high", category: "sender_spoofing", reason: "", matchedText: null }] } as PhishingScanResult;
const safeMarks = { senders: { "ff_nic@chinatelecom.cn": { verdict: "safe" as const, at: "x", subject: "", name: "" } } };

beforeEach(() => {
  usePhishingMarksStore.setState({ marks: { senders: {} }, rescanned: {} });
  api.emailPhishingMark.mockReset();
  api.emailPhishingScanNow.mockReset().mockResolvedValue({ m1: { ...flagged, highestSeverity: "none", userMark: "safe" } });
});

describe("PhishingBanner", () => {
  it("不是钓鱼: 存标注 (带上这个发件人的两封), 红条换成一行, 后台重扫", async () => {
    api.emailPhishingMark.mockResolvedValue({ marks: safeMarks, store: {} });
    render(<PhishingBanner result={flagged} msg={msg} list={list} />);
    expect(screen.getByText("⚠ catfish 标记此邮件有钓鱼嫌疑")).toBeTruthy();
    fireEvent.click(screen.getByText("不是钓鱼"));
    await screen.findByText(/你标过「不是钓鱼」/);
    expect(api.emailPhishingMark).toHaveBeenCalledWith(NIC, "safe", "OpenSSH 漏洞预警", ["m1", "m2"]);
    await waitFor(() => expect(api.emailPhishingScanNow).toHaveBeenCalled());
    expect(screen.queryByText("⚠ catfish 标记此邮件有钓鱼嫌疑")).toBeNull();
  });

  it("撤销: 标注去掉", async () => {
    act(() => usePhishingMarksStore.setState({ marks: safeMarks }));
    api.emailPhishingMark.mockResolvedValue({ marks: { senders: {} }, store: {} });
    render(<PhishingBanner result={flagged} msg={msg} list={list} />);
    fireEvent.click(screen.getByText("撤销"));
    await waitFor(() => expect(api.emailPhishingMark).toHaveBeenCalledWith(NIC, null, "OpenSSH 漏洞预警", ["m1", "m2"]));
  });

  it("没报的信: 「标为钓鱼」; 标了之后红条说你标过、按钮消失", async () => {
    api.emailPhishingMark.mockResolvedValue({ marks: { senders: { "ff_nic@chinatelecom.cn": { verdict: "phishing", at: "x", subject: "", name: "" } } }, store: {} });
    const safe = { ...flagged, highestSeverity: "none", flags: [] } as PhishingScanResult;
    render(<><PhishingBanner result={safe} msg={msg} list={list} /><MarkPhishingButton result={safe} msg={msg} list={list} /></>);
    expect(screen.queryByText(/钓鱼嫌疑/)).toBeNull();
    fireEvent.click(screen.getByText("⚠ 标为钓鱼"));
    await screen.findByText("⚠ 你标过这个发件人是钓鱼");
    expect(screen.queryByText("⚠ 标为钓鱼")).toBeNull();
    expect(screen.getByText("撤销标注")).toBeTruthy();
  });

  it("存不上: 说清楚, 红条留着", async () => {
    api.emailPhishingMark.mockRejectedValue(new Error("写不进 ~/.catfish"));
    render(<PhishingBanner result={flagged} msg={msg} list={list} />);
    fireEvent.click(screen.getByText("不是钓鱼"));
    await screen.findByText(/没改成: 写不进/);
    expect(screen.getByText("⚠ catfish 标记此邮件有钓鱼嫌疑")).toBeTruthy();
  });
});
