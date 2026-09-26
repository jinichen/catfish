/** 邮件文件夹切换: 收件箱 / 草稿箱 / 已发送 (9/26)。
 *
 * 以前邮件页只列收件箱。macOS 上草稿和已发送在 Mail.app 里看, 没人觉得缺;
 * Windows 走 IMAP 以后不再有客户端, 小鲶存进草稿箱的回复在 Companion 里**看不见**,
 * 也就没法核对后发送 —— 小鲶自己的回复里还写着"去鲶鱼邮件页核对后点发送"。
 *
 * 取数不另写: 后端 list 本来就收 --folder, IMAP 按文件夹角色认 (草稿 / 草稿箱 /
 * Drafts 都算 Drafts), Apple Mail 同理。
 */
export type MailFolder = "Inbox" | "Drafts" | "Sent";

const FOLDERS: Array<[MailFolder, string]> = [
  ["Inbox", "收件箱"],
  ["Drafts", "草稿箱"],
  ["Sent", "已发送"],
];

export default function FolderTabs({
  value,
  onChange,
}: {
  value: MailFolder;
  onChange: (folder: MailFolder) => void;
}) {
  return (
    <div role="tablist" aria-label="邮件文件夹" style={{ display: "flex", gap: 4, marginBottom: "var(--space-2)" }}>
      {FOLDERS.map(([folder, label]) => {
        const active = folder === value;
        return (
          <button
            key={folder}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(folder)}
            style={{
              flex: 1,
              padding: "3px 0",
              fontSize: 12,
              fontFamily: "inherit",
              borderRadius: 4,
              cursor: "pointer",
              border: `1px solid ${active ? "var(--catfish-cyan)" : "var(--catfish-border)"}`,
              background: active ? "var(--catfish-cyan)" : "transparent",
              color: active ? "#fff" : "var(--catfish-text-muted)",
              fontWeight: active ? 600 : 400,
            }}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}
