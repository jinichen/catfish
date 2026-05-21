/** BL-ADVISOR-UI (5/21 Phase 7 第 6 步): 早安"已默认处理" 折叠区.
 *
 * 设计稿 §7.3: 底部小字"📊 catfish 处理记录: 自动分类 X 邮件 / Y 日历事件"
 *   - 注意: 即便是"默认处理", 也只是分类 + 标志, 不真发任何东西
 *   - 邮件归档 = Mail.app 内部标 label, 不删
 *   - 会议接受 = 日历内标 tentative, 不是 accepted
 */

import type { HandledSilentlyItem } from "../../../lib/briefing_advisor";

interface HandledSilentlyProps {
  items: HandledSilentlyItem[];
}

export default function HandledSilently({ items }: HandledSilentlyProps) {
  if (items.length === 0) return null;

  const summary = items
    .map((it) => `${typeLabel(it.type)} ${it.count} 件 (${it.category})`)
    .join(" · ");

  return (
    <details
      style={{
        marginTop: 12,
        fontSize: 11,
        color: "var(--catfish-text-muted)",
        opacity: 0.85,
      }}
    >
      <summary
        style={{
          cursor: "pointer",
          padding: "4px 0",
          listStyle: "none",
        }}
      >
        📊 catfish 处理记录: {summary} (点开看明细)
      </summary>
      <div style={{ marginTop: 6, paddingLeft: 16 }}>
        {items.map((it, i) => (
          <div key={i} style={{ marginBottom: 4 }}>
            <strong>{typeLabel(it.type)}</strong> · {it.count} 件 · {it.category}
          </div>
        ))}
        <div style={{ marginTop: 6, fontSize: 10, opacity: 0.7 }}>
          注: catfish 仅做分类标志, 不替员工真发任何东西. 邮件归档 = 内部 label,
          会议接受 = tentative. 想撤回去对应 tab 操作.
        </div>
      </div>
    </details>
  );
}

function typeLabel(type: string): string {
  switch (type) {
    case "email_archive":
      return "邮件归档";
    case "calendar_accept":
      return "日历 tentative";
    case "todo_dedup":
      return "TODO 去重";
    default:
      return type;
  }
}
