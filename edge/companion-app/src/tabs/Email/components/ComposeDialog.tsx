/**
 * 从别的页面发邮件的弹窗 (11/12): 会议纪要、协同群文档的最新版都用它。
 *
 * 里面就是「邮件」页那个写信面板 (ComposeCore): 收件人 / 主题 / 正文 / 附件预先填好, 收件人框能从知识库搜人;
 * 存草稿还是发送员工自己点 (发送两次确认)。默认账号自己取。
 *
 * 附件超过员工设的上限 (「邮件 → 设置」, 默认 25 MB): 上面先说清楚, 当场能改上限; 或者「不带附件」——
 * 正文末尾补一句文件在哪 (`dropNote`), 不偷偷压缩 (PPT / Word 本来就是压缩格式, 再压也小不了多少)。
 */
import { useEffect, useState, type ReactNode } from "react";

import { ModalShell } from "../../../components/ModalShell";
import { emailAccountsFetch } from "../../../lib/tauri_briefing";
import type { EmailAccountItem, PickedAttachment } from "../../../lib/tauri_cron";
import { useAgentStore } from "../../../store/agent";
import { AttachLimitInput, useAttachLimitMb } from "./AttachLimitSetting";
import ComposeCore from "./ComposeCore";

const fmtMb = (b: number) => `${(b / 1024 / 1024).toFixed(1)} MB`;

export default function ComposeDialog({ title, subject, body, initialTo = "", attachments, notes, dropNote, ready = true, resetKey, onClose, onDone }: {
  title: string; subject: string; body: string; initialTo?: string; attachments: PickedAttachment[];
  /** 弹窗上方的说明 (收件人是怎么填的等) */
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
  const muted = { fontSize: 12, color: "var(--catfish-text-muted)", lineHeight: 1.6 } as const;

  return (
    <ModalShell onClose={onClose} maxWidth={760} themed>
      <div style={{ display: "flex", flexDirection: "column", gap: 8, color: "var(--catfish-text)" }}>
        <strong style={{ fontSize: 15 }}>{title}</strong>
        {notes}
        {tooBig && (
          <div style={{ display: "flex", flexDirection: "column", gap: 6, padding: "8px 10px", borderRadius: 6, fontSize: 12,
                        border: "1px solid var(--status-warn)", color: "var(--catfish-text)" }}>
            <span style={{ color: "var(--status-warn)", fontWeight: 600 }}>
              附件合计 {fmtMb(total)}, 超过你设的 {limitMb} MB —— 收件方邮箱多半收不了, 现在也存不了草稿。
            </span>
            <span style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center" }}>
              <AttachLimitInput compact />
              {dropNote && (
                <button type="button" onClick={() => setDropped(true)}
                        style={{ border: "1px solid var(--catfish-border)", background: "transparent", color: "var(--catfish-text)",
                                 borderRadius: 4, padding: "3px 10px", cursor: "pointer", fontSize: 12 }}>
                  不带附件, 正文里写明文件在哪
                </button>
              )}
            </span>
            <span style={muted}>你们公司邮箱确实收得下, 就把上限调大; 收件方是外部邮箱时以对方的上限为准。</span>
          </div>
        )}
        {dropped && <span style={muted}>已去掉附件, 正文末尾写了文件在哪。</span>}
        <span style={muted}>收件人框里打名字, 会从知识库里给候选。可以先存草稿, 也可以直接发送 —— 发送要点两次确认。</span>
        {!ready ? <span style={{ fontSize: 13, color: "var(--catfish-text-muted)" }}>正在准备…</span> : (
          <div style={{ display: "flex", flexDirection: "column", minHeight: 420, maxHeight: "70vh", overflowY: "auto" }}>
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
          </div>
        )}
      </div>
    </ModalShell>
  );
}
