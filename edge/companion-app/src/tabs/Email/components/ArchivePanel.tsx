import { useEffect, useState } from "react";

import {
  RETENTION_LABELS,
  getArchiveStatus,
  normalizeRetention,
  type ArchiveStatus,
} from "../../../lib/tauri_imap";

/**
 * 邮件档案进度。
 *
 * # 这块存在的理由
 *
 * 归档是**后台渐进**的: 每次收信/轮询推进一批 (ARCHIVE_BATCH=20)。也就是说
 * 员工开了功能之后, 在相当长一段时间里"什么都没发生"是正常状态。没有进度
 * 显示的话, 这个正常状态和"坏了"完全分不开 —— 他只能等, 而且不知道在等什么。
 *
 * # 为什么"已校验"和"已落地"分开显示
 *
 * 服务器端清理只认**已校验**。落地了但没校验通过的那些, 既没受保护也没被
 * 删除, 处在中间态。两个数一样大是健康的; 持续不一样说明有邮件卡在校验上,
 * 那是要查的。把它们合成一个"已归档 N 封"会把这个信号抹掉。
 */
export default function ArchivePanel() {
  const [stat, setStat] = useState<ArchiveStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try {
      setStat(await getArchiveStatus());
      setError(null);
    } catch (e) {
      // 没配 IMAP / 组件没装好都会走到这里。这块是附属信息, 报错不该
      // 盖过邮件列表本身 —— 所以只记下来, 由下面决定显不显示。
      setError(String(e));
      setStat(null);
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  // 没配过 IMAP 就整块不出现。空状态卡片只会占版面, 而这时候员工该做的
  // 是先配 IMAP —— 那个入口就在上面。
  if (error || !stat || stat.total === 0) return null;

  const pct = stat.total > 0 ? Math.round((stat.verified / stat.total) * 100) : 0;
  const pending = Math.max(0, stat.total - stat.archived);
  const unverified = Math.max(0, stat.archived - stat.verified);

  return (
    <div
      style={{
        marginTop: 8,
        padding: "10px 12px",
        border: "1px solid var(--catfish-border)",
        borderRadius: 8,
        background: "var(--catfish-bg)",
        fontSize: 12,
        lineHeight: 1.5,
      }}
    >
      <strong>邮件档案</strong>

      <div style={{ marginTop: 6, display: "flex", alignItems: "center", gap: 8 }}>
        <div
          style={{
            flex: 1,
            height: 6,
            borderRadius: 3,
            background: "var(--catfish-border)",
            overflow: "hidden",
          }}
          role="progressbar"
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="邮件归档进度"
        >
          <div style={{ width: `${pct}%`, height: "100%", background: "var(--catfish-accent, #2e7d6b)" }} />
        </div>
        <span style={{ color: "var(--catfish-muted)" }}>{pct}%</span>
      </div>

      <div style={{ marginTop: 6, color: "var(--catfish-muted)" }}>
        已校验 {stat.verified} / {stat.total} 封 · {formatBytes(stat.bytes)}
        {/* 待归档不为 0 是**正常**的, 要说清楚, 否则员工会当成卡住了。
            归档每轮只推进一批, 本来就要跑一阵子。 */}
        {pending > 0 && <> · 还有 {pending} 封排队中（每次收信推进一批）</>}
        {unverified > 0 && (
          <span style={{ color: "var(--status-danger)" }}>
            {" "}· {unverified} 封落地了但没通过校验
          </span>
        )}
      </div>

      {/* 只在本地档案里的那些 —— 这正是整套东西的兑现点。
          有这个数就说明"服务器清理了本地还在"真的发生过。 */}
      {stat.only_local > 0 && (
        <div style={{ marginTop: 4 }}>
          其中 {stat.only_local} 封服务器上已经没有了，只在本地档案里
        </div>
      )}

      <div style={{ marginTop: 4, color: "var(--catfish-muted)" }}>
        归档后: {RETENTION_LABELS[normalizeRetention(stat.retention)]}
      </div>

      <button
        type="button"
        onClick={() => void refresh()}
        style={{
          marginTop: 8,
          background: "none",
          border: "none",
          padding: 0,
          font: "inherit",
          color: "var(--catfish-text-muted)",
          cursor: "pointer",
          textDecoration: "underline",
        }}
      >
        刷新进度
      </button>
    </div>
  );
}

/** 给人看的体积。档案会长到几个 GB, 显示成字节数没人读得出来。 */
export function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  // 整数部分小于 10 时留一位小数: "1.4 GB" 比 "1 GB" 有用得多,
  // 而 "847 MB" 再加小数就是噪声。
  return `${v < 10 && i > 0 ? v.toFixed(1) : Math.round(v)} ${units[i]}`;
}
