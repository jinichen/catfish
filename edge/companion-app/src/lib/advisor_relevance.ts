/** P3.5.4 (6/16 鸿波): advisor 注入相关性筛选 wrapper.
 *
 * BGE-M3 本机 ONNX (~/.catfish/models/bge-m3.onnx) cosine 相似度 + sqlite cache.
 * advisor 注入 distilled_facts / hermes memory § / prev_tasks 之前调一下,
 * 按今天输入 (todos + emails + events) 排序, top-K 注入. 砍 prompt 50%+,
 * advisor Call 1 不再 truncated.
 *
 * 设计 (鸿波 6/16 拍): 不要粗暴 slice. distilled_facts / memory / prev_tasks
 * 跟今天输入有语义相关性才有效. BGE-M3 语义 vs jieba 词面 — 语义胜.
 */

import { invoke } from "@tauri-apps/api/core";

export interface RankedItem {
  /** 原 candidates 数组里的 index */
  idx: number;
  /** cosine 0-1, 越大越相关. embed 失败时返 0 */
  score: number;
  /** 这次是否命中 cache (debug 用 — 看缓存命中率) */
  fromCache: boolean;
}

export interface RelevanceResult {
  /** BGE-M3 model 是否成功加载. false 时 ranked 空, caller 走 fallback */
  modelLoaded: boolean;
  /** 按 score 降序的 ranked 列表 (全部). caller 自己 slice top-K */
  ranked: RankedItem[];
  /** 错误信息 (model 缺等). 仅 modelLoaded=false 时填 */
  message: string;
}

/** 调 Rust advisor_rank_relevance.
 *
 * source_hint: 'distilled' | 'memory' | 'prev_task' (debug 用, cache row 标记来源).
 *
 * 失败 / model 缺 → 返 modelLoaded=false, ranked 空. caller 应 fallback 全量注入.
 */
export async function rankRelevance(
  query: string,
  candidates: string[],
  sourceHint: "distilled" | "memory" | "prev_task",
): Promise<RelevanceResult> {
  if (!query.trim() || candidates.length === 0) {
    return { modelLoaded: true, ranked: [], message: "query 或 candidates 空" };
  }
  try {
    return await invoke<RelevanceResult>("advisor_rank_relevance", {
      query,
      candidates,
      sourceHint,
    });
  } catch (e) {
    console.warn("[advisor relevance] invoke 失败 (fallback 全量):", e);
    return { modelLoaded: false, ranked: [], message: String(e) };
  }
}

/** 用 RankedItem 数组从原 candidates 取 top-K, 按 score 排序后的真实 string 数组. */
export function pickTopK(
  candidates: string[],
  ranked: RankedItem[],
  k: number,
): string[] {
  return ranked
    .slice(0, Math.max(0, k))
    .map((r) => candidates[r.idx])
    .filter((s): s is string => typeof s === "string");
}

/** 调试: 看 cache 状态. */
export interface CacheStats {
  rows: number;
  sources: Array<{ source: string; count: number }>;
}
export async function getCacheStats(): Promise<CacheStats> {
  return invoke<CacheStats>("advisor_relevance_cache_stats");
}

// ─── 切段 helpers ─────────────────────────────────────────────────

/** distilled_facts.md 按 `### 蒸馏段 N` 切. 段头保留作 context. */
export function splitDistilledFacts(text: string): string[] {
  if (!text.trim()) return [];
  // 按 `### 蒸馏段` 切, keep delimiter 作 segment 起首
  const parts = text.split(/(?=### 蒸馏段)/g);
  return parts.map((p) => p.trim()).filter(Boolean);
}

/** hermes memory recent 按 `\n§\n` 切 (catfish-memory plugin 写入格式). */
export function splitMemoryRecent(text: string): string[] {
  if (!text.trim()) return [];
  return text.split(/\n\s*§\s*\n/g).map((p) => p.trim()).filter(Boolean);
}
