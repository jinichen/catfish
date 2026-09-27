/** 邮件页「邮箱设置」展开后的内容 (9/28)。
 *
 * 鸿波: "为什么 macOS 上用原生邮件客户端, 还会提示 IMAP?" —— 1.0.48 的设置面板
 * 只有一张 IMAP 卡片, 上面写着「新版 Outlook 和 Foxmail 读不到邮件时用这个」,
 * 在 Mac 上看像是在让人去配一个用不着的东西, 而真正在用的「邮件」App 账号一个
 * 字都没提。
 *
 * 所以先说**正在读哪些邮箱** (本机客户端里的账号是自动读到的, 不用在这里配),
 * 再说保留时间只管哪一类 (IMAP 直连; 客户端里的账号由客户端自己管), 最后才是
 * IMAP 卡片和档案进度。
 */
import type { EmailAccountItem } from "../../../lib/tauri";
import ArchivePanel from "./ArchivePanel";
import ImapSetup from "./ImapSetup";

const CLIENT_LABEL: Record<string, string> = {
  apple_mail: "「邮件」App",
  foxmail_mac: "Foxmail",
  outlook_win: "Outlook",
  eml_dir: "导出的邮件目录",
  imap: "邮箱直连 (IMAP)",
};

function accountText(a: EmailAccountItem): string {
  if (a.address && a.name && a.name !== a.address) return `${a.name} (${a.address})`;
  return a.address || a.name;
}

export default function MailSettingsPanel({
  accounts,
  onChanged,
}: {
  accounts: EmailAccountItem[];
  onChanged: () => void;
}) {
  const groups = new Map<string, EmailAccountItem[]>();
  for (const a of accounts) {
    const key = a.client || "";
    groups.set(key, [...(groups.get(key) ?? []), a]);
  }
  const hasClientAccounts = accounts.some((a) => a.client && a.client !== "imap");
  const imapConfigured = accounts.some((a) => a.client === "imap");
  // 鸿波 (9/28) 两次: "用原生客户端为什么还提示 IMAP?" / 收成一行之后 "为什么还要这个
  // 提示, 是不是很奇怪?"。说得对: 靠邮件客户端读信的人, 设置里不该出现 IMAP 的推荐。
  // IMAP 只在两种情况下出现: 已经配了 (要能看、能改保留时间), 或者根本没有客户端
  // 账号 (Windows 上 IMAP 就是唯一的路)。客户端读不到信时, 收件箱为空的引导卡片
  // (EmailSourceSetup) 里照样有 IMAP 入口, 不会无路可走。
  const imapVisible = imapConfigured || !hasClientAccounts;

  return (
    <>
      <div
        style={{
          margin: "8px 12px",
          padding: "10px 12px",
          border: "1px solid var(--catfish-border)",
          borderRadius: 8,
          background: "var(--catfish-bg)",
          fontSize: 12,
          lineHeight: 1.6,
        }}
      >
        <strong>正在读的邮箱</strong>
        {accounts.length === 0 ? (
          <div style={{ color: "var(--catfish-muted)" }}>还没有读到任何邮箱。</div>
        ) : (
          [...groups.entries()].map(([client, list]) => (
            <div key={client || "other"} style={{ marginTop: 4 }}>
              <span style={{ color: "var(--catfish-muted)" }}>{CLIENT_LABEL[client] ?? (client || "其他")}: </span>
              {list.map(accountText).join("、")}
            </div>
          ))
        )}
        {hasClientAccounts && (
          <div style={{ marginTop: 6, color: "var(--catfish-muted)", fontSize: 11 }}>
            从邮件客户端自动读到, 增删账号在客户端里做。
            {imapConfigured && " 「服务器上保留多久」只对下面邮箱直连 (IMAP) 的邮箱生效。"}
          </div>
        )}
      </div>
      {imapVisible && <ImapSetup onConfigured={onChanged} />}
      <ArchivePanel />
    </>
  );
}
