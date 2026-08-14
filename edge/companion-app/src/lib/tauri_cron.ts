/**
 * 定时任务监控 · P3.5.105 (6/25 鸿波 catch「定时任务跑没跑结果如何都看不到」)
 *
 * 2026-08-15 从 lib/tauri.ts 切出来 (1309 行超限)。纯搬迁, 逻辑一行未改。
 * 边界照抄原文件里作者早就画好的 `// ── xxx ──` 分节, 不是我另起的划分。
 *
 * lib/tauri.ts 现在是 barrel, 只做 re-export —— 62 个调用方一行没动。
 */

import { invoke as rawInvoke } from "@tauri-apps/api/core";

// ── P3.5.105 (6/25 鸿波 catch '定时任务跑没跑结果如何都看不到'): cron 监控 ──

/** ~/.hermes/cron/jobs.json 真 schema (25 字段, nullable 严格按真数据). */
export interface CronJob {
  id: string;
  name: string;
  prompt?: string | null;
  skills: string[];
  skill?: string | null;
  model?: string | null;
  schedule?: { kind?: string; expr?: string; display?: string } | null;
  schedule_display?: string | null;
  repeat?: { times?: number | null; completed?: number } | null;
  enabled: boolean;
  state: string;
  paused_at?: string | null;
  paused_reason?: string | null;
  created_at?: string | null;
  next_run_at?: string | null;
  last_run_at?: string | null;
  last_status?: "ok" | "error" | string | null;
  last_error?: string | null;
  last_delivery_error?: string | null;
  deliver?: string | null;
  // P3.5.106 P27 (6/25): 自动重试 5/10/15 三档状态
  catfish_retry_attempt?: number | null; // 0/1/2/3
  catfish_retry_exhausted?: boolean | null; // 3 次都失败后 true
}

export interface CronOutputMeta {
  timestamp: string;
  size_bytes: number;
  snippet: string;
}

/** 读 ~/.hermes/cron/jobs.json 返 jobs[]. 0 hermes 不影响, 返空 list. */
export const cronJobsList = () => rawInvoke<CronJob[]>("cron_jobs_list");

/** 列单 job 历史 outputs (按 mtime 倒序, limit 默认 10). */
export const cronJobOutputs = (jobId: string, limit?: number) =>
  rawInvoke<CronOutputMeta[]>("cron_job_outputs", { jobId, limit });

/** 读单个 output .md 完整内容. */
export const cronJobOutputRead = (jobId: string, timestamp: string) =>
  rawInvoke<string>("cron_job_output_read", { jobId, timestamp });

/** POST /api/cron/jobs/{id}/pause 走 P26 endpoint (走 hermes Python public function). */
export const cronJobPause = (jobId: string, reason?: string) =>
  rawInvoke<void>("cron_job_pause", { jobId, reason });

/** POST /api/cron/jobs/{id}/resume */
export const cronJobResume = (jobId: string) =>
  rawInvoke<void>("cron_job_resume", { jobId });

/** DELETE /api/cron/jobs/{id} — 真删 jobs.json 条目 + 清 output. */
export const cronJobDelete = (jobId: string) =>
  rawInvoke<void>("cron_job_delete", { jobId });

// BL-COMPANION-HERMES-API-CONFIG (5/19 Phase 2-2A): 暴露 hermes API server 配置
// 给 chat.ts. 不返 key (key 在 Rust 端拼 header, 不发 JS, 防 XSS / 误 log).
// Phase 2-2B 实施 chat.ts 切换时读这个判断走 gateway 还是 hermes.
export interface HermesApiConfigPublic {
  enabled: boolean;
  url: string;
  has_key: boolean;
}
export const hermesApiConfigGet = () =>
  rawInvoke<HermesApiConfigPublic>("hermes_api_config_get");
// BL-COMPANION-CHAT-SWITCH-TO-HERMES (5/19 Phase 2-2B): 拿完整 "Bearer <key>"
// 字符串塞 fetch headers. enabled=false 或没 key → 返 null, caller fallback gateway.
export const hermesApiAuthHeader = () =>
  rawInvoke<string | null>("hermes_api_auth_header");
export interface CreateDraftArgs {
  to: string;
  subject: string;
  body: string;
  cc?: string;
  bcc?: string;
  inReplyTo?: string;
  account?: string;
}
export const emailCreateDraft = (args: CreateDraftArgs) =>
  rawInvoke<string>("email_create_draft", {
    to: args.to,
    cc: args.cc ?? null,
    bcc: args.bcc ?? null,
    subject: args.subject,
    body: args.body,
    inReplyTo: args.inReplyTo ?? null,
    account: args.account ?? null,
  });
/** id → "急" | "中" | "低" map, scheduler 后台评级缓存. */
export const emailUrgencyMap = () =>
  rawInvoke<Record<string, string>>("email_urgency_map");

/** BL-EMAIL-URGENCY-BADGE (5/18): 前端主动评级一批邮件 (历史邮件也能评).
 * 已 cache 的跳过, 只评新 id. 返完整 cache map.
 * 性能: 一次评 batch (LLM 调一次), 前端最好 batch <=30 避免 prompt 太长. */
export const emailClassifyNow = (
  items: Array<{
    id: string;
    subject: string;
    sender: string;
    account?: string;
    date?: string;
    is_read?: boolean;
  }>,
) => rawInvoke<Record<string, string>>("email_classify_now", { items });

/** catfish-email list --json 单条 message 的 schema. 字段跟 base.py Message dataclass 对齐. */
export interface EmailDigestItem {
  id: string;
  account: string;
  folder: string;
  subject: string;
  sender: string;
  date: string;          // ISO-8601 UTC
  is_read: boolean;
  has_attachments: boolean;
  body_text: string;     // list 场景为 snippet
  // P3.5.58 (6/22 鸿波 catch "有回复了为啥还让小鲶处理"): RFC 822 thread chain
  // 三件套, 让 isReplied(msg, list) 算法可靠判定 — ∃ R: R.in_reply_to ==
  // M.message_id OR M.message_id ∈ R.references.split().
  // 老邮件 / 老 Mail.app 版本可能为 undefined, 算法兜空.
  message_id?: string;   // RFC 822 Message-ID, 形如 <abc@x.com>
  in_reply_to?: string;  // RFC 822 In-Reply-To, 直接父级 Message-ID
  references?: string;   // RFC 822 References, 空格分隔的完整祖先链
}
/** catfish-email accounts --json 单条 schema.
 * BL-EMAIL-MULTI-CLIENT (5/18): 多客户端时每个 account 多了 client 字段标识来源.
 */
export interface EmailAccountItem {
  name: string;
  address: string;
  is_default: boolean;
  client?: string;  // apple_mail / foxmail_mac, 老 CLI 输出可能没这字段
}
