/** BL-ADVISOR-UI (5/21 Phase 7 第 6 步): 智能参谋早安主菜列表 — 顶层容器.
 *
 * 替代 Phase 6 WorkplanView (已砍). 角色:
 *   1. 拉 profile (~/.catfish/profile.json) — 没有/占位 (confidence=0) 显引导
 *   2. profile 真识别后, 并发拉 7 数据源 + emails/events/todos
 *   3. 调 fetchBriefingAdvisor → AdvisorResult
 *   4. 渲染主菜 list (ActionCard) + 已默认处理 (HandledSilently)
 *
 * 启动顺序对 5/21 鸿波"不切信息"约束: 一次拉全数据, 一次喂 LLM, 不分块.
 */

import { useEffect, useState } from "react";

import {
  fetchBriefingAdvisor,
  type AdvisorResult,
} from "../../lib/briefing_advisor";
import { ensureRecomputed, type Profile } from "../../lib/profile";
import {
  briefingContextFetch,
  calendarTodayFetch,
  emailDigestFetch,
  journalTodosFetch,
  type BriefingContext,
  type CalendarEvent,
  type EmailDigestItem,
  type JournalTodo,
} from "../../lib/tauri";
import { useChatStore } from "../../store/chat";
import { useEmailStore } from "../../store/email";

import ActionCard from "./components/ActionCard";
import HandledSilently from "./components/HandledSilently";

interface AdvisorViewProps {
  /** 父组件 (BriefingCard) 触发 refresh 时调本 props 后会 reload */
  refreshKey?: number;
}

export default function AdvisorView({ refreshKey = 0 }: AdvisorViewProps) {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [result, setResult] = useState<AdvisorResult | null>(null);
  const [phase, setPhase] = useState<"booting" | "profile_loading" | "data_loading" | "llm_running" | "done" | "no_profile" | "error">("booting");
  const [errorMsg, setErrorMsg] = useState<string>("");

  const urgencyMap = useEmailStore((s) => s.urgencyMap);
  const model = useChatStore((s) => s.model);

  useEffect(() => {
    let cancelled = false;

    // 5/22 cold start 修: 同步等 recompute 完再决定走主链路还是占位.
    // refreshKey > 0 (员工点刷新) → force=true 强制重算.
    const isManualRefresh = refreshKey > 0;

    void (async () => {
      try {
        // 1. profile — 同步等 recompute (in-flight 锁防 StrictMode 双调)
        // model 跟员工 chat 同款 (5/17 BL-INTERNAL-MODEL-FOLLOW-USER)
        setPhase("profile_loading");
        const p = await ensureRecomputed(model, isManualRefresh);
        if (cancelled) return;

        if (!p || p.confidence < 0.3) {
          // recompute 完仍低置信度 (数据稀疏 / LLM 挂) → 占位引导
          setProfile(p);
          setPhase("no_profile");
          return;
        }
        setProfile(p);

        // 2. 并发拉数据
        setPhase("data_loading");
        const [emailRes, eventsRes, todosRes, ctx] = await Promise.allSettled([
          emailDigestFetch(50),
          calendarTodayFetch(false),
          journalTodosFetch(),
          briefingContextFetch(),
        ]);
        if (cancelled) return;

        const emails = parseJsonList<EmailDigestItem>(emailRes);
        const events = parseJsonList<CalendarEvent>(eventsRes);
        const todos = parseJsonList<JournalTodo>(todosRes);
        const ctxValue =
          ctx.status === "fulfilled" ? ctx.value as BriefingContext : emptyCtx();

        // 3. 调 LLM (advisor)
        setPhase("llm_running");
        const r = await fetchBriefingAdvisor({
          profile: p,
          emails,
          events,
          todos,
          ctx: ctxValue,
          urgencyMap,
          sessionGoal: ctxValue.sessionGoal,
          model,
        });
        if (cancelled) return;

        setResult(r);
        setPhase("done");
      } catch (e) {
        if (cancelled) return;
        setErrorMsg(e instanceof Error ? e.message : String(e));
        setPhase("error");
      }
    })();

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey]);

  // ─── 渲染 ────────────────────────────────────────────────────

  if (phase === "booting" || phase === "profile_loading") {
    return <Placeholder text="正在识别员工画像..." />;
  }

  if (phase === "no_profile") {
    return (
      <NoProfileNotice
        hasProfileButZero={profile !== null && profile.confidence === 0}
      />
    );
  }

  if (phase === "data_loading" || phase === "llm_running") {
    return (
      <Placeholder
        text={
          phase === "data_loading"
            ? "正在拉取邮件 / 日历 / TODO / 历史..."
            : "鲶鱼正在综合判断 (LLM 调用 + 起草草稿)..."
        }
      />
    );
  }

  if (phase === "error") {
    return <Placeholder text={`出错了: ${errorMsg}`} error />;
  }

  // phase === "done"
  if (!result) {
    return (
      <Placeholder
        text="LLM 返空或解析失败. 数据有可能不够 (邮件/日历/TODO 全空), 或网络挂. 点刷新重试."
        error
      />
    );
  }

  return (
    <div style={{ marginTop: "var(--space-4)" }}>
      {/* 5/21 cold start 3: confidence 分层 UI 提示 */}
      {profile && <ConfidenceHint profile={profile} />}

      <div
        style={{
          fontSize: 11,
          color: "var(--catfish-text-muted)",
          marginBottom: 10,
          paddingLeft: 4,
        }}
      >
        鲶鱼参谋 · tier={result.tier}
      </div>

      {result.mainTasks.length === 0 ? (
        <Placeholder text="今天没有重要主菜 — 也许该喝杯茶, 或者整理工作计划." />
      ) : (
        result.mainTasks.map((task) => <ActionCard key={task.id} task={task} />)
      )}

      <HandledSilently items={result.handledSilently} />
    </div>
  );
}

/** 5/21 cold start 3: confidence 分层 UI 提示.
 *
 *   - < 0.3: "🪴 刚认识你, 默认按通用版渲染. 多用几天会更准."
 *   - 0.3-0.7: "📊 画像中. 已识别: tier / style / N 关键人 / N 项目."
 *   - > 0.7: 无提示, 全功能 (顶上"鲶鱼参谋 · tier=..." 一行就够)
 */
function ConfidenceHint({ profile }: { profile: Profile }) {
  const c = profile.confidence;
  if (c >= 0.7) return null;  // 高置信度无提示

  const isLow = c < 0.3;
  const bg = isLow ? "rgba(146,64,14,0.08)" : "rgba(37,99,235,0.06)";
  const border = isLow ? "#92400e40" : "#2563eb40";
  const color = isLow ? "#92400e" : "#1e3a8a";
  const icon = isLow ? "🪴" : "📊";
  const title = isLow ? "刚认识你" : "画像中";
  const body = isLow
    ? "默认按通用版 (中层) 渲染. 多用几天 catfish (写邮件 / 跟我聊几次) 会自动校准更准."
    : `已识别: ${profile.tier} / ${profile.style} / ${profile.keyPeople.length} 关键人 / ${profile.keyProjects.length} 项目. 置信度 ${(c * 100).toFixed(0)}%, 继续用会更准.`;

  return (
    <div
      style={{
        marginBottom: 12,
        padding: "8px 12px",
        background: bg,
        border: `1px solid ${border}`,
        borderRadius: "var(--radius-sm)",
        fontSize: 12,
        color,
        lineHeight: 1.55,
      }}
    >
      <strong style={{ marginRight: 6 }}>
        {icon} {title}
      </strong>
      {body}
    </div>
  );
}

// ─── 子组件 ──────────────────────────────────────────────────────

function Placeholder({ text, error = false }: { text: string; error?: boolean }) {
  return (
    <div
      style={{
        marginTop: "var(--space-4)",
        padding: "20px 18px",
        color: error ? "#dc2626" : "var(--catfish-text-muted)",
        fontSize: 13,
        fontStyle: error ? "normal" : "italic",
        textAlign: "center",
        background: "var(--catfish-bg)",
        borderRadius: "var(--radius-sm)",
        border: `1px dashed ${error ? "#dc262640" : "var(--catfish-border)"}`,
      }}
    >
      {text}
    </div>
  );
}

function NoProfileNotice({ hasProfileButZero }: { hasProfileButZero: boolean }) {
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
        color: "var(--catfish-text-muted)",
      }}
    >
      <div style={{ fontSize: 14, color: "var(--catfish-text)", marginBottom: 8 }}>
        员工画像识别中
      </div>
      <div>
        {hasProfileButZero
          ? "已生成画像占位 (Phase 7 第 1 步), 真 LLM 推断待第 4 步集成后启用."
          : "首次启动, catfish 后台正在分析员工历史数据生成画像."}
      </div>
      <div style={{ marginTop: 8, fontSize: 12, opacity: 0.85 }}>
        识别完后才会出现主菜 + 选项 + 草稿. 请稍候或刷新.
      </div>
    </div>
  );
}

// ─── helpers ─────────────────────────────────────────────────────

function parseJsonList<T>(res: PromiseSettledResult<string>): T[] {
  if (res.status !== "fulfilled") return [];
  try {
    const arr = JSON.parse(res.value);
    return Array.isArray(arr) ? (arr as T[]) : [];
  } catch {
    return [];
  }
}

function emptyCtx(): BriefingContext {
  return {
    distilledFacts: "",
    recentSessionBriefs: [],
    sessionGoal: "",
    workplan: "",
    projects: "",
    weeklyReports: [],
  };
}
