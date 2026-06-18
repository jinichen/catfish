/** P3.5.32 Phase 10 (6/18 鸿波 OpenWiki 借鉴) — 3 维 self-aware reflection cards.
 *
 * # 真**鸿波诉求 + audit reasoning**
 *
 * OpenWiki Insight Reports 7 dim: At a Glance + Subconscious + Graveyard + Blind Spots
 * + Hot Topics + Heatmap + Action Items.
 *
 * catfish Briefing 已有 4 dim:
 *   - At a Glance (BriefingTwoColumnView 主菜)
 *   - Action Items (ActionCard mainTasks)
 *   - Hot Topics (recent_session_briefs)
 *   - Events Heatmap (EventsDetail 7 天日历)
 *
 * 缺 3 维 真**self-aware reflection 真**周维度** — 真**本文件 ship**.
 *
 * # 真**3 个 cards**
 *
 *   - **SubconsciousCard** (无意识高频): 问真**多** 但 没 deep-dive. 点 reflectPrompt → chat
 *   - **GraveyardCard** (墓地): 装但 0 回顾. 真**纯诊断**, 0 reflectPrompt (让员工主动决定)
 *   - **BlindSpotsCard** (盲点): 标重要但 0 action. 点 reflectPrompt → chat
 *
 * # 真**UI 模式**
 *
 * 跟 HandledSilently 同款 `<details>` 折叠. 真**0 item 时 早返**.
 *
 * Card 0 item 时 component 返 null → BriefingTwoColumnView 真**0 渲染空 card**.
 */

import type {
  BlindSpotItem,
  GraveyardItem,
  SubconsciousItem,
} from "../../../lib/briefing_advisor";

interface SubconsciousProps {
  items: SubconsciousItem[];
  onReflect: (prompt: string) => void;
}

export function SubconsciousCard({ items, onReflect }: SubconsciousProps) {
  if (items.length === 0) return null;
  return (
    <details
      style={{
        marginTop: 12,
        fontSize: 12,
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-sm)",
        background: "var(--catfish-bg-elevated)",
      }}
    >
      <summary
        style={{
          cursor: "pointer",
          padding: "8px 12px",
          fontWeight: 500,
          color: "var(--catfish-text)",
        }}
      >
        🌊 反复出现 ({items.length}) — 多次咨询但没深入**
      </summary>
      <div style={{ padding: "4px 12px 12px", paddingLeft: 16 }}>
        {items.map((item, i) => (
          <div
            key={i}
            style={{
              marginBottom: 10,
              paddingBottom: 8,
              borderBottom:
                i < items.length - 1
                  ? "1px dashed var(--catfish-border)"
                  : "none",
            }}
          >
            <div style={{ fontWeight: 500, marginBottom: 3 }}>
              {item.topic}{" "}
              <span
                style={{
                  fontSize: 11,
                  color: "var(--catfish-text-muted)",
                  fontWeight: 400,
                }}
              >
                · 问 {item.count} 次
              </span>
            </div>
            <div
              style={{
                fontSize: 11,
                color: "var(--catfish-text-muted)",
                marginBottom: 6,
              }}
            >
              {item.evidence}
            </div>
            {item.reflectPrompt && (
              <button
                type="button"
                onClick={() => onReflect(item.reflectPrompt)}
                style={{
                  fontSize: 11,
                  padding: "3px 8px",
                  background: "transparent",
                  border: "1px solid var(--catfish-border)",
                  borderRadius: "var(--radius-sm)",
                  color: "var(--catfish-accent, #4a90e2)",
                  cursor: "pointer",
                }}
              >
                💬 {item.reflectPrompt}
              </button>
            )}
          </div>
        ))}
      </div>
    </details>
  );
}

interface GraveyardProps {
  items: GraveyardItem[];
}

export function GraveyardCard({ items }: GraveyardProps) {
  if (items.length === 0) return null;
  return (
    <details
      style={{
        marginTop: 12,
        fontSize: 12,
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-sm)",
        background: "var(--catfish-bg-elevated)",
      }}
    >
      <summary
        style={{
          cursor: "pointer",
          padding: "8px 12px",
          fontWeight: 500,
          color: "var(--catfish-text)",
        }}
      >
        📦 久未使用 ({items.length}) — 装了但很少打开
      </summary>
      <div style={{ padding: "4px 12px 12px", paddingLeft: 16 }}>
        {items.map((item, i) => (
          <div
            key={i}
            style={{
              marginBottom: 8,
              paddingBottom: 6,
              borderBottom:
                i < items.length - 1
                  ? "1px dashed var(--catfish-border)"
                  : "none",
            }}
          >
            <div style={{ fontWeight: 500, marginBottom: 3 }}>
              {item.name}{" "}
              <span
                style={{
                  fontSize: 11,
                  color: "var(--catfish-text-muted)",
                  fontWeight: 400,
                }}
              >
                · {item.lastSeen}
              </span>
            </div>
            <div
              style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}
            >
              {item.evidence}
            </div>
          </div>
        ))}
        <div
          style={{
            marginTop: 6,
            fontSize: 10,
            color: "var(--catfish-text-muted)",
            opacity: 0.7,
            fontStyle: "italic",
          }}
        >
          注: catfish 不删. 想清理去对应 tab 自己决定.
        </div>
      </div>
    </details>
  );
}

interface BlindSpotsProps {
  items: BlindSpotItem[];
  onReflect: (prompt: string) => void;
}

export function BlindSpotsCard({ items, onReflect }: BlindSpotsProps) {
  if (items.length === 0) return null;
  return (
    <details
      style={{
        marginTop: 12,
        fontSize: 12,
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-sm)",
        background: "var(--catfish-bg-elevated)",
      }}
    >
      <summary
        style={{
          cursor: "pointer",
          padding: "8px 12px",
          fontWeight: 500,
          color: "var(--catfish-text)",
        }}
      >
        ⚠️ 需要关注 ({items.length}) — 标重要但还没跟进
      </summary>
      <div style={{ padding: "4px 12px 12px", paddingLeft: 16 }}>
        {items.map((item, i) => (
          <div
            key={i}
            style={{
              marginBottom: 10,
              paddingBottom: 8,
              borderBottom:
                i < items.length - 1
                  ? "1px dashed var(--catfish-border)"
                  : "none",
            }}
          >
            <div style={{ fontWeight: 500, marginBottom: 3 }}>
              {item.topic}{" "}
              <span
                style={{
                  fontSize: 11,
                  color: "var(--catfish-text-muted)",
                  fontWeight: 400,
                }}
              >
                · {item.signal}
              </span>
            </div>
            <div
              style={{
                fontSize: 11,
                color: "var(--catfish-text-muted)",
                marginBottom: 6,
              }}
            >
              {item.evidence}
            </div>
            {item.reflectPrompt && (
              <button
                type="button"
                onClick={() => onReflect(item.reflectPrompt)}
                style={{
                  fontSize: 11,
                  padding: "3px 8px",
                  background: "transparent",
                  border: "1px solid var(--catfish-border)",
                  borderRadius: "var(--radius-sm)",
                  color: "var(--catfish-accent, #4a90e2)",
                  cursor: "pointer",
                }}
              >
                💬 {item.reflectPrompt}
              </button>
            )}
          </div>
        ))}
      </div>
    </details>
  );
}
