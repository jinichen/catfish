import { Btn } from "./roomLinkUi";

/** 交回的是文件 (11/5): 文件在发给发起人的邮件草稿里 —— 员工先去草稿箱核对、点发送, 再回来交回这段说明。 */
export default function MailBox({ mail, owner, sent, onOpen }: {
  mail: { draft_id: string; files: string[] }; owner: string; sent: boolean; onOpen: (draftId: string) => void;
}) {
  return (
    <div style={{ fontSize: 13, padding: "6px 10px", borderRadius: 4, border: "1px solid var(--catfish-border)",
                  display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
      <span>
        📎 文件在发给 {owner} 的邮件草稿里{mail.files.length ? `: ${mail.files.join("、")}` : ""}
        <span style={{ color: "var(--catfish-text-muted)" }}>
          {sent ? " —— 记得确认那封邮件已经发出去了。" : " —— 先去草稿箱核对附件、点发送, 再回来点「交回」。邮件不会自己发出去。"}
        </span>
      </span>
      {mail.draft_id && <Btn kind="ghost" onClick={() => onOpen(mail.draft_id)}>打开邮件草稿</Btn>}
    </div>
  );
}
