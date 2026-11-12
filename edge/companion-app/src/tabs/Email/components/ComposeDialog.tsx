/**
 * 从别的页面发邮件的弹窗 (11/12): 会议纪要、协同群文档的最新版都用它。
 *
 * 里面就是「邮件」页那个写信面板 (ComposeCore): 收件人 / 主题 / 正文 / 附件预先填好, 收件人框能从知识库搜人;
 * 存草稿还是发送员工自己点 (发送两次确认)。默认账号自己取。
 *
 * 附件超过员工设的上限 (「邮件 → 设置」, 默认 25 MB): 一行说清楚, 当场能改上限; 或者「不带附件」——
 * 正文末尾补一句文件在哪 (`dropNote`), 不偷偷压缩 (PPT / Word 本来就是压缩格式, 再压也小不了多少)。
 *
 * 11/12 第二版 (鸿波「这个 UI 是不是也要优化」): 第一版套了三层滚动 (弹窗 / 写信区 / 写信面板),「发送」「存草稿」
 * 被挤到下面要在里层滚才看得到; 上面四段说明把写信区压下去一半。现在: 弹窗固定高度、只有写信面板一层滚,
 * 正文框填满剩下的空间, 按钮始终在底下; 说明压成一两行; 点框外不关 (写了一半误点就丢了), 右上角 × 关。
 */
import { useEffect, useState, type ReactNode } from "react";

import { emailAccountsFetch } from "../../../lib/tauri_briefing";
import type { EmailAccountItem, PickedAttachment } from "../../../lib/tauri_cron";
import { useAgentStore } from "../../../store/agent";
import { AttachLimitInput, useAttachLimitMb } from "./AttachLimitSetting";
import ComposeCore from "./ComposeCore";

const fmtMb = (b: number) => `${(b / 1024 / 1024).toFixed(1)} MB`;
const small = { fontSize: 12, color: "var(--catfish-text-muted)", lineHeight: 1.6 } as const;

export default function ComposeDialog({ title, subject, body, initialTo = "", attachments, notes, dropNote, ready = true, resetKey, onClose, onDone }: {
  title: string; subject: string; body: string; initialTo?: string; attachments: PickedAttachment[];
  /** 标题下面的一两行说明 (收件人是怎么填的、附不附修订版等) */
  notes?: ReactNode;
  /** 选「不带附件」时补在正文末尾的一句 (文件在哪能拿到) */
  dropNote?: string;
  /** 附件 / 收件人还在准备 */
  ready?: boolean;
  resetKey: string; onClose: () => void; onDone: (text: string) => void;
}) {
  const agentName = useAgentStore((s) => s.name);
  const personality = useAgentStore((s) => s.personality);
  const limitMb = useAttachLimitMb();
  const [account, setAccount] = useState<string | undefined>(undefined);
  const [dropped, setDropped] = useState(false);

  useEffect(() => {
    emailAccountsFetch().then((j) => {
      try {
        const list = JSON.parse(j) as EmailAccountItem[];
        setAccount(list.find((a) => a.is_default)?.address || list[0]?.address);
      } catch { /* 读不到账号就让 ComposeCore 用缺省 */ }
    }).catch(() => undefined);
  }, []);

  const total = attachments.reduce((n, a) => n + a.size, 0);
  const tooBig = !dropped && total > limitMb * 1024 * 1024;
  const files = dropped ? [] : attachments;
  const text = dropped && dropNote ? `${body}\n\n${dropNote}` : body;

  return (
    <div role="dialog" aria-label={title}
         style={{ position: "fixed", inset: 0, zIndex: 1000, background: "rgba(0,0,0,0.45)",
                  display: "flex", alignItems: "center", justifyContent: "center", padding: 16 }}>
      <div style={{ width: "min(860px, 100%)", height: "min(820px, 92vh)", display: "flex", flexDirection: "column",
                    background: "var(--catfish-bg-elevated)", color: "var(--catfish-text)", border: "1px solid var(--catfish-border)",
                    borderRadius: 12, boxShadow: "0 16px 48px rgba(0,0,0,0.3)", overflow: "hidden" }}>
        <div style={{ padding: "14px 20px 10px", display: "flex", flexDirection: "column", gap: 6, borderBottom: "1px solid var(--catfish-border)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <strong style={{ fontSize: 15, flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={title}>
              {title}
            </strong>
            <button type="button" aria-label="关闭" onClick={onClose}
                    style={{ border: "none", background: "transparent", color: "var(--catfish-text-muted)", cursor: "pointer", fontSize: 18, lineHeight: 1 }}>
              ×
            </button>
          </div>
          {notes}
          {tooBig && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 12px", alignItems: "center", fontSize: 12, padding: "6px 10px",
                          borderRadius: 6, border: "1px solid var(--status-warn)" }}>
              <span style={{ color: "var(--status-warn)", fontWeight: 600 }}>
                ⚠ 附件 {fmtMb(total)}, 超过上限 {limitMb} MB, 收件方邮箱多半收不了
              </span>
              <AttachLimitInput compact />
              {dropNote && (
                <button type="button" onClick={() => setDropped(true)}
                        style={{ border: "1px solid var(--catfish-border)", background: "transparent", color: "var(--catfish-text)",
                                 borderRadius: 4, padding: "2px 10px", cursor: "pointer", fontSize: 12 }}>
                  不带附件, 正文写明文件在哪
                </button>
              )}
            </div>
          )}
          {dropped && <span style={small}>已去掉附件, 正文末尾写了文件在哪。</span>}
        </div>
        <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
          {!ready ? <span style={{ ...small, padding: 20 }}>正在准备…</span> : (
            <ComposeCore
              key={`${resetKey}:${dropped ? "drop" : "files"}`}
              isOpen
              onClose={onClose}
              initialTo={initialTo}
              initialSubject={subject}
              initialBody={text}
              initialAttachments={files}
              inReplyToMsgId={null}
              account={account}
              originalMessage={null}
              agentName={agentName}
              agentPersonality={personality}
              resetKey={`${resetKey}:${dropped ? "drop" : "files"}`}
              onSaveDraftSuccess={() => onDone("已存进邮件草稿箱, 去「邮件」或邮件 App 里检查后发送")}
              onSendSuccess={() => onDone("邮件已发送")}
            />
          )}
        </div>
      </div>
    </div>
  );
}
