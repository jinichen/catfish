/** 销售跟进 (10/12) —— Rust commands/sales.rs 的前端封装。数据规则在 tool-bridge customers.py / customer_radar.py。 */
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
