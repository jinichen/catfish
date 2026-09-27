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
import { useState } from "react";

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
  // 鸿波 (9/28): "是不是原生和 IMAP 会很混乱?" —— 已经靠邮件客户端读着信、又没配
  // IMAP 的人, 用不着一上来就看一张 IMAP 配置卡片。收成一行, 要用再展开。
  const [showImap, setShowImap] = useState(false);
  const imapVisible = imapConfigured || !hasClientAccounts || showImap;

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
            邮件客户端里的账号是自动读到的, 不用在这里再配; 增删账号在客户端里做。
            「服务器上保留多久」只对下面「邮箱直连 (IMAP)」的邮箱生效 ——
            客户端里的账号, 服务器上的邮件由客户端和邮箱服务商自己管。
          </div>
        )}
      </div>
      {imapVisible ? (
        <ImapSetup onConfigured={onChanged} />
      ) : (
        <button
          type="button"
          onClick={() => setShowImap(true)}
          style={{
            margin: "0 12px 8px", background: "none", border: "none", padding: 0, font: "inherit",
            fontSize: 12, color: "var(--catfish-text-muted)", cursor: "pointer", textDecoration: "underline",
          }}
        >
          要在本机留存邮件原文、设置服务器上保留多久? 开启邮箱直连 (IMAP)
        </button>
      )}
      <ArchivePanel />
    </>
  );
}
