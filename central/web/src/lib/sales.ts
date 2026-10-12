// 部门销售漏斗 (销售跟进阶段 3, docs/SALES-FOLLOWUP-PLAN.md §11)。
// 数据 = 每个销售在 Companion 里主动「发布给主管」的结构化摘要 (wiki-hub), 不含聊天原文。
// 只有 admin 和管这个部门的 manager 能拿到 (gateway + wiki-hub 两处都查)。
import { api } from "./api";

export interface SalesCustomerRow {
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

export interface SalesMember {
  published_by: string;
  updated_at: string;
  owner: string;
  generated_at: string;
  funnel: { stage: string; count: number; deal_total: number }[];
  followup: Record<string, number>;
  week: { new_customers: number; quotes_made: number; quotes_sent: number; stage_changes: unknown[] };
  customers: SalesCustomerRow[];
}

export interface DepartmentSales {
  department: string;
  members: SalesMember[];
  funnel: { stage: string; count: number; deal_total: number }[];
}

export async function fetchDepartmentSales(dept: string): Promise<DepartmentSales> {
  return api.get<DepartmentSales>(`/v1/wiki/sales/dept/${encodeURIComponent(dept)}`);
}
