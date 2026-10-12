/** 销售跟进 (10/12) —— Rust commands/sales.rs 的前端封装。数据规则在 tool-bridge customers.py / customer_radar.py / sales_pricebook.py。 */
import { invoke } from "@tauri-apps/api/core";

export type RadarRule = "reply" | "promise" | "cold" | "quote";

export interface RadarCard {
  uid: string;
  rule: RadarRule;
  label: string;
  customer: string;
  stage: string | null;
  rel_path: string;
  /** 依据: 客户 / 我方原话要点 */
  quote: string;
  age_days: number;
  next_action?: string | null;
  channel?: string;
  due?: string;
  /** promise 卡来自时间线时才有 —— 有它才能一键「已兑现」 */
  promise_text?: string;
  cold_days?: number;
}

export interface RadarResult {
  cards: RadarCard[];
  customers: number;
  template: string;
}

export interface SalesStage {
  name: string;
  cold_days: number;
}

export interface SalesTemplate {
  name: string;
  stages: SalesStage[];
  quoted_stage: string;
  reply_wait_days: number;
  quote_followup_days: number;
}

export const salesCustomerRadar = () => invoke<RadarResult>("sales_customer_radar");
export const salesTemplateGet = () => invoke<SalesTemplate>("sales_template_get");
export const salesTemplateSet = (template: SalesTemplate) => invoke<SalesTemplate>("sales_template_set", { template });
export const salesPromiseDone = (customer: string, text: string) =>
  invoke<{ ok: boolean; done?: string }>("sales_promise_done", { customer, text });

// ── 阶段 2: 价格表 + 报价单设置 ──

export interface PricebookItem {
  name: string;
  spec: string;
  unit: string;
  price: number;
  floor: number | null;
  note: string;
}

export interface Pricebook {
  source: string | null;
  sheet?: string;
  imported_at?: string;
  columns?: Record<string, string>;
  items: PricebookItem[];
  /** 只在刚导入时有 */
  count?: number;
  skipped?: number;
}

export interface QuoteSettings {
  company: string;
  contact: string;
  phone: string;
  email: string;
  valid_days: number;
  terms: string;
  /** 有没有导入公司自己的报价单模板 (只读) */
  custom_template: boolean;
}

export const salesPickXlsx = (title: string) => invoke<string | null>("sales_pick_xlsx", { title });
export const salesPricebookImport = (path: string) => invoke<Pricebook>("sales_pricebook_import", { path });
export const salesPricebookGet = () => invoke<Pricebook>("sales_pricebook_get");
export const salesQuoteSettingsGet = () => invoke<QuoteSettings>("sales_quote_settings_get");
export const salesQuoteSettingsSet = (settings: Omit<QuoteSettings, "custom_template">) =>
  invoke<QuoteSettings>("sales_quote_settings_set", { settings });
export const salesQuoteTemplateImport = (path: string) =>
  invoke<QuoteSettings & { placeholders: string[] }>("sales_quote_template_import", { path });
export const salesQuoteTemplateClear = () => invoke<QuoteSettings>("sales_quote_template_clear");

// ── 阶段 3: 发布给主管 ──

export interface PublishedCustomer {
  name: string;
  stage: string | null;
  owner: string;
  deal_size: string;
  deal_value: number | null;
  last_contact: string;
  days_since_contact: number | null;
  next_action: string;
  next_action_due: string;
  flags: string[];
  quotes: number;
  latest_quote_total: number | null;
}

export interface SalesSummary {
  generated_at: string;
  owner: string;
  funnel: { stage: string; count: number; deal_total: number }[];
  customers: PublishedCustomer[];
}

export interface PublishPreview {
  summary: SalesSummary;
  published_at: string | null;
  namespace: string | null;
}

export const salesPublishPreview = () => invoke<PublishPreview>("sales_publish_preview");
export const salesPublish = () => invoke<{ ok: boolean; namespace: string; published_at: string }>("sales_publish");
export const salesPublishWithdraw = () => invoke<{ ok: boolean; unpublished: boolean }>("sales_publish_withdraw");
