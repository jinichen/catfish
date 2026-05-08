/** BL-MM11 (5/8): skill 级 👍/👎/改 评分 按钮.
 *
 * 跟 BL-MM6 FeedbackButtons.tsx 区别:
 *   - FeedbackButtons → 给整条 assistant 消息评分 (LLM 这次回得好/差)
 *   - SkillFeedbackButtons (本组件) → 给特定 skill 调用评分 (这次 weekly-report 用得好/差)
 *
 * 触发: 一条 assistant 消息含 `catfish_run_skill` tool call → 对每个 skill_path
 * 渲染一行 👍/👎/改 按钮. 写到 ~/.catfish/skill_quality.jsonl, 用于:
 *   1. Dashboard SkillAuditCard 显示 thumbs up/down 累计
 *   2. BL-MM12 综合质量分数公式的 explicit_feedback 来源
 *   3. 员工记录 "这个 skill 哪里要改" 给 owner review
 *
 * 跟 FeedbackButtons 共存:
 *   - 一条消息**两个组件都显示** — FeedbackButtons 给消息评分, SkillFeedbackButtons 给 skill 评分
 *   - 员工可以同时打分 (整体回答 OK 但某个 skill 用错了)
 */

import { useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { useChatStore } from "../../store/chat";

interface Props {
  /** skill 路径, 例 "department/weekly-report" */
  skillPath: string;
  /** skill 调用的 unix ts (跟 skill_audit.jsonl 关联) */
  skillCallTs?: number;
  /** 流式中不显按钮 */
  hidden?: boolean;
}

type Status = "idle" | "writing-down" | "writing-edit" | "saved";

export default function SkillFeedbackButtons({
  skillPath,
  skillCallTs,
  hidden,
}: Props) {
  const [status, setStatus] = useState<Status>("idle");
  const [comment, setComment] = useState("");
  const [savedKind, setSavedKind] = useState<"thumb_up" | "thumb_down" | "edit" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const sessionId = useChatStore((s) => s.persistedSessionId) || "";

  if (hidden) return null;

  const submit = async (
    kind: "thumb_up" | "thumb_down" | "edit",
    commentArg?: string,
  ) => {
    setError(null);
    try {
      await invoke("skill_feedback_record", {
        kind,
        skillPath,
        sessionId,
        skillCallTs,
        comment: commentArg,
      });
      setSavedKind(kind);
      setStatus("saved");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  if (status === "saved") {
    const label =
      savedKind === "thumb_up"
        ? `🛠 ${skillPath} 👍 已记`
        : savedKind === "thumb_down"
        ? `🛠 ${skillPath} 👎 已记: ${comment || "无评论"}`
        : `🛠 ${skillPath} ✏️ 已记: ${comment || "无评论"}`;
    return (
      <div
        style={{
          marginTop: 4,
          fontSize: 11,
          color: "var(--catfish-text-muted)",
          fontStyle: "italic",
        }}
      >
        {label}
      </div>
    );
  }

  if (status === "writing-down" || status === "writing-edit") {
    const isEdit = status === "writing-edit";
    return (
      <div
        style={{
          marginTop: "var(--space-2)",
          display: "flex",
          gap: 6,
          alignItems: "stretch",
        }}
      >
        <input
          type="text"
          autoFocus
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              const k = isEdit ? "edit" : "thumb_down";
              void submit(k, comment.trim() || undefined);
            }
            if (e.key === "Escape") {
              setStatus("idle");
              setComment("");
            }
          }}
          placeholder={
            isEdit
              ? `${skillPath} 该怎么改? (Enter 提交 / Esc 取消)`
              : `${skillPath} 哪里不行? (Enter 提交 / Esc 取消)`
          }
          style={{
            flex: 1,
            fontSize: 12,
            padding: "4px 8px",
            border: "1px solid var(--catfish-border)",
            borderRadius: 4,
            background: "var(--catfish-bg)",
            color: "var(--catfish-text)",
            outline: "none",
          }}
        />
        <button
          type="button"
          onClick={() => void submit(isEdit ? "edit" : "thumb_down", comment.trim() || undefined)}
          style={{
            padding: "4px 10px",
            border: "1px solid var(--catfish-border)",
            borderRadius: 4,
            background: "var(--catfish-cyan-dim)",
            color: "var(--catfish-text)",
            cursor: "pointer",
            fontSize: 12,
          }}
        >
          提交
        </button>
        <button
          type="button"
          onClick={() => {
            setStatus("idle");
            setComment("");
          }}
          style={{
            padding: "4px 10px",
            border: "1px solid var(--catfish-border)",
            borderRadius: 4,
            background: "transparent",
            color: "var(--catfish-text-muted)",
            cursor: "pointer",
            fontSize: 12,
          }}
        >
          取消
        </button>
      </div>
    );
  }

  return (
    <div
      style={{
        marginTop: 2,
        display: "flex",
        gap: 4,
        alignItems: "center",
      }}
    >
      <span
        style={{
          fontSize: 10,
          color: "var(--catfish-text-muted)",
          marginRight: 4,
        }}
      >
        🛠 skill <code style={{ fontSize: 10 }}>{skillPath}</code>:
      </span>
      <SBtn title="这个 skill 用得好" onClick={() => void submit("thumb_up")}>
        👍
      </SBtn>
      <SBtn title="skill 用得不行 (可写为啥)" onClick={() => setStatus("writing-down")}>
        👎
      </SBtn>
      <SBtn title="想要不一样的 skill 输出" onClick={() => setStatus("writing-edit")}>
        ✏️ 改
      </SBtn>
      {error && (
        <span style={{ fontSize: 11, color: "var(--status-err)", marginLeft: 6 }}>
          {error}
        </span>
      )}
    </div>
  );
}

function SBtn({
  title,
  onClick,
  children,
}: {
  title: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      style={{
        padding: "1px 6px",
        fontSize: 11,
        border: "1px solid transparent",
        borderRadius: 4,
        background: "transparent",
        color: "var(--catfish-text-muted)",
        cursor: "pointer",
        opacity: 0.5,
        transition: "opacity 100ms, background 100ms",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.opacity = "1";
        e.currentTarget.style.background = "var(--catfish-bg-elevated)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.opacity = "0.5";
        e.currentTarget.style.background = "transparent";
      }}
    >
      {children}
    </button>
  );
}
