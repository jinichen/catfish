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
  /** 归档校验通过之后多久把服务器上那份删掉。 */
  retention: RetentionPolicy;
}

/** 邮件归档之后, 服务器上那份留多久。
 *
 * ⚠ **默认永远是 never, 而且认不出来的值也退回 never。** 删服务器上的邮件
 * 不可逆, 不能因为员工没注意到设置项、或者哪里传了个拼错的字符串就开始删。
 * 这个兜底在三层都有 (这里 / Rust / Python) —— 三层都兜是因为这一步没有
 * 任何后悔的余地。 */
export type RetentionPolicy = "immediate" | "1w" | "2w" | "never";

export const RETENTION_LABELS: Record<RetentionPolicy, string> = {
  never: "不删除（服务器上一直保留）",
  "2w": "归档两周后删除",
  "1w": "归档一周后删除",
  immediate: "归档校验通过后立即删除",
};

/** 界面上的顺序: 从最安全到最激进。
 *
 * 不是按时间长短排 —— 是按"删错了有多惨"排。下拉框第一项是员工最可能
 * 不小心选中的那个, 所以第一项必须是 never。 */
export const RETENTION_ORDER: RetentionPolicy[] = ["never", "2w", "1w", "immediate"];

export function normalizeRetention(value: unknown): RetentionPolicy {
  return value === "immediate" || value === "1w" || value === "2w" ? value : "never";
}

/** 档案进度。纯本地读 SQLite, 不连服务器 —— 界面可以放心频繁问。 */
export interface ArchiveStatus {
  account: string;
  root: string;
  retention: RetentionPolicy;
  /** 索引里一共多少封 */
  total: number;
  /** 原文已落地的 */
  archived: number;
  /** **独立回读核对过**的。服务器端清理只认这个数。 */
  verified: number;
  /** 服务器上已经没有、只剩本地档案的 */
  only_local: number;
  /** 档案占了多少字节 */
  bytes: number;
}

export async function getArchiveStatus(): Promise<ArchiveStatus> {
  const raw = await invoke<string>("email_archive_status");
  const parsed = JSON.parse(raw) as ArchiveStatus;
  return { ...parsed, retention: normalizeRetention(parsed.retention) };
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
  /** 不传 = never。见 RetentionPolicy 上那段。 */
  retention?: RetentionPolicy;
}): Promise<ImapStatus> {
  const { smtpHost, smtpPort, retention, ...rest } = input;
  return invoke<ImapStatus>("imap_credential_save", {
    ...rest,
    smtpHost: smtpHost?.trim() || null,
    smtpPort: smtpPort || null,
    // 不传 null 而传 "never": null 走 Rust 的 default_retention() 也是 never,
    // 两条路结果一样。显式传是为了让"界面上选了什么"跟"存进去什么"一一对应,
    // 排查时不用再去猜哪一层填的默认值。
    retention: normalizeRetention(retention),
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
