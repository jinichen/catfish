/** 早安「今天要复习的题」(11/02, 借 DeepTutor Practice)。
 *
 * 题库在员工本机 (tool-bridge catfish_practice → ~/.catfish/practice.db)。答错的题按间隔重复排复习,
 * 今天有到期的就在早安顶上提一句。没有要复习的不显示。
 * 11/18: 点「开始复习」去「学习」页的复习 (一张一张翻卡、按想起来的程度排下次; docs/LEARNING-PLAN.md §5),
 * 不再塞进工作台对话框。
 */
import { useEffect, useState } from "react";

import { practiceStats, type PracticeStats } from "../../lib/tauri_practice";
import { useUIStore } from "../../store/ui";

export default function PracticeStrip() {
  const [stats, setStats] = useState<PracticeStats | null>(null);
  const openLearnReview = useUIStore((s) => s.openLearnReview);

  useEffect(() => {
    let alive = true;
    practiceStats().then((s) => { if (alive) setStats(s); }).catch(() => undefined);
    return () => { alive = false; };
  }, []);

  if (!stats || stats.dueToday <= 0) return null;
  return (
    <div role="status" style={{ display: "flex", alignItems: "center", gap: 12, padding: "8px 12px", margin: "0 0 8px",
      borderRadius: "var(--radius-md)", background: "var(--catfish-bg-elevated)", border: "1px solid var(--catfish-border, #e3e3dc)" }}>
      <span style={{ flex: 1 }}>
        📚 今天有 <b>{stats.dueToday}</b> 条要复习
        {stats.reviewedToday > 0 && <span style={{ color: "var(--catfish-text-muted)" }}> · 今天已复习 {stats.reviewedToday} 道</span>}
      </span>
      <button type="button" className="primary" onClick={openLearnReview}>
        开始复习
      </button>
    </div>
  );
}
