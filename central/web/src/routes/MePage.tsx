/** /me — 我的概览 (BL-ARCH1 5/10).
 *
 * 跟 Companion 的 IdentityCard / QuotaCard 视角一致, 但用 web 横向更宽屏排版.
 */

import { useEffect, useState } from "react";

import { Card, Row } from "../components/Card";
import { fetchQuotaMe, type QuotaMe } from "../lib/me";
import { useAuthStore } from "../store/auth";

function fmtTokens(n: number): string {
  if (n === 0) return "0";
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}K`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

function fmtLimit(limit: number): string {
  if (limit === 0) return "不限";
  return fmtTokens(limit);
}

export function MePage() {
  const me = useAuthStore((s) => s.me);
  const [quota, setQuota] = useState<QuotaMe | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchQuotaMe()
      .then(setQuota)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  if (!me) return null;

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "1fr 1fr",
        gap: "var(--space-4)",
      }}
    >
      <Card title="身份">
        <Row label="员工" value={me.email} />
        <Row label="部门" value={me.department || "-"} />
        <Row label="角色" value={me.role} />
        {me.managed_departments.length > 0 && (
          <Row
            label="管理"
            value={me.managed_departments.join(", ")}
          />
        )}
        <Row label="登录方式" value={me.auth_method} mono />
      </Card>

      <Card title="今日配额">
        {error && <div style={{ color: "var(--status-err)" }}>{error}</div>}
        {!quota && !error && <div>加载中…</div>}
        {quota && (
          <>
            <Row
              label="近 1 分钟"
              value={`${fmtTokens(quota.minute.used)} / ${fmtLimit(quota.minute.limit)}`}
            />
            <Row
              label="今日 (24h 滑动)"
              value={`${fmtTokens(quota.day.used)} / ${fmtLimit(quota.day.limit)}`}
            />
            <Row
              label={`部门 ${quota.department} 今日`}
              value={`${fmtTokens(quota.department_day.used)} / ${fmtLimit(quota.department_day.limit)}`}
            />
            <div
              style={{
                marginTop: "var(--space-3)",
                fontSize: 11,
                color: "var(--text-muted)",
              }}
            >
              数据实时来自 gateway · /api/quota/me
            </div>
          </>
        )}
      </Card>

      <Card title="详细数据 (在桌面 Companion 看)">
        <div style={{ color: "var(--text-muted)", fontSize: 13 }}>
          <p>这些跟"我"绑定的, 在桌面 app 看更顺手 (操作类立刻反馈):</p>
          <ul style={{ paddingLeft: 20, lineHeight: 1.8 }}>
            <li>我的画像 (writing_style / personality / work_pattern)</li>
            <li>鲶鱼对我的印象 (employee_journal 总结)</li>
            <li>鲶鱼记的具体信息 (session_facts 硬事实)</li>
            <li>我装的 skill / mcp 列表 (操作: 装/卸/重连)</li>
            <li>我今日学的 (LearningCard)</li>
            <li>我的 skill 改进建议 (SkillRevisionCard)</li>
            <li>对话 / 桌宠 / 快捷键 / 语音</li>
          </ul>
        </div>
      </Card>
    </div>
  );
}
