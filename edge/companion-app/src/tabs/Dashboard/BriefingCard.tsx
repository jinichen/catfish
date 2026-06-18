/** 早安播报 — Phase 7 智能参谋 (5/21 鸿波拍板).
 *
 * 设计稿: docs/CATFISH-ADVISOR-DESIGN.md
 *
 * 角色: 只做 header (问候 + 日期 + 刷新按钮). 主菜 + 选项 + 草稿全在 AdvisorView.
 *   - 数据采集 / LLM 调用 / 解析 / 渲染都进 AdvisorView (单一职责)
 *   - 本文件就一个壳, 刷新按钮 increment refreshKey 让 AdvisorView re-load
 */

import { useEffect, useState } from "react";

import AdvisorView from "../Briefing/AdvisorView";
import RefreshInfo from "../Briefing/components/RefreshInfo";  // P3.5.32.9 (6/18): 时间挪 header
import { getGreeting } from "../Briefing/components/helpers";
import { weatherGet, type WeatherCache } from "../../lib/weather";  // P3.3.8 (6/10): 早安天气

export default function BriefingCard() {
  const [refreshKey, setRefreshKey] = useState(0);
  // P3.3.8 (6/10): 早安天气 — 走 ~/.catfish/weather_cache.json (6h cache).
  // mount 时拉一次, refresh 按钮也触发. wttr.in 国内访问慢 / Clash 拦时显错.
  const [weather, setWeather] = useState<WeatherCache | null>(null);

  useEffect(() => {
    let cancelled = false;
    void weatherGet(false /* not force, 走 cache */).then((w) => {
      if (!cancelled) setWeather(w);
    }).catch((e) => {
      console.warn("[BriefingCard] weather 拉失败:", e);
    });
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  const greeting = getGreeting();
  const today = new Date().toLocaleDateString("zh-CN", {
    month: "long",
    day: "numeric",
    weekday: "long",
  });

  return (
    <div
      style={{
        // P3.5.32.8 (6/18 鸿波 catch '外框浪费空间'):
        //   老 bg-elevated + border + padding var(--space-4) 形成卡片外框,
        //   跟 BriefingTab outer container 双层 framing, 双侧大量浪费.
        //   改: 砍 bg + border, 让内容直接在 tab bg 上显. 留小 padding 防边贴边.
        padding: "var(--space-2)",
        alignSelf: "start",
      }}
    >
      <header
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-2)",
          marginBottom: "var(--space-3)",
        }}
      >
        <strong style={{ fontSize: 15, color: "var(--catfish-text)" }}>{greeting}</strong>
        <span style={{ fontSize: 12, color: "var(--catfish-text-muted)" }}>{today}</span>
        {weather && weather.entries.length > 0 && (
          <span
            style={{
              fontSize: 12,
              color: "var(--catfish-text-muted)",
              display: "inline-flex",
              gap: 8,
              alignItems: "center",
            }}
            title={
              weather.stale
                ? `天气数据 stale (>6h), 刷新拉新. fetched=${weather.fetchedAt}`
                : `wttr.in · fetched=${weather.fetchedAt}`
            }
          >
            {weather.entries.map((e, i) => (
              <span key={i}>
                {e.icon} {e.city || "本地"} {e.tempC}°C
                {e.desc && <span style={{ opacity: 0.7 }}> {e.desc}</span>}
              </span>
            ))}
          </span>
        )}
        {weather?.error && weather.entries.length === 0 && (
          <span
            style={{ fontSize: 11, color: "var(--catfish-text-muted)", opacity: 0.7 }}
            title={weather.error}
          >
            🌐 天气不可用
          </span>
        )}
        {/* P3.5.32.9 (6/18 鸿波 catch '时间和刷新放一行'): RefreshInfo 挪到 header,
            放在刷新按钮左边. flex-end 自动右对齐. */}
        <div
          style={{
            marginLeft: "auto",
            display: "flex",
            alignItems: "center",
            gap: "var(--space-3)",
          }}
        >
          <RefreshInfo />
          <button
            type="button"
            onClick={() => setRefreshKey((k) => k + 1)}
            title="重新综合判断"
            style={{
              background: "transparent",
              border: "1px solid var(--catfish-border)",
              borderRadius: "var(--radius-sm)",
              color: "var(--catfish-text-muted)",
              cursor: "pointer",
              fontSize: 12,
              padding: "3px 10px",
              fontFamily: "inherit",
            }}
          >
            刷新
          </button>
        </div>
      </header>

      <AdvisorView refreshKey={refreshKey} />
    </div>
  );
}
