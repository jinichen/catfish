/** 顶部 Advisory Banner — 6/7 BL-MANIFESTO-ADVISORY-PHASE1.
 *
 * 显示规则 (跟 spec §4.3):
 *   - critical → 红色, 不可永久 dismiss (24h 后重新弹)
 *   - high → 红色, 可 "稍后提醒 24h" / "我已了解" / "忽略"
 *   - medium → 黄色, 可 dismiss
 *   - low → 灰色 (折叠 — Phase 1 暂不展示 banner, 只 list)
 *   - info → 仅 advisory 列表, 不弹 banner
 *
 * Phase 1: 顶部只展示 banners[0] (最高 severity 那条), 多条堆叠 Phase 2 加.
 *
 * 跟 manifesto 公理 3 (中央 0 控制) 一致 — 员工有"忽略"选项, 状态留本机.
 */

import { useEffect, useState } from "react";

import {
  advisoryAck,
  advisoryDismiss,
  advisoryMarkShown,
  advisorySnooze,
} from "../lib/advisory";
import { useAdvisory } from "../hooks/useAdvisory";
import type { Advisory } from "../types/advisory";

export default function AdvisoryBanner() {
  const { banners, reload } = useAdvisory();
  const [busy, setBusy] = useState<string | null>(null);

  // Phase 1 只展示最高 severity 那条 (排序已在 hook 里做了)
  const current: Advisory | undefined = banners[0];

  // 弹出时 mark_shown (本机 state 记 last_shown, 不上报中央)
  useEffect(() => {
    if (current) {
      void advisoryMarkShown(current.id);
    }
  }, [current?.id]);

  if (!current) return null;
  // info / low 不弹 banner (按 spec, 只进 advisory list 页)
  if (current.severity === "info" || current.severity === "low") return null;

  const onAck = async () => {
    setBusy(current.id);
    try {
      await advisoryAck(current.id);
      await reload();
    } finally {
      setBusy(null);
    }
  };

  const onSnooze = async () => {
    setBusy(current.id);
    try {
      await advisorySnooze(current.id, 24);
      await reload();
    } finally {
      setBusy(null);
    }
  };

  const onDismiss = async () => {
    setBusy(current.id);
    try {
      await advisoryDismiss(current.id);
      await reload();
    } finally {
      setBusy(null);
    }
  };

  const cls = `advisory-banner advisory-banner--${current.severity}`;
  const disabled = busy === current.id;
  const isCritical = current.severity === "critical";

  return (
    <div className={cls} role="alert" aria-live="polite">
      <div className="advisory-banner__icon">
        {isCritical ? "⛔" : current.severity === "high" ? "⚠" : "ℹ"}
      </div>
      <div className="advisory-banner__body">
        <div className="advisory-banner__title">{current.title}</div>
        {current.recommendation && (
          <div className="advisory-banner__rec">{current.recommendation}</div>
        )}
      </div>
      <div className="advisory-banner__actions">
        <button
          className="advisory-banner__btn advisory-banner__btn--primary"
          onClick={() => void onAck()}
          disabled={disabled}
          title="标记为已处理"
        >
          我已了解
        </button>
        <button
          className="advisory-banner__btn advisory-banner__btn--link"
          onClick={() => void onSnooze()}
          disabled={disabled}
          title="24 小时后重新提醒"
        >
          稍后提醒
        </button>
        {!isCritical && (
          <button
            className="advisory-banner__btn advisory-banner__btn--link"
            onClick={() => void onDismiss()}
            disabled={disabled}
            title="忽略此提醒"
          >
            忽略
          </button>
        )}
      </div>
    </div>
  );
}
