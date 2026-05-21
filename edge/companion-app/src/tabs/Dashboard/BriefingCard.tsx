/** 早安播报 — Phase 7 智能参谋 (5/21 鸿波拍板).
 *
 * 设计稿: docs/CATFISH-ADVISOR-DESIGN.md
 *
 * 角色: 只做 header (问候 + 日期 + 刷新按钮). 主菜 + 选项 + 草稿全在 AdvisorView.
 *   - 数据采集 / LLM 调用 / 解析 / 渲染都进 AdvisorView (单一职责)
 *   - 本文件就一个壳, 刷新按钮 increment refreshKey 让 AdvisorView re-load
 */

import { useState } from "react";

import AdvisorView from "../Briefing/AdvisorView";
import { getGreeting } from "../Briefing/components/helpers";

export default function BriefingCard() {
  const [refreshKey, setRefreshKey] = useState(0);

  const greeting = getGreeting();
  const today = new Date().toLocaleDateString("zh-CN", {
    month: "long",
    day: "numeric",
    weekday: "long",
  });

  return (
    <div
      style={{
        background: "var(--catfish-bg-elevated)",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-4)",
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
        <button
          type="button"
          onClick={() => setRefreshKey((k) => k + 1)}
          title="重新综合判断"
          style={{
            marginLeft: "auto",
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
      </header>

      <AdvisorView refreshKey={refreshKey} />
    </div>
  );
}
