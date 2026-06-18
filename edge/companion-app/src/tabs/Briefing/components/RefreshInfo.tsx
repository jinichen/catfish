/** P3.5.32.9 (6/18 鸿波 catch '时间和刷新放一行') — RefreshInfo 抽独立 component.
 *
 * 老路径: AdvisorView 内 RefreshInfo 独立一行右对齐 ('上次 12:43 ...· 下次自动 14:00').
 * 鸿波 catch: 跟 BriefingCard 标题行 (中午好 + 日期 + 天气 + 刷新按钮) 分开占两行,
 * 浪费垂直空间.
 *
 * fix: 抽到独立 component, 由 BriefingCard header 调用, 放在刷新按钮左边,
 * 一行搞定. AdvisorView 不再 render 这条.
 *
 * BriefingCard 自己 polling advisorCacheGet + advisorConfigGet 5s 更新.
 */

import { useEffect, useState } from "react";

import {
  type AdvisorCache,
  type AdvisorConfig,
  advisorCacheGet,
  advisorConfigGet,
  cacheAgeMinutes,
  nextRefreshAfter,
} from "../../../lib/advisor_cache";

export default function RefreshInfo() {
  const [cache, setCache] = useState<AdvisorCache | null>(null);
  const [config, setConfig] = useState<AdvisorConfig | null>(null);

  // mount 拉 config 一次, cache 走 polling (advisor 完成时会写, 间隔短点能感知)
  useEffect(() => {
    let cancelled = false;
    void advisorConfigGet().then((c) => {
      if (!cancelled) setConfig(c);
    }).catch(() => {});

    const fetchCache = async () => {
      try {
        const c = await advisorCacheGet();
        if (!cancelled) setCache(c);
      } catch {
        /* silent */
      }
    };
    void fetchCache();
    // 5s polling — advisor LLM 完成后下次 tick 看到. 比 60s 反馈快.
    const timer = window.setInterval(fetchCache, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  if (!cache || !config) return null;

  const computedDate = new Date(cache.computedAt);
  const computedHHMM = computedDate.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  const next = nextRefreshAfter(new Date(), config.refreshTimes);
  const nextHHMM = next
    ? next.toLocaleTimeString("zh-CN", {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      })
    : "明天 " + (config.refreshTimes[0] ?? "08:30");

  const ageMin = Math.round(cacheAgeMinutes(cache));
  const ageLabel =
    ageMin < 1
      ? "刚刚"
      : ageMin < 60
      ? `${ageMin} 分钟前`
      : `${(ageMin / 60).toFixed(1)} 小时前`;

  return (
    <span
      style={{
        fontSize: 11,
        color: "var(--catfish-text-muted)",
        opacity: 0.8,
        whiteSpace: "nowrap",
      }}
    >
      上次 {computedHHMM} ({ageLabel}) · 下次 {nextHHMM}
    </span>
  );
}
