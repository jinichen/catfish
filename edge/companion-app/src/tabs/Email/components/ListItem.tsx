/** EmailTab ListItem — 抽自 EmailTab.tsx (5/20 拆分).
 *
 * 列表单条邮件渲染. urgency badge (急/中/低) + sender + subject + date.
 */

import type { EmailDigestItem, PhishingScanResult, PoliticalScanResult } from "../../../lib/tauri";
import { _extractSenderName, _formatShortDate } from "./helpers";


function ListItem({
  item,
  active,
  urgency,
  phishing,
  political,
  replied,
  actionEntry,
  onClick,
}: {
  item: EmailDigestItem;
  active: boolean;
  urgency?: string;  // '急' / '中' / '低', undef = scheduler 还没评级
  phishing?: PhishingScanResult;  // P3.3.58 段 2B: 钓鱼扫描结果
  political?: PoliticalScanResult; // P3.3.53.2: 政治敏感扫描 (仅 detail 打开过的有)
  replied?: boolean;  // P3.5.204 (7/9): 已回复标志, 由 EmailTab 一次算 O(N²) map 传下来
  /** 8/21 分诊: {action:"知"|"回"|"办", deadline?:"YYYY-MM-DD"}, undef = 没分 */
  actionEntry?: { action: string; deadline?: string };
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
        {/* 8/21 分诊 badge — 办 (带截止日显 "办·9/10") / 回。
            "知"不显: 知悉即可的邮件占大多数, 给它们都贴 badge 只会稀释
            要办/要回的显著性 —— badge 的价值在于稀缺。
            deadline 只显 月/日 (年份省掉, 列表宽度金贵; hover title 有全值)。 */}
        {actionEntry?.action === "办" && (
          <span
            title={actionEntry.deadline ? `截止 ${actionEntry.deadline}` : "要办事"}
            style={{
              flex: "0 0 auto",
              fontSize: 9,
              padding: "1px 5px",
              background: "rgba(168, 85, 247, 0.15)",
              color: "rgb(126, 34, 206)",
              borderRadius: 3,
              fontWeight: 600,
            }}
          >
            办{actionEntry.deadline
              ? `·${actionEntry.deadline.slice(5).replace("-", "/")}`
              : ""}
          </span>
        )}
        {actionEntry?.action === "回" && (
          <span
            title={actionEntry.deadline ? `截止 ${actionEntry.deadline}` : "要回信"}
            style={{
              flex: "0 0 auto",
              fontSize: 9,
              padding: "1px 5px",
              background: "rgba(59, 130, 246, 0.15)",
              color: "rgb(29, 78, 216)",
              borderRadius: 3,
              fontWeight: 600,
            }}
          >
            回{actionEntry.deadline
              ? `·${actionEntry.deadline.slice(5).replace("-", "/")}`
              : ""}
          </span>
        )}
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
        {/* P3.5.197 (7/7 鸿波军规审判): marketing badge — low=灰. 营销/推广/订阅
            群发邮件, 非威胁但员工可能不想看. 用灰色 badge 提示, 不上升到橙色警告
            避免"狼来了"警报麻木. */}
        {phishing && phishing.highestSeverity === "low" && (
          <span
            style={{
              flex: "0 0 auto",
              fontSize: 9,
              padding: "1px 5px",
              background: "rgba(107,114,128,0.15)",
              color: "rgb(75,85,99)",
              borderRadius: 3,
              fontWeight: 500,
            }}
            title={`LLM 判为营销/推广邮件${phishing.llmReason ? ` — ${phishing.llmReason}` : ""}`}
          >
            📢 营销
          </span>
        )}
        {/* P3.5.204 (7/9 鸿波 catch "回复过的邮件怎么没有标志"): 已回复 badge —
            复用 P3.5.58 isReplied 算法 (EmailTab 一次算 map 传下来).
            灰绿色低调 badge, 让员工列表里一眼看到"这封已回过", 不用点开详情才发现.
            静默行为: 老邮件 / 老 Mail.app 无 message_id → EmailTab 侧 isReplied
            返 false, 这里就不显 badge. */}
        {replied && (
          <span
            style={{
              flex: "0 0 auto",
              fontSize: 9,
              padding: "1px 5px",
              background: "rgba(42, 139, 63, 0.15)",
              color: "rgb(42, 139, 63)",
              borderRadius: 3,
              fontWeight: 500,
            }}
            /* P3.5.80 (7/28): 原文是 "已回复 (P3.5.58 RFC 822 thread chain 算法命中)" —
               内部编号 + 协议名对员工没有任何意义, 只说结论. */
            title="这封邮件你已经回过了"
          >
            ↩ 已回复
          </span>
        )}
        {/* P3.3.53.2: 政治敏感 badge (仅 detail 打开过的, 引擎开 + 命中). */}
        {political && political.engineEnabled && political.highestSeverity === "high" && (
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
            title={`${political.flags.length} 条规则触发, 请咨询信安 / 党办`}
          >
            ⚠ 合规
          </span>
        )}
        {political && political.engineEnabled && political.highestSeverity === "medium" && (
          <span
            style={{
              flex: "0 0 auto",
              fontSize: 9,
              padding: "1px 5px",
              background: "rgba(234,179,8,0.18)",
              color: "rgb(133,77,14)",
              borderRadius: 3,
              fontWeight: 600,
            }}
            title={`${political.flags.length} 条规则触发, 请复核`}
          >
            ⚠ 待复核
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
