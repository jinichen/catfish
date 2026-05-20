/** 早安播报 — 邮件详情区 (按急/中/低分组 + 单条可展开) — 抽自 BriefingCard.tsx (5/20 拆分).
 *
 * v2 (5/20 BL-COMPANION-BRIEFING-V2): 单条邮件点击 toggle 展开, lazy 调
 * emailReadMessage 拉 body snippet (200 字截), bodyCache 防二次拉.
 *
 * 急的全显, 中/低/未评级 各取前 3 行 (省空间, 想看全去 Email tab).
 */

import { useState } from "react";

import { emailReadMessage, type EmailDigestItem } from "../../../lib/tauri";
import { extractSenderName } from "./helpers";

interface EmailGroupProps {
  title: string;
  color: string;
  items: EmailDigestItem[];
  onGoEmailTab: () => void;
  limit?: number;
}

function EmailGroup({ title, color, items, onGoEmailTab, limit }: EmailGroupProps) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  // 已拉 body 的 cache (id → snippet). 第二次点同条不再调 emailReadMessage
  const [bodyCache, setBodyCache] = useState<Record<string, string | null>>({});
  const [loadingIds, setLoadingIds] = useState<Set<string>>(new Set());

  if (items.length === 0) return null;
  const shown = limit ? items.slice(0, limit) : items;
  const hidden = items.length - shown.length;

  const toggle = (id: string) => {
    const next = new Set(expanded);
    if (next.has(id)) {
      next.delete(id);
    } else {
      next.add(id);
      if (!(id in bodyCache) && !loadingIds.has(id)) {
        const nextLoading = new Set(loadingIds);
        nextLoading.add(id);
        setLoadingIds(nextLoading);
        emailReadMessage(id)
          .then((raw) => {
            try {
              const obj = JSON.parse(raw) as { body_text?: string; body_html?: string };
              const body = (obj.body_text || obj.body_html || "").trim();
              const snippet = body.length > 200 ? body.slice(0, 200) + "…" : body;
              setBodyCache((m) => ({ ...m, [id]: snippet || "(空正文)" }));
            } catch {
              setBodyCache((m) => ({ ...m, [id]: "(正文解析失败)" }));
            }
          })
          .catch((e) => {
            setBodyCache((m) => ({
              ...m,
              [id]: `(拉取失败: ${e instanceof Error ? e.message.slice(0, 80) : String(e)})`,
            }));
          })
          .finally(() => {
            setLoadingIds((s) => {
              const n = new Set(s);
              n.delete(id);
              return n;
            });
          });
      }
    }
    setExpanded(next);
  };

  return (
    <div style={{ marginTop: 6 }}>
      <div style={{ fontSize: 11, color: "var(--catfish-text-muted)", marginBottom: 4 }}>
        {title} · {items.length} 封
      </div>
      <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: 3 }}>
        {shown.map((m) => {
          const isOpen = expanded.has(m.id);
          const loading = loadingIds.has(m.id);
          const body = bodyCache[m.id];
          return (
            <li
              key={m.id}
              style={{ borderLeft: `2px solid ${color}`, fontSize: 11 }}
            >
              <div
                onClick={() => toggle(m.id)}
                style={{
                  display: "flex",
                  gap: 6,
                  alignItems: "baseline",
                  padding: "3px 6px",
                  cursor: "pointer",
                }}
                title={isOpen ? "点击收起" : "点击展开正文"}
              >
                <span style={{ color: "var(--catfish-text-muted)", fontSize: 9, minWidth: 10 }}>
                  {isOpen ? "▼" : "▶"}
                </span>
                <span
                  style={{
                    color: "var(--catfish-text-muted)",
                    minWidth: 80,
                    maxWidth: 100,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {extractSenderName(m.sender)}
                </span>
                <span
                  style={{
                    flex: 1,
                    color: "var(--catfish-text)",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {m.subject || "(无主题)"}
                </span>
              </div>
              {isOpen && (
                <div
                  style={{
                    padding: "6px 10px 8px 24px",
                    background: "var(--catfish-bg)",
                    fontSize: 10,
                    color: "var(--catfish-text-muted)",
                    lineHeight: 1.5,
                  }}
                >
                  <div style={{ marginBottom: 4, wordBreak: "break-all" }}>📧 {m.sender}</div>
                  {loading && <div style={{ opacity: 0.7 }}>🤔 拉正文…</div>}
                  {!loading && body !== undefined && (
                    <div style={{ whiteSpace: "pre-wrap", color: "var(--catfish-text)" }}>{body}</div>
                  )}
                  <div style={{ marginTop: 6 }}>
                    <a
                      onClick={(e) => {
                        e.stopPropagation();
                        onGoEmailTab();
                      }}
                      style={{ color: "var(--catfish-cyan)", cursor: "pointer", textDecoration: "underline" }}
                    >
                      📧 在 邮件 tab 看完整
                    </a>
                  </div>
                </div>
              )}
            </li>
          );
        })}
        {hidden > 0 && (
          <li style={{ fontSize: 10, color: "var(--catfish-text-muted)", paddingLeft: 8 }}>
            … 还有 {hidden} 封,{" "}
            <a
              onClick={onGoEmailTab}
              style={{ color: "var(--catfish-cyan)", cursor: "pointer", textDecoration: "underline" }}
            >
              📧 邮件 tab 查看
            </a>
          </li>
        )}
      </ul>
    </div>
  );
}

export interface EmailsDetailSectionProps {
  emails: EmailDigestItem[];
  urgencyMap: Record<string, string>;
  onGoEmailTab: () => void;
}

export default function EmailsDetailSection({
  emails,
  urgencyMap,
  onGoEmailTab,
}: EmailsDetailSectionProps) {
  const groups: Record<"urgent" | "medium" | "low" | "unrated", EmailDigestItem[]> = {
    urgent: [],
    medium: [],
    low: [],
    unrated: [],
  };
  for (const m of emails) {
    const u = urgencyMap[m.id];
    if (u === "急" || u?.toLowerCase() === "urgent") groups.urgent.push(m);
    else if (u === "低" || u?.toLowerCase() === "low") groups.low.push(m);
    else if (u === "中" || u?.toLowerCase() === "medium") groups.medium.push(m);
    else groups.unrated.push(m);
  }

  return (
    <details
      open
      style={{
        marginTop: "var(--space-3)",
        fontSize: 12,
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-sm)",
        padding: "8px 12px",
      }}
    >
      <summary
        style={{
          cursor: "pointer",
          fontWeight: 500,
          color: "var(--catfish-text)",
          marginBottom: 6,
        }}
      >
        📬 邮件详情 · {emails.length} 封
        {groups.urgent.length > 0 && (
          <span style={{ marginLeft: 6, fontSize: 11, color: "#dc2626", fontWeight: 400 }}>
            (🔴 {groups.urgent.length} 急)
          </span>
        )}
        <span style={{ marginLeft: 6, fontSize: 10, color: "var(--catfish-text-muted)", opacity: 0.6, fontWeight: 400 }}>
          (点条目展开正文)
        </span>
      </summary>
      <EmailGroup title="🔴 急" color="#dc2626" items={groups.urgent} onGoEmailTab={onGoEmailTab} />
      <EmailGroup title="🟡 中" color="#ca8a04" items={groups.medium} onGoEmailTab={onGoEmailTab} limit={3} />
      <EmailGroup title="🔵 低" color="#0284c7" items={groups.low} onGoEmailTab={onGoEmailTab} limit={3} />
      <EmailGroup
        title="⚪ 未评级"
        color="var(--catfish-text-muted)"
        items={groups.unrated}
        onGoEmailTab={onGoEmailTab}
        limit={3}
      />
    </details>
  );
}
