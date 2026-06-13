/** EmailTab ListItem — 抽自 EmailTab.tsx (5/20 拆分).
 *
 * 列表单条邮件渲染. urgency badge (急/中/低) + sender + subject + date.
 */

import type { EmailDigestItem, PhishingScanResult } from "../../../lib/tauri";
import { _extractSenderName, _formatShortDate } from "./helpers";


function ListItem({
  item,
  active,
  urgency,
  phishing,
  onClick,
}: {
  item: EmailDigestItem;
  active: boolean;
  urgency?: string;  // '急' / '中' / '低', undef = scheduler 还没评级
  phishing?: PhishingScanResult;  // P3.3.58 段 2B: 钓鱼扫描结果
  onClick: () => void;
}) {
  return (
    <li
      onClick={onClick}
      style={{
        padding: "10px 12px",
        borderBottom: "1px solid var(--catfish-border)",
        cursor: "pointer",
        background: active ? "var(--catfish-bg-cream)" : "transparent",
        borderLeft: active
          ? "3px solid var(--catfish-cyan)"
          : "3px solid transparent",
        fontSize: 12,
        userSelect: "none",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          gap: 6,
          marginBottom: 3,
        }}
      >
        {!item.is_read && (
          <span
            style={{
              width: 6,
              height: 6,
              borderRadius: "50%",
              background: "var(--catfish-cyan)",
              flex: "0 0 auto",
              marginTop: 4,
            }}
            aria-label="未读"
          />
        )}
        <strong
          style={{
            flex: 1,
            color: "var(--catfish-text)",
            fontWeight: item.is_read ? 400 : 600,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {_extractSenderName(item.sender)}
        </strong>
        {/* 评级 badge — 急=红 / 中=黄 / 低=灰 / 未评=空.
            5/18 BL-EMAIL-URGENCY-BADGE: 老逻辑 "中=不显" 让用户以为没评级, 实际是
            已评但被藏起来. 鸿波反馈"现在邮件没有任何优先级"就是这问题. 改 中
            也显黄色 chip, 三色齐全用户看得见. */}
        {urgency === "急" && (
          <span
            style={{
              flex: "0 0 auto",
              fontSize: 9,
              padding: "1px 5px",
              background: "rgba(239, 68, 68, 0.15)",
              color: "rgb(185, 28, 28)",
              borderRadius: 3,
              fontWeight: 600,
            }}
          >
            急
          </span>
        )}
        {urgency === "中" && (
          <span
            style={{
              flex: "0 0 auto",
              fontSize: 9,
              padding: "1px 5px",
              background: "rgba(251, 191, 36, 0.18)",
              color: "rgb(180, 130, 20)",
              borderRadius: 3,
              fontWeight: 500,
            }}
          >
            中
          </span>
        )}
        {urgency === "低" && (
          <span
            style={{
              flex: "0 0 auto",
              fontSize: 9,
              padding: "1px 5px",
              background: "transparent",
              color: "var(--catfish-text-muted)",
              opacity: 0.6,
              borderRadius: 3,
            }}
          >
            低
          </span>
        )}
        {/* P3.3.58 段 2B (6/12 鸿波): 钓鱼 ⚠️ badge — high=红 / medium=橙 / 其他不显 */}
        {phishing && phishing.highestSeverity === "high" && (
          <span
            style={{
              flex: "0 0 auto",
              fontSize: 9,
              padding: "1px 5px",
              background: "rgba(220,38,38,0.2)",
              color: "rgb(185,28,28)",
              borderRadius: 3,
              fontWeight: 700,
            }}
            title={`${phishing.flags.length} 条规则触发, LLM: ${phishing.llmVerdict ?? "未跑"}`}
          >
            ⚠ 钓鱼
          </span>
        )}
        {phishing && phishing.highestSeverity === "medium" && (
          <span
            style={{
              flex: "0 0 auto",
              fontSize: 9,
              padding: "1px 5px",
              background: "rgba(251,146,60,0.18)",
              color: "rgb(194,65,12)",
              borderRadius: 3,
              fontWeight: 600,
            }}
            title={`${phishing.flags.length} 条规则触发, LLM: ${phishing.llmVerdict ?? "未跑"}`}
          >
            ⚠ 可疑
          </span>
        )}
        <span style={{ flex: "0 0 auto", color: "var(--catfish-text-muted)", fontSize: 11 }}>
          {_formatShortDate(item.date)}
        </span>
      </div>
      <div
        style={{
          color: "var(--catfish-text-muted)",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          fontWeight: item.is_read ? 400 : 500,
        }}
      >
        {item.subject || "(无主题)"}
      </div>
      <div
        style={{
          color: "var(--catfish-text-muted)",
          opacity: 0.7,
          fontSize: 10,
          marginTop: 2,
        }}
      >
        {item.account}
      </div>
    </li>
  );
}

/** ─── 详情面板 ───────────────────────────────────────── */

export default ListItem;
