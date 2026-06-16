// P3.4.4 (6/15 鸿波): 早安卡数据全空诊断卡 — 替换老 "LLM 返空或解析失败" 红字.
//
// # 背景
//
// 老 UI (AdvisorView.tsx 老的 line 280-287): 三件套 (emails/events/todos) 全 0
// 时, advisor 走 short-circuit 返 null (briefing_advisor.ts:533 "数据全空 不调 LLM"),
// AdvisorView 看 `!result` 兜底渲染 "LLM 返空或解析失败. 数据有可能不够
// (邮件/日历/TODO 全空), 或网络挂. 点刷新重试.". 文案 lying — LLM 一次没调.
//
// 6/15 鸿波本机 case:
//   - Mail.app 主动清空了 → emails=0 (✓ 预期, 不是 bug)
//   - 日历权限给的"仅添加访问权限"档 → osascript JXA 静默返空 events=0 (⚠ 真 bug)
//   - employee_journal.md 不存在 → todos=0 (设计如此)
// 三件套全 0 → advisor short-circuit → UI 红字误导, 排错绕一通弯路.
//
// # 设计
//
// 三段式诊断卡, 每个 source 独立显:
//   - 状态: ✓ OK + 数据量 / ✗ 失败 + 真实原因
//   - 修复指引: 文本指引 + 可复制路径 (P3.4.4 第一刀不做 Tauri command 行动按钮)
//
// 失败原因尽量保留: parseJsonListWithDiagnosis 把 calendar.rs:451 那段
// "仅添加访问权限...必须 Cmd+Q 重启" 文案 + email.rs "Mail.app 没开/没权限"
// 文案直接推到 UI, 不在前端瞎写指引.

import type { SourceStatus } from "../diagnosis_types";

export interface DataDiagnosisCardProps {
  statuses: {
    emails: SourceStatus;
    events: SourceStatus;
    todos: SourceStatus;
  };
  onRetry: () => void;
}

export function DataDiagnosisCard({ statuses, onRetry }: DataDiagnosisCardProps) {
  return (
    <div
      style={{
        marginTop: "var(--space-4)",
        padding: "20px 18px",
        background: "var(--catfish-bg)",
        borderRadius: "var(--radius-sm)",
        border: "1px dashed var(--catfish-border)",
        fontSize: 13,
        lineHeight: 1.7,
      }}
    >
      <div
        style={{
          fontSize: 14,
          color: "var(--catfish-text)",
          marginBottom: 4,
          fontWeight: 500,
        }}
      >
        今天没有素材推早安
      </div>
      <div
        style={{
          fontSize: 12,
          color: "var(--catfish-text-muted)",
          marginBottom: 16,
        }}
      >
        三件套 (邮件 / 日历 / TODO) 都拉到 0 条, advisor 没素材可推. 不调 LLM (省 token).
        下面看每条数据源真状态:
      </div>

      <SourceRow
        icon="📧"
        name="邮件"
        status={statuses.emails}
        zeroHint="Mail.app 真没未读邮件 (可能你都看完了). 收新邮件后点刷新."
        failHints={[
          {
            keywords: ["没装", "没找到", "CLI"],
            fix: "catfish-email CLI 没装. 装: cd ~/person_task/catfish/edge/email-agent && bash install.sh",
          },
          {
            keywords: ["没开", "Mail.app", "权限"],
            fix: "Mail.app 没开 / 没给 catfish-email 自动化权限. 开 Mail.app + 系统设置 → 隐私 → 自动化 → 勾 Mail.",
          },
        ]}
      />

      <SourceRow
        icon="📅"
        name="日历"
        status={statuses.events}
        zeroHint="日历今天真没事件? 也可能是权限档错: 系统设置 → 隐私 → 日历 → Catfish Companion → 改 '完全日历访问权限' (常见踩坑). 改完 Cmd+Q 重启 Companion!"
        failHints={[
          {
            keywords: ["仅添加", "完全访问", "完全日历"],
            fix: "日历权限给的是'仅添加访问'档, 读取被静默拒. 系统设置 → 隐私与安全性 → 日历 → Catfish Companion → 点'选项...' → 改'完全日历访问权限'. ⚠ 改完必须 Cmd+Q 完全退出 Companion 再重开 (TCC 权限按进程启动时快照, 改完不重启用旧快照).",
          },
          {
            keywords: ["自动化", "-1743"],
            fix: "macOS 首次授权对话框被忽略. 系统设置 → 隐私 → 自动化 → 鲶鱼 Companion → 勾 Calendar.",
          },
          {
            keywords: ["超时", "timeout"],
            fix: "Calendar.app 冷启动慢, 重试一次一般快. 长期建议编译 catfish-calendar Swift binary (EventKit < 100ms).",
          },
        ]}
      />

      <SourceRow
        icon="✅"
        name="TODO"
        status={statuses.todos}
        zeroHint={
          <>
            本周待办文件{" "}
            <code style={{ background: "var(--catfish-bg-2, #f1f5f9)", padding: "1px 4px", borderRadius: 3, fontSize: 12 }}>
              ~/.catfish/current_todos.md
            </code>{" "}
            没未完成 checkbox (或不存在). P3.4.7c 完工后会每周日 00:00 自动 reset, 现在手动加:
            <pre
              style={{
                marginTop: 6,
                padding: "8px 10px",
                background: "var(--catfish-bg-2, #f1f5f9)",
                borderRadius: 4,
                fontSize: 11,
                fontFamily: "ui-monospace, monospace",
                overflow: "auto",
              }}
            >
              {`mkdir -p ~/.catfish && cat > ~/.catfish/current_todos.md <<'EOF'
## ${new Date().toISOString().slice(0, 10)} 本周待办
- [ ] 测试 advisor TODO 抽取
EOF`}
            </pre>
            或者在 chat 里说 &quot;加一个 TODO: xxx&quot;, advisor 自动写 current_todos.md. 老
            <code style={{ background: "var(--catfish-bg-2, #f1f5f9)", padding: "1px 3px", borderRadius: 3, fontSize: 11 }}>
              ~/.catfish/employee_journal.md
            </code>{" "}
            里的流水帐 - [ ] 也照样合并显示 (向后兼容).
          </>
        }
        failHints={[]}
      />

      <div style={{ marginTop: 16, textAlign: "right" }}>
        <button
          onClick={onRetry}
          style={{
            padding: "6px 14px",
            fontSize: 12,
            border: "1px solid var(--catfish-border)",
            borderRadius: 4,
            background: "var(--catfish-bg)",
            color: "var(--catfish-text)",
            cursor: "pointer",
          }}
        >
          ⟳ 刷新
        </button>
      </div>
    </div>
  );
}

// ─── SourceRow 子组件 ───────────────────────────────────────────

interface SourceRowProps {
  icon: string;
  name: string;
  status: SourceStatus;
  /** count=0 但 ok=true 时的指引 (e.g. "今天没事件, 也可能权限档错") */
  zeroHint: React.ReactNode;
  /** count=0 + ok=false 时, 按 reason 关键词匹配的诊断 */
  failHints: Array<{ keywords: string[]; fix: string }>;
}

function SourceRow({ icon, name, status, zeroHint, failHints }: SourceRowProps) {
  const matchedHint = matchFailHint(status.reason, failHints);

  return (
    <div
      style={{
        marginBottom: 14,
        paddingBottom: 14,
        borderBottom: "1px solid var(--catfish-border-light, #e5e7eb)",
      }}
    >
      <div style={{ marginBottom: 6, color: "var(--catfish-text)" }}>
        <span style={{ marginRight: 6 }}>{icon}</span>
        <strong>{name}</strong>
        <span
          style={{
            marginLeft: 8,
            fontSize: 12,
            color: status.ok ? "#16a34a" : "#dc2626",
            fontWeight: 500,
          }}
        >
          {status.ok ? `✓ OK · ${status.count} 条` : "✗ 失败"}
        </span>
      </div>

      {status.ok && status.count === 0 && (
        <div
          style={{
            fontSize: 12,
            color: "var(--catfish-text-muted)",
            paddingLeft: 24,
          }}
        >
          {zeroHint}
        </div>
      )}

      {!status.ok && (
        <>
          {status.reason && (
            <div
              style={{
                fontSize: 11,
                fontFamily: "ui-monospace, monospace",
                background: "rgba(220, 38, 38, 0.08)",
                color: "#991b1b",
                padding: "6px 10px",
                borderRadius: 4,
                marginLeft: 24,
                marginBottom: 8,
                whiteSpace: "pre-wrap",
                overflowWrap: "anywhere",
              }}
            >
              {status.reason}
            </div>
          )}
          {matchedHint && (
            <div
              style={{
                fontSize: 12,
                color: "var(--catfish-text)",
                paddingLeft: 24,
                lineHeight: 1.6,
              }}
            >
              <strong style={{ color: "#0284c7" }}>修复:</strong> {matchedHint}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function matchFailHint(
  reason: string | undefined,
  hints: Array<{ keywords: string[]; fix: string }>,
): string | null {
  if (!reason) return null;
  const lower = reason.toLowerCase();
  for (const h of hints) {
    if (h.keywords.some((k) => lower.includes(k.toLowerCase()))) {
      return h.fix;
    }
  }
  return null;
}
