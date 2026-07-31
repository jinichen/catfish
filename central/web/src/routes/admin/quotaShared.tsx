/** /admin/quota 的共享件 —— 紧凑样式 + MiniSection + 通用数字行 (8/1 拆出).
 *
 * QuotaConfigPage 到了 812 行, **已经越过 CLAUDE.md 军规 §1 的 800 行红线**
 * (check_file_sizes 的必拆名单里有它)。这次要给两个删除加确认对话框, 不拆
 * 只会更糟。
 *
 * 切口是"共享件 / defaults 三段 / overrides 两段":
 *   quotaShared.tsx    这个文件 —— 样式常量 + MiniSection + NumericRow
 *   QuotaConfigPage    编排 + defaults 的 per_user / per_model / per_department
 *   QuotaOverrides     overrides.users + overrides.departments
 *
 * 样式常量放这里是因为**三个文件都要**, 而它们正是 P3.5.93.1 那轮 compact
 * 化的结论 (行高 40px → 28px)。散成三份很快就会各自漂移。
 */

import { useState, type ReactNode } from "react";

import { ConfirmDialog } from "../../components/Dialog";

// P3.5.93.2 (6/23 鸿波): 紧凑 MiniSection 替 Card — Card 全 web 共用, 不能
// 为了这一页改大改。改用更紧的 box: padding 8px (vs Card 16px), 标题
// fontSize 12 (vs h3 ~18-20)。总省 vertical space ~50%, 配合整页 2 列
// grid 才能一屏看完 5 个 section。
//
// (8/1 拆文件时这段注释一度掉了。留着是因为下一个人看到"这里为什么不用
//  Card"时, 答案不在代码里。)
export function MiniSection({
  title,
  children,
}: {
  title: ReactNode;
  children: ReactNode;
}) {
  return (
    <div
      style={{
        background: "var(--bg-elev)",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-md)",
        padding: 8,
      }}
    >
      <h4
        style={{
          margin: 0,
          marginBottom: 4,
          fontSize: 12,
          fontWeight: 600,
          color: "var(--text)",
        }}
      >
        {title}
      </h4>
      {children}
    </div>
  );
}

// ── compact styles (P3.5.93.1) ─────────────────────────────
// 全部 export —— 三个文件共用, 见文件头。

export const TABLE_HEAD: React.CSSProperties = {
  display: "grid",
  gap: 8,
  padding: "4px 0",
  borderBottom: "1px solid var(--border)",
  fontWeight: 600,
  color: "var(--text-muted)",
  fontSize: 11,
  textTransform: "uppercase",
  letterSpacing: 0.3,
};
export const TABLE_ROW: React.CSSProperties = {
  display: "grid",
  gap: 8,
  padding: "4px 0",
  borderBottom: "1px solid var(--border-soft)",
  alignItems: "center",
  fontSize: 12,
};
export const TABLE_ADD: React.CSSProperties = {
  ...TABLE_ROW,
  borderBottom: "none",
  padding: "6px 0 0 0",
};
export const INPUT: React.CSSProperties = {
  padding: "2px 6px",
  border: "1px solid var(--border)",
  borderRadius: 3,
  fontFamily: "monospace",
  fontSize: 12,
  width: "100%",
  boxSizing: "border-box",
};
export const BTN: React.CSSProperties = {
  padding: "2px 8px",
  border: "1px solid var(--border)",
  background: "transparent",
  borderRadius: 3,
  cursor: "pointer",
  fontSize: 11,
  whiteSpace: "nowrap",
};
export const BTN_DANGER: React.CSSProperties = {
  ...BTN,
  borderColor: "#d9534f",
  color: "#d9534f",
};
export const BTN_PRIMARY: React.CSSProperties = {
  ...BTN,
  background: "var(--accent)",
  color: "white",
  borderColor: "var(--accent)",
};
export const TPL_SIMPLE = "1fr 130px 60px 50px 40px"; // name / tpd input / fmt / save / del
export const TPL_USER = "1fr 120px 120px 60px 50px 40px"; // email / tpm / tpd / fmt / save / del

export function fmtTokens(n: number): string {
  if (n === 0) return "∞";
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(0)}K`;
  if (n < 1_000_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  return `${(n / 1_000_000_000).toFixed(1)}B`;
}

// ── 通用单字段 row (per_model / per_dept / dept_override 共用) ──

export function NumericRow({
  tpl,
  label,
  current,
  onSave,
  onDelete,
  successMsg,
  onError,
  onSuccess,
}: {
  tpl: string;
  label: string;
  current: number;
  onSave: (tokens_per_day: number) => Promise<unknown>;
  onDelete: () => Promise<unknown>;
  successMsg: (action: "存" | "删") => string;
  onError: (e: unknown) => void;
  onSuccess: (msg: string) => void;
}) {
  const [tpd, setTpd] = useState(String(current));
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [delErr, setDelErr] = useState<string | null>(null);
  const changed = parseInt(tpd, 10) !== current;

  const save = async () => {
    setBusy(true);
    try {
      await onSave(parseInt(tpd, 10) || 0);
      onSuccess(successMsg("存"));
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  };
  const del = async () => {
    setBusy(true);
    setDelErr(null);
    try {
      await onDelete();
      setConfirming(false);
      onSuccess(successMsg("删"));
    } catch (e) {
      // ⚠ 不走 onError。onError 会把错误抛到 QuotaConfigEditor 的 err 上,
      // 而那个是**早返回**的 (`if (err) return <MiniSection title="错误">`)
      // —— 整个编辑器会被一张错误卡换掉, 其他行没保存的输入一起丢。
      // 一次删除失败不该有这么大的爆炸半径, 错误留在对话框里。
      setDelErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ ...TABLE_ROW, gridTemplateColumns: tpl }}>
      <div style={{ fontFamily: "monospace", fontSize: 12 }}>{label}</div>
      <input
        type="number"
        value={tpd}
        onChange={(e) => setTpd(e.target.value)}
        min={0}
        style={INPUT}
      />
      <span style={{ fontSize: 11, color: "var(--text-muted)" }}>{fmtTokens(parseInt(tpd, 10) || 0)}</span>
      <button onClick={save} disabled={!changed || busy} style={changed ? BTN_PRIMARY : BTN}>
        {busy ? "…" : "存"}
      </button>
      <button onClick={() => setConfirming(true)} disabled={busy} style={BTN_DANGER}>
        删
      </button>
      {confirming && (
        <ConfirmDialog
          danger
          title={`删掉 ${label} 的配额规则?`}
          confirmLabel="删除"
          busy={busy}
          onCancel={() => {
            setConfirming(false);
            setDelErr(null);
          }}
          onConfirm={() => void del()}
        >
          删掉之后这一项<b>回落到上一层的默认值</b>，不是变成"不限"。
          <div style={{ marginTop: 6, color: "var(--text-muted)" }}>
            规则写在 quotas.yaml，改完对新请求立即生效。
          </div>
          {delErr && (
            <div style={{ marginTop: 8, color: "var(--status-err)" }}>
              删除失败：{delErr}
            </div>
          )}
        </ConfirmDialog>
      )}
    </div>
  );
}
