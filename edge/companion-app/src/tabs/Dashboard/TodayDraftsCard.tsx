/**
 * P3.3.62 (6/13 鸿波): TodayDraftsCard — 草稿接 Mail.app Drafts 链路.
 *
 * 闭环: advisor LLM 调 catfish_draft_email_reply tool → 落
 *      ~/.catfish/outputs/<today>/reply-*.md (advisor_drafts.py)
 *   → 这卡列今天所有草稿 (reply / meeting-brief / followup)
 *   → reply 类有 [📥 放入 Mail.app 草稿箱] 按钮调 emailCreateDraft
 *     (跟邮件 tab DetailPane 同款 cmd, 两步 confirm 发送红线复用)
 *   → 员工自己审 + 改 + 发. AI 永不代发 (manifesto 公理 4).
 *
 * UI 红线:
 *   - 删除走 8s inline confirming (跟 HermesMemoryCard 同款)
 *   - "放入 Mail.app 草稿箱" 落本机 Mail.app Drafts, **不发送**, 员工自己点发
 *   - "在 Finder 打开" 走系统默认应用 (draft_open_in_editor)
 */

import { useEffect, useState, type CSSProperties } from "react";
import {
  draftDeleteMd,
  draftListToday,
  draftOpenInEditor,
  draftParseMd,
  type DraftRef,
  type ParsedDraft,
} from "../../lib/drafts";
import { emailCreateDraft } from "../../lib/tauri";

interface DraftEntry {
  ref: DraftRef;
  parsed: ParsedDraft | null;
  parseError: string | null;
}

const KIND_LABEL: Record<string, string> = {
  reply: "📧 邮件回信",
  "meeting-brief": "📋 会议汇报",
  followup: "📌 项目催办",
  unknown: "📝 草稿",
};

const KIND_COLOR: Record<string, string> = {
  reply: "#2563eb",
  "meeting-brief": "#7c3aed",
  followup: "#ea580c",
  unknown: "#6b7280",
};

export default function TodayDraftsCard() {
  const [entries, setEntries] = useState<DraftEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null);
  const [busyMail, setBusyMail] = useState<string | null>(null); // 哪条正在放草稿
  const [mailMsg, setMailMsg] = useState<{ path: string; text: string; kind: "ok" | "err" } | null>(
    null,
  );

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const refs = await draftListToday();
      // 并行 parse 所有 — N 一般 < 20, 不需要节流
      const parsed = await Promise.all(
        refs.map(async (ref) => {
          try {
            const p = await draftParseMd(ref.absPath);
            return { ref, parsed: p, parseError: null } as DraftEntry;
          } catch (e) {
            return {
              ref,
              parsed: null,
              parseError: String(e),
            } as DraftEntry;
          }
        }),
      );
      setEntries(parsed);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const handleDelete = async (absPath: string) => {
    // 第 1 次点 → 进 confirming 态, 8s 内再点真删
    if (confirmingDelete !== absPath) {
      setConfirmingDelete(absPath);
      setTimeout(() => {
        setConfirmingDelete((cur) => (cur === absPath ? null : cur));
      }, 8000);
      return;
    }
    setConfirmingDelete(null);
    try {
      await draftDeleteMd(absPath);
      // 刷新
      setEntries((prev) => prev.filter((e) => e.ref.absPath !== absPath));
    } catch (e) {
      setError(`删除失败: ${e}`);
    }
  };

  const handleSendToMail = async (entry: DraftEntry) => {
    if (!entry.parsed || entry.parsed.kind !== "reply") return;
    const p = entry.parsed;
    if (!p.recipient || !p.subject) {
      setMailMsg({
        path: entry.ref.absPath,
        text: "缺收件人 / 主题, 无法放入 Mail.app 草稿箱",
        kind: "err",
      });
      return;
    }
    setBusyMail(entry.ref.absPath);
    setMailMsg(null);
    try {
      await emailCreateDraft({
        to: p.recipient,
        subject: p.subject,
        body: p.body,
        inReplyTo: p.threadId ?? undefined,
      });
      setMailMsg({
        path: entry.ref.absPath,
        text:
          "✅ 已放入 Mail.app 草稿箱, Mail.app 已切到前台 + 草稿窗口弹出. " +
          "你在草稿窗口审阅 + 改, 按 ⌘+Shift+D 发送 (catfish 不替你按 — 红线).",
        kind: "ok",
      });
    } catch (e) {
      setMailMsg({
        path: entry.ref.absPath,
        text: `放入草稿箱失败: ${e}`,
        kind: "err",
      });
    } finally {
      setBusyMail(null);
    }
  };

  const handleOpenInFinder = async (absPath: string) => {
    try {
      await draftOpenInEditor(absPath);
    } catch (e) {
      setError(`打开失败: ${e}`);
    }
  };

  if (loading) {
    return (
      <div style={{ padding: 16, color: "#6b7280" }}>正在读今天的草稿…</div>
    );
  }

  return (
    <div style={{ padding: 16 }}>
      <div style={{ marginBottom: 12 }}>
        <h3 style={{ margin: "0 0 4px 0", fontSize: 16 }}>🤖 今日 AI 草稿</h3>
        <p style={{ margin: 0, fontSize: 12, color: "#6b7280" }}>
          鲶鱼在跟你聊的过程里, 调起草工具落到 ~/.catfish/outputs/&lt;今天&gt;/.
          员工本机存, 中央 0 字节. 邮件回信类可一键放入 Mail.app 草稿箱 —
          Mail.app 自动切前台, 草稿窗口直接弹出, 你审阅 + 改 + 按
          ⌘+Shift+D 发送 (<b>catfish 不替你按</b>, 红线).
        </p>
      </div>

      <div style={{ marginBottom: 12 }}>
        <button
          onClick={() => void load()}
          style={{
            padding: "4px 10px",
            fontSize: 12,
            border: "1px solid #d1d5db",
            background: "#fff",
            borderRadius: 6,
            cursor: "pointer",
          }}
        >
          🔄 刷新
        </button>
      </div>

      {error && (
        <div
          style={{
            padding: 8,
            marginBottom: 12,
            background: "#fef2f2",
            border: "1px solid #fecaca",
            borderRadius: 6,
            color: "#991b1b",
            fontSize: 13,
          }}
        >
          {error}
        </div>
      )}

      {entries.length === 0 && (
        <div
          style={{
            padding: 24,
            textAlign: "center",
            color: "#6b7280",
            fontSize: 13,
            background: "#f9fafb",
            borderRadius: 8,
          }}
        >
          今天还没有草稿. 在 chat 里让小鲶帮你起一份邮件回信 / 会议汇报 / 项目催办,
          就会出现在这里.
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {entries.map((e) => {
          const p = e.parsed;
          const kind = p?.kind ?? "unknown";
          const isExpanded = expanded === e.ref.absPath;
          const isConfirming = confirmingDelete === e.ref.absPath;
          const isBusyMail = busyMail === e.ref.absPath;
          const msg = mailMsg && mailMsg.path === e.ref.absPath ? mailMsg : null;
          const canSendToMail =
            kind === "reply" && !!p?.recipient && !!p?.subject;

          return (
            <div
              key={e.ref.absPath}
              style={{
                border: "1px solid #e5e7eb",
                borderRadius: 8,
                background: "#fff",
                padding: 12,
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  flexWrap: "wrap",
                  marginBottom: 8,
                }}
              >
                <span
                  style={{
                    padding: "2px 8px",
                    fontSize: 12,
                    background: KIND_COLOR[kind] ?? "#6b7280",
                    color: "#fff",
                    borderRadius: 12,
                    fontWeight: 500,
                  }}
                >
                  {KIND_LABEL[kind] ?? kind}
                </span>
                {p?.tone && (
                  <span
                    style={{
                      padding: "2px 6px",
                      fontSize: 11,
                      background: "#f3f4f6",
                      color: "#374151",
                      borderRadius: 4,
                    }}
                  >
                    {p.tone}
                  </span>
                )}
                <span style={{ fontSize: 12, color: "#6b7280" }}>
                  {formatTime(e.ref.modifiedAt)}
                </span>
              </div>

              {p && (
                <div style={{ fontSize: 13, color: "#374151", marginBottom: 8 }}>
                  {p.recipient && (
                    <div>
                      <b>收件人</b>: {p.recipient}
                    </div>
                  )}
                  {p.subject && (
                    <div>
                      <b>主题</b>: {p.subject}
                    </div>
                  )}
                  {p.eventTitle && (
                    <div>
                      <b>会议</b>: {p.eventTitle}
                    </div>
                  )}
                  {p.project && (
                    <div>
                      <b>项目</b>: {p.project}
                    </div>
                  )}
                </div>
              )}

              {e.parseError && (
                <div style={{ fontSize: 12, color: "#dc2626", marginBottom: 8 }}>
                  解析失败: {e.parseError}
                </div>
              )}

              {p && p.complianceNotes.length > 0 && (
                <div
                  style={{
                    padding: 8,
                    marginBottom: 8,
                    background: "#fef3c7",
                    border: "1px solid #fde68a",
                    borderRadius: 6,
                    fontSize: 12,
                  }}
                >
                  <div style={{ fontWeight: 600, marginBottom: 4 }}>
                    ⚠️ 合规提示
                  </div>
                  {p.complianceNotes.map((n, i) => (
                    <div key={i}>• {n}</div>
                  ))}
                </div>
              )}

              {p && p.uncertainPoints.length > 0 && (
                <div
                  style={{
                    padding: 8,
                    marginBottom: 8,
                    background: "#fef3c7",
                    border: "1px solid #fde68a",
                    borderRadius: 6,
                    fontSize: 12,
                  }}
                >
                  <div style={{ fontWeight: 600, marginBottom: 4 }}>
                    ⚠️ 待你确认的内容点
                  </div>
                  {p.uncertainPoints.map((n, i) => (
                    <div key={i}>• {n}</div>
                  ))}
                </div>
              )}

              {isExpanded && p && (
                <pre
                  style={{
                    margin: "8px 0",
                    padding: 10,
                    background: "#f9fafb",
                    border: "1px solid #e5e7eb",
                    borderRadius: 6,
                    fontSize: 12,
                    whiteSpace: "pre-wrap",
                    fontFamily:
                      "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
                    maxHeight: 400,
                    overflowY: "auto",
                    color: "#1f2937",
                  }}
                >
                  {p.body}
                </pre>
              )}

              {msg && (
                <div
                  style={{
                    padding: 8,
                    marginBottom: 8,
                    background: msg.kind === "ok" ? "#ecfdf5" : "#fef2f2",
                    border: `1px solid ${msg.kind === "ok" ? "#bbf7d0" : "#fecaca"}`,
                    borderRadius: 6,
                    color: msg.kind === "ok" ? "#065f46" : "#991b1b",
                    fontSize: 12,
                  }}
                >
                  {msg.text}
                </div>
              )}

              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                <button
                  onClick={() =>
                    setExpanded((cur) => (cur === e.ref.absPath ? null : e.ref.absPath))
                  }
                  style={btnStyle(false)}
                >
                  {isExpanded ? "收起" : "👁 看内容"}
                </button>

                {canSendToMail && (
                  <button
                    onClick={() => void handleSendToMail(e)}
                    disabled={isBusyMail}
                    style={{
                      ...btnStyle(false),
                      background: "#dbeafe",
                      borderColor: "#93c5fd",
                      color: "#1e40af",
                      fontWeight: 500,
                      opacity: isBusyMail ? 0.5 : 1,
                    }}
                  >
                    {isBusyMail ? "放中…" : "📥 放入 Mail.app 草稿箱"}
                  </button>
                )}

                <button
                  onClick={() => void handleOpenInFinder(e.ref.absPath)}
                  style={btnStyle(false)}
                >
                  📂 默认应用打开
                </button>

                <button
                  onClick={() => void handleDelete(e.ref.absPath)}
                  style={btnStyle(isConfirming)}
                >
                  {isConfirming ? "✓ 确认删除" : "🗑 删除"}
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function btnStyle(danger: boolean): CSSProperties {
  return {
    padding: "4px 10px",
    fontSize: 12,
    border: `1px solid ${danger ? "#fca5a5" : "#d1d5db"}`,
    background: danger ? "#fee2e2" : "#fff",
    color: danger ? "#991b1b" : "#374151",
    borderRadius: 6,
    cursor: "pointer",
    fontWeight: danger ? 500 : 400,
  };
}

function formatTime(iso: string): string {
  try {
    const d = new Date(iso);
    const hh = String(d.getHours()).padStart(2, "0");
    const mm = String(d.getMinutes()).padStart(2, "0");
    return `${hh}:${mm}`;
  } catch {
    return iso;
  }
}
