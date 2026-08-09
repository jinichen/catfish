/** Dashboard 卡 — "命令审批建议" (P47, 8/9 鸿波)
 *
 * 接 hermes 的 `approvals suggest`: 它挖会话库, 找出被危险命令分类器判过、但你
 * 实际批准执行了的命令, 把反复出现的模式排出来。勾选应用 = 加进
 * `command_allowlist`, 以后同款命令不再弹审批。
 *
 * # 这张卡的措辞原则
 *
 * 它是**放宽安全策略**的入口, 不是省事的小工具。所以:
 *   · 标题和说明都要讲清楚"加进去之后会静默放行"
 *   · 默认一条都不勾 —— 不做"全选"这种降低摩擦的设计
 *   · 应用后把结果如实报出来, 包括**没能加进去的那几条**
 *
 * # 为什么按 pattern 不按序号
 *
 * 见 lib/approvalSuggestions.ts。简单说: 渲染和点击之间列表会变, 按序号可能
 * 加错条目, 而加错一条 = 一类命令从此静默放行。
 *
 * # 安全过滤不在这里
 *
 * hermes 侧已经承诺"破坏性/提权/凭据/混淆类永不提议"。前端不再滤一层 ——
 * 两套判据会打架, 真要变严该在 hermes 那层变。
 */

import { useCallback, useEffect, useState } from "react";

import {
  type ApplyResult,
  type ApprovalSuggestions,
  applyApprovalPatterns,
  describeReason,
  fetchApprovalSuggestions,
} from "../../lib/approvalSuggestions";

export default function ApprovalSuggestionsCard() {
  const [data, setData] = useState<ApprovalSuggestions | null>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [applying, setApplying] = useState(false);
  const [result, setResult] = useState<ApplyResult | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setResult(null);
    const out = await fetchApprovalSuggestions();
    setData(out);
    // 列表重新拉过 → 旧的勾选一律清掉。留着会让员工以为还选着,
    // 而那些 pattern 可能已经不在新列表里了。
    setSelected(new Set());
    setLoading(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const toggle = (pattern: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(pattern)) next.delete(pattern);
      else next.add(pattern);
      return next;
    });
  };

  const onApply = async () => {
    if (!selected.size || applying) return;
    setApplying(true);
    const out = await applyApprovalPatterns([...selected]);
    setResult(out);
    setApplying(false);
    if (out.applied.length) await load();
  };

  const muted = { color: "var(--catfish-text-muted)" } as const;

  return (
    <div style={{ fontSize: 13, lineHeight: 1.6 }}>
      <div style={{ marginBottom: 10, ...muted, fontSize: 12 }}>
        小鲶翻了你的历史命令，找出<strong>反复批准过</strong>的那几类。
        勾选后加入免审批清单 —— 以后同类命令<strong>不再弹窗询问，直接执行</strong>。
        <br />
        删除、提权、改凭据这些永远不会出现在这里（hermes 侧硬性排除）。
      </div>

      {loading && <div style={muted}>正在分析历史命令…</div>}

      {!loading && data && !data.ok && (
        <div style={{ ...muted, padding: "8px 0" }}>
          {describeReason(data.reason)}
          <button type="button" onClick={() => void load()} style={linkBtn}>
            重试
          </button>
        </div>
      )}

      {!loading && data?.ok && data.proposals.length === 0 && (
        <div style={muted}>
          没有可提议的模式。
          {typeof data.days === "number" && `（扫了最近 ${data.days} 天）`}
          <button type="button" onClick={() => void load()} style={linkBtn}>
            重新分析
          </button>
        </div>
      )}

      {!loading && data?.ok && data.proposals.length > 0 && (
        <>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {data.proposals.map((p) => (
              <label
                key={p.pattern}
                style={{
                  display: "flex",
                  gap: 8,
                  alignItems: "flex-start",
                  padding: "8px 10px",
                  border: "1px solid var(--catfish-border)",
                  borderRadius: "var(--radius-sm)",
                  cursor: "pointer",
                  background: selected.has(p.pattern)
                    ? "var(--catfish-hint-amber-bg)"
                    : "transparent",
                }}
              >
                <input
                  type="checkbox"
                  checked={selected.has(p.pattern)}
                  onChange={() => toggle(p.pattern)}
                  style={{ marginTop: 3 }}
                />
                <span style={{ minWidth: 0, flex: 1 }}>
                  <code style={{ fontSize: 12, wordBreak: "break-all" }}>{p.pattern}</code>
                  <span style={{ ...muted, fontSize: 11, marginLeft: 8 }}>
                    批准过 {p.count} 次 · {p.classes.join(" / ")}
                  </span>
                  {p.examples.length > 0 && (
                    <div style={{ ...muted, fontSize: 11, marginTop: 4 }}>
                      例：{p.examples.join("　")}
                    </div>
                  )}
                </span>
              </label>
            ))}
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 12 }}>
            <button
              type="button"
              onClick={() => void onApply()}
              disabled={!selected.size || applying}
              style={{
                fontSize: 12,
                padding: "6px 14px",
                borderRadius: "var(--radius-sm)",
                border: "1px solid var(--catfish-border)",
                background: selected.size ? "var(--catfish-accent, #2d7d7d)" : "transparent",
                color: selected.size ? "#fff" : "var(--catfish-text-muted)",
                cursor: selected.size && !applying ? "pointer" : "default",
                fontFamily: "inherit",
              }}
            >
              {applying ? "写入中…" : `加入免审批清单（已选 ${selected.size} 条）`}
            </button>
            <button type="button" onClick={() => void load()} style={linkBtn}>
              重新分析
            </button>
            {typeof data.existing_allowlist_size === "number" && (
              <span style={{ ...muted, fontSize: 11 }}>
                当前清单 {data.existing_allowlist_size} 条
              </span>
            )}
          </div>
        </>
      )}

      {result && (
        <div style={{ marginTop: 10, fontSize: 12 }}>
          {result.applied.length > 0 && (
            <div style={{ color: "var(--catfish-text)" }}>
              ✅ 已加入 {result.applied.length} 条
              {typeof result.allowlist_size === "number" &&
                `，清单现共 ${result.allowlist_size} 条`}
              ：{result.applied.map((p) => <code key={p} style={{ marginRight: 6 }}>{p}</code>)}
            </div>
          )}
          {/* 没进去的必须显示 —— 员工点了三条只进两条, 不能当没发生 */}
          {result.rejected.length > 0 && (
            <div style={{ color: "var(--catfish-hint-amber-text)", marginTop: 4 }}>
              ⚠ 这 {result.rejected.length} 条没能加入（建议列表已变化，请重新分析后再选）：
              {result.rejected.map((p) => <code key={p} style={{ marginLeft: 6 }}>{p}</code>)}
            </div>
          )}
          {!result.ok && result.applied.length === 0 && (
            <div style={{ color: "var(--catfish-hint-amber-text)" }}>
              {describeReason(result.reason)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

const linkBtn = {
  marginLeft: 8,
  fontSize: 12,
  background: "transparent",
  border: "none",
  color: "var(--catfish-text-muted)",
  textDecoration: "underline",
  cursor: "pointer",
  fontFamily: "inherit",
  padding: 0,
} as const;
