/**
 * IMAP 邮箱凭据 —— 存和删, **没有读**。
 *
 * 9/18: 加这条路是因为两条"读客户端本地数据"的路都被厂商堵死了 ——
 * Foxmail 7.2 把邮件文件加密了 (实测熵 7.96), 新版 Outlook 既不提供 COM
 * 也不在本地存邮件 (WebView 套壳)。IMAP 是公开协议, 跟客户端无关。
 *
 * ⚠ 这个文件里**故意没有** getImapPassword。
 *
 * Rust 侧 imap_credentials.rs 也没有对应的 #[tauri::command] —— 密码只进不出。
 * 前端负责把密码送进系统凭据库, 不负责取出来; 取值只有一个消费者, 是 Rust
 * 内部给 catfish-email 子进程注入环境变量的那一步。
 *
 * 一旦这里加一个读密码的封装, 任何一段前端 JS 就都能把员工的邮箱密码要出去。
 * 这条红线跟 teaching_credentials 里那条是同一条。
 */

import { invoke } from "@tauri-apps/api/core";

export interface ImapStatus {
  configured: boolean;
  host: string;
  user: string;
  port: number;
  /** 凭据库里到底还有没有那条密码。配置在但密码没了是真实状态 ——
   *  用户清过钥匙串, 或者换机器同步了配置没同步凭据。 */
  password_present: boolean;
  /** 发信服务器。空串 = 没显式配过, 发信时从 IMAP 主机猜 smtp.<域名>。 */
  smtp_host: string;
  /** 0 = 用默认 465 (隐式 TLS)。 */
  smtp_port: number;
}

/** 保存前 Rust 会**真连一次**服务器; 连不上就不保存, 直接抛错。
 *
 *  ⚠ 那次验证只验 IMAP (收信)。SMTP (发信) 是另一套主机和端口, **这里验
 *  不到** —— 收信正常不代表发得出去。第一次回复邮件时才会知道。
 */
export async function saveImapCredential(input: {
  host: string;
  user: string;
  password: string;
  port?: number;
  /** 留空就让后端从 IMAP 主机猜 */
  smtpHost?: string;
  /** 留空/0 就用默认 465 */
  smtpPort?: number;
}): Promise<ImapStatus> {
  const { smtpHost, smtpPort, ...rest } = input;
  return invoke<ImapStatus>("imap_credential_save", {
    ...rest,
    smtpHost: smtpHost?.trim() || null,
    smtpPort: smtpPort || null,
  });
}

/** 从 IMAP 主机猜发信服务器, 跟后端 smtp_send.guess_host 同一条规则。
 *
 *  只用来**给界面填个默认值让员工看见**, 不参与真正的发信 —— 真发信时
 *  后端自己会猜一次。两边各猜一次听着重复, 但界面这次是为了"让员工知道
 *  我们会用哪台服务器", 不填的话他到发失败为止都不知道。
 */
export function guessSmtpHost(imapHost: string): string {
  const host = (imapHost || "").trim().toLowerCase();
  return host.startsWith("imap.") ? `smtp.${host.slice(5)}` : host;
}

export async function clearImapCredential(): Promise<ImapStatus> {
  return invoke<ImapStatus>("imap_credential_clear");
}

export async function getImapStatus(): Promise<ImapStatus> {
  return invoke<ImapStatus>("imap_credential_status");
}

/** 从邮箱地址猜 IMAP 服务器, 只是个默认值, 用户随时能改。
 *
 *  不做 DNS/autodiscover: 那要联网、要处理超时、还未必准。猜错的代价是用户
 *  改一行字, 猜对了省他一次查文档 —— 这个取舍下不值得引入一条网络路径。 */
export function guessImapHost(email: string): string {
  const at = email.lastIndexOf("@");
  if (at < 0 || at === email.length - 1) return "";
  return `imap.${email.slice(at + 1).trim().toLowerCase()}`;
}
