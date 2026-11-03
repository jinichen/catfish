/** 早安播报 — Phase 7 智能参谋 (5/21 鸿波拍板).
 *
 * 设计稿: docs/CATFISH-ADVISOR-DESIGN.md
 *
 * 角色: 只做 header (问候 + 日期 + 刷新按钮). 主菜 + 选项 + 草稿全在 AdvisorView.
 *   - 数据采集 / LLM 调用 / 解析 / 渲染都进 AdvisorView (单一职责)
 *   - 本文件就一个壳, 刷新按钮 increment refreshKey 让 AdvisorView re-load
 */

import { useEffect, useState, type ReactNode } from "react";

import AdvisorView from "../Briefing/AdvisorView";
import RefreshInfo from "../Briefing/components/RefreshInfo";  // P3.5.32.9 (6/18): 时间挪 header
import { getGreeting } from "../Briefing/components/helpers";
import { weatherGet, type WeatherCache } from "../../lib/weather";  // P3.3.8 (6/10): 早安天气
import WeatherChip from "./WeatherChip";  // 11/3: 点天气设常驻城市

/** 11/3: headerExtra 放进问候行 (客户小标签), headerBelow 紧贴问候行下面 (加客户表单)。 */
export default function BriefingCard({ headerExtra, headerBelow }: { headerExtra?: ReactNode; headerBelow?: ReactNode } = {}) {
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
        // 8/8: 高度传递链的第二环, 见 BriefingTab 那段注释。
        //
        // 同时删掉了 `alignSelf: "start"` —— 它以前是 no-op (父容器那时不是 flex),
        // 现在父容器改成 flex column 之后它会**主动阻止拉伸**, 正好把这次的修复
        // 抵消掉。这种"以前无害、别处一改就变成 bug"的属性, 留着比删掉危险。
        flex: 1,
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
      }}
    >
      <header
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-2)",
          // 11/3 (鸿波 catch '空间太浪费'): space-3 → space-2; 下面 AdvisorView / 两栏的上边距也砍了
          marginBottom: "var(--space-2)",
          flexWrap: "wrap",
        }}
      >
        <strong style={{ fontSize: 15, color: "var(--catfish-text)" }}>{greeting}</strong>
        <span style={{ fontSize: 12, color: "var(--catfish-text-muted)" }}>{today}</span>
        <WeatherChip weather={weather} onChange={setWeather} />
        {headerExtra}
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
      {headerBelow && <div style={{ marginBottom: "var(--space-2)" }}>{headerBelow}</div>}

      <AdvisorView refreshKey={refreshKey} />
    </div>
  );
}
