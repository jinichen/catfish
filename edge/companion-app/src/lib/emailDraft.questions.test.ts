/** [QUESTIONS] 协议 —— 模型的反问不许进正文框 (8/21)。
 *
 * 病历: 员工点「重拟」, 模型不知道员工在这件事里的角色, 没按 prompt 用 [TODO]
 * 占位, 整段反问「你不是个人工会小组长吧? … 先别急着动笔, 你告诉我」。这段被
 * setComposeBody 原样塞进**发送框** —— 员工点发送就会寄给对方 (还抄送 9 人)。
 *
 * 修法: prompt 要求模型信息不足时以 [QUESTIONS] 开头; classifyDraftContent
 * 按显式标记分流, kind="questions" 时 ComposeCore 显示在提示区, 不进正文框。
 *
 * 判据故意只认显式标记, 不做「像不像在提问」的模糊启发 —— 那种判据必然比
 * 真事宽或窄 (中文邮件正文里带问号的草稿多得是)。
 */
import { describe, expect, it } from "vitest";

import { classifyDraftContent, QUESTIONS_MARK } from "./emailDraft";

describe("classifyDraftContent", () => {
  it("带标记 → questions, 标记剥掉, 不当草稿", () => {
    const r = classifyDraftContent(
      `${QUESTIONS_MARK}\n1. 你是工会小组长吗?\n2. 要确认还是要落实摸排?`,
    );
    expect(r.ok).toBe(true);
    expect(r.kind).toBe("questions");
    expect(r.body).toContain("工会小组长");
    expect(r.body).not.toContain(QUESTIONS_MARK);
  });

  it("不带标记 → draft (正文里有问号也不误判)", () => {
    // 真草稿完全可以含问句 —— 「像在提问」不是判据, 标记才是
    const r = classifyDraftContent("收到, 我周一给你。另外附件要最新版吗?");
    expect(r.kind).toBe("draft");
    expect(r.body).toBe("收到, 我周一给你。另外附件要最新版吗?");
  });

  it("标记后面是空的 → 报错而不是塞空问题清单", () => {
    const r = classifyDraftContent(QUESTIONS_MARK);
    expect(r.ok).toBe(false);
  });

  it("标记不在开头 → 当草稿 (协议只认行首)", () => {
    const r = classifyDraftContent(`草稿正文提到了 ${QUESTIONS_MARK} 这个词`);
    expect(r.kind).toBe("draft");
  });
});
