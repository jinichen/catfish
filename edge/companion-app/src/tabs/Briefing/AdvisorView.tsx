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

import { useEffect, useRef, useState } from "react";

import {
  advisorCacheGet,
  advisorCacheSave,
  advisorConfigGet,
  advisorTaskStateGet,
  advisorTaskStatePruneOld,
  cacheAgeMinutes,
  didCrossRefreshTime,
  isCacheFresh,
  type AdvisorCache,
  type AdvisorConfig,
  type TaskChatStatus,
  type TaskStateFetch,
} from "../../lib/advisor_cache";  // P3.5.32.9 (6/18): nextRefreshAfter 不再 import — 老 RefreshInfo 函数砍, 用方挪去 components/RefreshInfo.tsx 自管
import {
  ADVISOR_TIMEOUT,
  ensureTaskChatSummariesFresh,
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

import BriefingTwoColumnView from "./components/BriefingTwoColumnView";  // P3.3.6 (6/10): 左右两栏 layout
import { DataDiagnosisCard } from "./components/DataDiagnosisCard";  // P3.4.4 (6/15): 三件套全空诊断卡, 替换老 "LLM 返空" 红字
import LoadingProgress from "./components/LoadingProgress";  // P3.5.32.7 (6/18): 细化进度展现
import type { SourceStatus } from "./diagnosis_types";

interface AdvisorViewProps {
  /** 父组件 (BriefingCard) 触发 refresh 时调本 props 后会 reload */
  refreshKey?: number;
}

export default function AdvisorView({ refreshKey = 0 }: AdvisorViewProps) {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [result, setResult] = useState<AdvisorResult | null>(null);
  // P3.5.32.9 (6/18): cacheInfo state 砍 — 老 RefreshInfo 依赖它, 现在
  // components/RefreshInfo.tsx polling 自管 advisorCacheGet, 不再需要 AdvisorView 中转.
  const [config, setConfig] = useState<AdvisorConfig | null>(null);
  const [phase, setPhase] = useState<"booting" | "profile_loading" | "data_loading" | "llm_running" | "done" | "no_profile" | "no_data" | "error" | "cache_hit" | "stale_fallback">("booting");
  const [errorMsg, setErrorMsg] = useState<string>("");
  // P3.4.4 (6/15 鸿波): 三件套各自拉取状态, no_data 阶段渲染诊断卡用.
  //   parseJsonList 老 helper 把失败原因吞成 [], 改 parseJsonListWithDiagnosis 保留 reason.
  const [sourceStatuses, setSourceStatuses] = useState<{
    emails: SourceStatus;
    events: SourceStatus;
    todos: SourceStatus;
  } | null>(null);
  /** 5/22 上游拥堵 fallback: TIMEOUT 时显的灰条提示 */
  const [staleNotice, setStaleNotice] = useState<string>("");
  // P3.5.32.7 (6/18 鸿波 catch '焦虑'): phase 切换时 reset 计时, 让 LoadingProgress
  // 显 elapsed. 三态 (profile_loading / data_loading / llm_running) 各自计时,
  // 切换时 reset 让员工看到当前阶段已等了多久, 不是从一开始累加.
  const [phaseStartedAt, setPhaseStartedAt] = useState<number>(Date.now());
  // data_loading 完后存 counts 给 llm_running 阶段展示, 让员工知道 advisor 在
  // 处理什么数据量.
  const [dataCounts, setDataCounts] = useState<{ emails: number; events: number; todos: number } | null>(null);
  // cancel 按钮 — 有 stale cache 时显, 点了走 stale fallback (跳 LLM 等待).
  const [staleCache, setStaleCache] = useState<AdvisorCache | null>(null);
  /** 5/22 鸿波: 任务状态 (key = task.title → status). 启动时从后端拉. */
  const [taskState, setTaskState] = useState<TaskStateFetch>({
    today: {},
    yesterdaySnoozed: [],
  });
  /** P3.5.207 (7/9 鸿波 catch "早安卡片跟 chat 讨论修改的待办无法同步"):
   *  chat 语义 status (LLM 从 task_chat/<uid>.jsonl 判 resolved/paused/pending),
   *  key = taskUid. 派生自 advisorCache.taskChatSummaries[uid].status.
   *  BriefingTwoColumnView 用 mergeTaskStatus(taskState, chatStatus) 合并出
   *  effective, sidebar 徽章 + action 按钮判定统一走 effective. */
  const [chatStatusByUid, setChatStatusByUid] = useState<Map<string, TaskChatStatus>>(new Map());

  // 5/22 鸿波: 启动时拉今日任务状态 + 清旧 (>7d)
  useEffect(() => {
    void advisorTaskStateGet().then(setTaskState).catch((e) => {
      console.warn("[AdvisorView] task state 拉取挂:", e);
    });
    void advisorTaskStatePruneOld().catch(() => {});  // 7d 前的旧记录清掉, 失败无所谓
  }, [refreshKey]);

  // P3.5.207 (7/9 鸿波): result 变化后, 从 advisorCache.taskChatSummaries 派生
  // chatStatusByUid, 传给 BriefingTwoColumnView 合并 taskState → effective.
  // result 更新链: setResult (5 处) → useEffect 触发 → advisorCacheGet 重读.
  // 不侵入 setResult 调用点, 只监听 result. ensureTaskChatSummariesFresh 后台
  // 完成写入 cache 后也不会自动 trigger 这里 — 但没关系, refreshKey 或下次
  // setResult 会带上. 视觉 lag 15s 内可接受.
  useEffect(() => {
    let cancelled = false;
    if (!result) {
      setChatStatusByUid(new Map());
      return;
    }
    void advisorCacheGet().then((c) => {
      if (cancelled) return;
      const m = new Map<string, TaskChatStatus>();
      const sums = c?.taskChatSummaries ?? {};
      for (const [uid, s] of Object.entries(sums)) {
        if (s?.status) m.set(uid, s.status);
      }
      setChatStatusByUid(m);
    }).catch(() => {
      // 失败降级空 map (BriefingTwoColumnView chatStatusByUid=undefined → merged 只看 taskState)
    });
    return () => { cancelled = true; };
  }, [result, refreshKey]);

  const urgencyMap = useEmailStore((s) => s.urgencyMap);
  const model = useChatStore((s) => s.model);

  // P3.3.12.1 (6/10): mount / refresh 时后台 ensure 每条 mainTask 的 task chat
  //   summary 是最新的 (jsonl size 没变跳, 变了重跑 LLM 写 cache). 跟 advisor
  //   主 LLM call 解耦 — cache 命中场景也能更新 summary. 异步不阻塞 UI.
  useEffect(() => {
    void ensureTaskChatSummariesFresh(model).catch((e) => {
      console.warn("[AdvisorView] ensure summary 异常 (不阻塞 UI):", e);
    });
  }, [refreshKey, model]);

  // 5/22 时段触发: 拉 yaml 配置 (refresh_times + cache_max_age_minutes)
  useEffect(() => {
    void advisorConfigGet().then(setConfig).catch(() => {
      // yaml 读挂了用默认值 (Rust 端 advisor_config_get 已 fallback)
    });
  }, []);

  useEffect(() => {
    let cancelled = false;

    // refreshKey > 0 = 员工点刷新, 强制重算 + 跳缓存
    const isManualRefresh = refreshKey > 0;

    void (async () => {
      try {
        // ─── 1. cache 优先 (非手动刷新时) ───
        if (!isManualRefresh && config) {
          const cached = await advisorCacheGet();
          if (cancelled) return;
          if (cached && isCacheFresh(cached, config.cacheMaxAgeMinutes)) {
            console.log("[advisor] cache 命中, 跳过 LLM 调用",
              { ageMin: cacheAgeMinutes(cached).toFixed(1) });
            setResult(cached.result);
            setPhase("cache_hit");
            // profile 也读一下让 UI 显示 (ConfidenceHint)
            const { profileGet } = await import("../../lib/profile");
            const p = await profileGet();
            if (!cancelled && p) setProfile(p);
            return;
          }
        }

        // ─── 2. profile — 同步等 recompute (in-flight 锁防 StrictMode 双调) ───
        setPhase("profile_loading");
        setPhaseStartedAt(Date.now());
        const p = await ensureRecomputed(model, isManualRefresh);
        if (cancelled) return;

        // P3.5.32.3 (6/18 鸿波 catch '55% 不能用'):
        //   老阈值 0.3 — confidence 0.3-0.5 区间也跑 advisor, 但画像质量低,
        //   LLM 推理基础不牢. 鸿波视角: 低置信度画像 + LLM 推理 = 不可靠输出.
        //   改 0.5: confidence < 0.5 不调 advisor LLM, 显 no_profile placeholder
        //   '画像还在学, 多用几天后再看 advisor'. 省 token + 防低质量输出.
        //   confidence 来源 profile.ts:173 — LLM 自评质量信号 (数据稀疏 → 0.3-0.5).
        if (!p || p.confidence < 0.5) {
          setProfile(p);
          setPhase("no_profile");
          return;
        }
        setProfile(p);

        // ─── 3. 并发拉数据 ───
        setPhase("data_loading");
        setPhaseStartedAt(Date.now());
        const [emailRes, eventsRes, todosRes, ctx] = await Promise.allSettled([
          emailDigestFetch(50),
          calendarTodayFetch(false),
          journalTodosFetch(),
          briefingContextFetch(),
        ]);
        if (cancelled) return;

        // P3.4.4 (6/15 鸿波): 用 parseJsonListWithDiagnosis 保留每个 source
        //   真实失败原因 (Tauri rejected reason / JSON parse err), 给诊断卡用.
        //   老 parseJsonList 把 rejected/无效都吞成 [], reason 全丢, calendar.rs
        //   :451 那段精彩"仅添加访问权限...必须 Cmd+Q 重启" 文案推不到 UI.
        const emailDiag = parseJsonListWithDiagnosis<EmailDigestItem>(emailRes);
        const eventsDiag = parseJsonListWithDiagnosis<CalendarEvent>(eventsRes);
        const todosDiag = parseJsonListWithDiagnosis<JournalTodo>(todosRes);
        const ctxValue =
          ctx.status === "fulfilled" ? ctx.value as BriefingContext : emptyCtx();

        const statuses = {
          emails: { ok: emailDiag.ok, count: emailDiag.items.length, reason: emailDiag.reason },
          events: { ok: eventsDiag.ok, count: eventsDiag.items.length, reason: eventsDiag.reason },
          todos:  { ok: todosDiag.ok,  count: todosDiag.items.length,  reason: todosDiag.reason  },
        };
        setSourceStatuses(statuses);

        // P3.4.4 (6/15 鸿波): 三件套全空 → 不调 advisor LLM (briefing_advisor.ts:533
        //   也有同款 short-circuit, 但走到那再返 null UI 只能渲老红字 lying 文案
        //   "LLM 返空或解析失败". 改: 这里直接 short-circuit 走 no_data, 渲诊断卡
        //   显每个 source 真状态.
        const totalCount = emailDiag.items.length + eventsDiag.items.length + todosDiag.items.length;
        if (totalCount === 0) {
          console.log("[advisor] 三件套全空, 跳 LLM, 走数据诊断卡", statuses);
          setPhase("no_data");
          return;
        }

        const emails = emailDiag.items;
        const events = eventsDiag.items;
        const todos = todosDiag.items;

        // ─── 4. 调 LLM (advisor) — 60s 客户端超时, 超时走 stale cache fallback ───
        setPhase("llm_running");
        setPhaseStartedAt(Date.now());
        // P3.5.32.7 (6/18 鸿波): 拿到 data_loading counts, 给 LoadingProgress 显.
        setDataCounts({
          emails: emails.length,
          events: events.length,
          todos: todos.length,
        });
        // 拉 stale cache 让 cancel button 有内容可显 (有 cache 才显按钮).
        void advisorCacheGet().then((c) => !cancelled && setStaleCache(c));
        const r = await fetchBriefingAdvisor({
          profile: p,
          emails,
          events,
          todos,
          ctx: ctxValue,
          urgencyMap,
          // sessionGoal 5/26 删 — hermes 0.14 原生 /goal 替代
          model,
        });
        if (cancelled) return;

        // 5/22 上游拥堵 fallback: 客户端 timeout → 拉 stale cache 撑场面 (即使过期).
        // P3.3.25 (6/11): 60s → 180s; P3.4.8 (6/15 鸿波): 180s → 300s (5min,
        //   因为 advisor 走 hermes agent loop 多轮, 实际跑完 ~2-3min, 见
        //   briefing_advisor.ts:CLIENT_TIMEOUT_MS 注释).
        if (r === ADVISOR_TIMEOUT) {
          console.warn("[advisor] 客户端 timeout, 走 stale cache fallback");
          const stale = await advisorCacheGet();
          if (cancelled) return;
          if (stale) {
            setResult(stale.result);
            setStaleNotice(
              `⚠️ 公司内网模型响应慢 (>5min), 显示上次结果. 后台仍在算, 完成会自动更新.`,
            );
            setPhase("stale_fallback");
          } else {
            setStaleNotice("");
            setErrorMsg(
              "公司内网模型响应慢 (>5min), 也没有历史缓存可显示. 等几分钟点刷新重试.",
            );
            setPhase("error");
          }
          return;
        }

        setResult(r);

        // ─── 5. 写 cache (briefing_advisor 后台也会写一份, 这里走 happy path) ───
        if (r) {
          const newCache: AdvisorCache = {
            computedAt: new Date().toISOString(),
            result: r,
            model,
          };
          await advisorCacheSave(newCache).catch((e) =>
            console.warn("[advisor] cache 写入失败 (不影响显示):", e),
          );
        }
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
  }, [refreshKey, config?.cacheMaxAgeMinutes]);

  // ─── 后台时段触发: setInterval 每分钟检查 now 跨时段就 setRefreshKey 触发刷新 ───
  const tickRef = useRef<Date>(new Date());
  useEffect(() => {
    if (!config || config.refreshTimes.length === 0) return;
    const timer = window.setInterval(() => {
      const prev = tickRef.current;
      const now = new Date();
      tickRef.current = now;
      if (didCrossRefreshTime(prev, now, config.refreshTimes)) {
        console.log("[advisor] 时段触发后台刷新", { time: now.toLocaleTimeString() });
        // 通过设置一个内部触发让 useEffect 重跑 — 但 refreshKey 是 props 不能直接改
        // 改用: 直接清缓存 + 再 mount 时会重新拉
        void (async () => {
          try {
            const { advisorCacheClear } = await import("../../lib/advisor_cache");
            await advisorCacheClear();
            // 再触发一次 useEffect: 用一个 state 切换让依赖变化
            setConfig((c) => (c ? { ...c } : c));  // shallow copy 触发 useEffect 重跑
          } catch (e) {
            console.warn("[advisor] 时段刷新失败:", e);
          }
        })();
      }
    }, 60_000);  // 每分钟检查一次
    return () => window.clearInterval(timer);
  }, [config]);

  // ─── 渲染 ────────────────────────────────────────────────────

  if (phase === "booting") {
    return <Placeholder text="准备中..." />;
  }

  // P3.5.32.7 (6/18 鸿波 catch '焦虑'): 三态用 LoadingProgress 细化展现.
  if (phase === "profile_loading") {
    return (
      <LoadingProgress
        phase="profile_loading"
        startedAt={phaseStartedAt}
        model={model}
        onCancel={staleCache ? () => {
          setResult(staleCache.result);
          setStaleNotice("已切到上次结果. 后台仍在算, 完成会自动更新.");
          setPhase("stale_fallback");
        } : undefined}
      />
    );
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
      <LoadingProgress
        phase={phase}
        startedAt={phaseStartedAt}
        model={model}
        counts={dataCounts ?? undefined}
        onCancel={staleCache ? () => {
          setResult(staleCache.result);
          setStaleNotice("已切到上次结果. 后台仍在算, 完成会自动更新.");
          setPhase("stale_fallback");
        } : undefined}
      />
    );
  }

  if (phase === "error") {
    return <Placeholder text={`出错了: ${errorMsg}`} error />;
  }

  // P3.4.4 (6/15 鸿波): 三件套全空走诊断卡, 替换老 "LLM 返空" 红字 lying 文案
  //   (LLM 一次没调, 是数据全空 short-circuit). DataDiagnosisCard 显每个 source
  //   独立状态 + 修复指引 (calendar.rs:451 错误文案推到 UI).
  if (phase === "no_data" && sourceStatuses) {
    return (
      <DataDiagnosisCard
        statuses={sourceStatuses}
        onRetry={() => {
          // 重新触发 mount effect — 跟父组件 refresh 同款语义
          setPhase("booting");
          setSourceStatuses(null);
        }}
      />
    );
  }

  // phase === "done" / "cache_hit" / "stale_fallback"
  // 这里 !result 是 LLM 真返 null (LLM 调用失败 / 解析失败), 不是数据全空 —
  // 数据全空已经在 phase === "no_data" 分支接掉了 (P3.4.4).
  if (!result) {
    return (
      <Placeholder
        text="advisor LLM 调用失败 (网络挂 / 模型解析返非预期结构). 点刷新重试."
        error
      />
    );
  }

  return (
    <div style={{ marginTop: "var(--space-4)" }}>
      {/* P3.5.32.3 (6/18 鸿波 catch): ConfidenceHint banner 砍.
       *  真因 1: '已识别: mid / 合规优先 / 8 关键人 / 5 项目' — 数据自夸, 0 actionable.
       *  真因 2: '置信度 55%, 继续用会更准' — hedge disclaimer 摆烂感.
       *  鸿波视角: banner 没意义, 不见就行.
       *  保留 ConfidenceHint 函数定义 (历史 git log 用), 这里不再调.
       *  低置信度 < 0.5 走 no_profile placeholder (上面 line 142), 不显 advisor 内容. */}

      {/* 5/22 上游拥堵 fallback: 显示 stale cache 时顶上挂提示
       *  P3.4.E.9 (6/15 鸿波): 跟 ConfidenceHint 同源 hardcode hex 问题, 用 hint-amber var.
       *  老 #a16207 深琥珀在暗模式不可读. tokens.css --catfish-hint-amber-* 双模式定义. */}
      {phase === "stale_fallback" && staleNotice && (
        <div
          style={{
            fontSize: 12,
            background: "var(--catfish-hint-amber-bg)",
            border: "1px solid var(--catfish-hint-amber-border)",
            color: "var(--catfish-hint-amber-text)",
            padding: "8px 12px",
            borderRadius: 6,
            marginBottom: 10,
          }}
        >
          {staleNotice}
        </div>
      )}

      {/* P3.5.32.9 (6/18 鸿波 catch '时间和刷新放一行'):
          RefreshInfo 独立 div 已挪到 BriefingCard header. 这里砍掉省一行垂直空间.
          AdvisorView 还保留 cacheInfo state 给 LoadingProgress / stale_fallback 用. */}

      {(() => {
        // P3.3.6 (6/10): 平铺 ActionCard → 左右两栏 layout
        // 过滤 ignored 的 (今天不再出); done/snoozed 仍在 sidebar 显示 (变灰)
        const visibleTasks = result.mainTasks.filter((t) => {
          const s = taskState.today[t.title]?.status;
          return s !== "ignored";
        });
        return (
          <BriefingTwoColumnView
            tasks={visibleTasks}
            handledItems={result.handledSilently}
            subconscious={result.subconscious ?? []}
            graveyard={result.graveyard ?? []}
            blindSpots={result.blindSpots ?? []}
            taskState={taskState}
            chatStatusByUid={chatStatusByUid}
            wasSnoozedYesterday={(title) => taskState.yesterdaySnoozed.includes(title)}
            onStatusChange={(title, newStatus) => {
              // 切状态后本地立刻反映 (不等下次 refresh)
              setTaskState((prev) => {
                const today = { ...prev.today };
                if (newStatus === null) {
                  delete today[title];
                } else {
                  today[title] = {
                    status: newStatus,
                    ts: new Date().toISOString(),
                  };
                }
                return { ...prev, today };
              });
            }}
          />
        );
      })()}
    </div>
  );
}

/** P3.5.32.9 (6/18 鸿波 catch): 老 RefreshInfo 抽到 components/RefreshInfo.tsx
 *  (BriefingCard header 调). 这里函数定义砍, import 也砍 nextRefreshAfter. */

/** 5/21 cold start 3: confidence 分层 UI 提示.
 *
 *   - < 0.3: "🪴 刚认识你, 默认按通用版渲染. 多用几天会更准."
 *   - 0.3-0.7: "📊 画像中. 已识别: tier / style / N 关键人 / N 项目."
 *   - > 0.7: 无提示, 全功能 (顶上"鲶鱼参谋 · tier=..." 一行就够)
 *
 * P3.5.32.3 (6/18 鸿波 catch): 此 component 不再调. 鸿波 catch '画像 banner
 * 没意义 + 55% 不能用'. 现在阈值改 0.5 (line 142), <0.5 走 no_profile placeholder,
 * >=0.5 直接显 advisor 内容, 不显 ConfidenceHint banner.
 * 函数定义保留 (git log 历史 / 万一未来要回炉再用).
 */
// @ts-expect-error P3.5.32.3 砍调用, 留函数定义.
function ConfidenceHint({ profile }: { profile: Profile }) {
  const c = profile.confidence;
  if (c >= 0.7) return null;  // 高置信度无提示

  const isLow = c < 0.3;
  // P3.4.E.9 (6/15 鸿波): 用 tokens.css 双模式 hint CSS var, 替换老 hardcode hex.
  //   老 #1e3a8a 深蓝 / #92400e 深棕在暗模式 (--catfish-bg #131C1F) 上看不清.
  //   tokens.css :root + @media dark 各定义一套 hint-blue / hint-amber, 暗模式自动提亮.
  const bg = isLow ? "var(--catfish-hint-amber-bg)" : "var(--catfish-hint-blue-bg)";
  const border = isLow ? "var(--catfish-hint-amber-border)" : "var(--catfish-hint-blue-border)";
  const color = isLow ? "var(--catfish-hint-amber-text)" : "var(--catfish-hint-blue-text)";
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

// P3.5.32.4 (6/18 鸿波 catch '整个画像不要显示'): hasProfileButZero prop 不再用
// (新文案 0 分支 — 'Phase 7 第 1 步占位' 这种技术细节砍掉).
function NoProfileNotice(_: { hasProfileButZero: boolean }) {
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
      {/* P3.5.32.4 (6/18 鸿波 catch '整个画像不要显示, 误导'): 去 '画像' 字眼.
          鸿波视角: '画像识别' / '画像占位' 都是后台技术细节, 暴露给员工没意义, 反而像
          AI 在炫耀. 改成中性描述, 只说 "数据还不够 / 多用几天" 让员工知道下一步. */}
      <div style={{ fontSize: 14, color: "var(--catfish-text)", marginBottom: 8 }}>
        多用几天再看
      </div>
      <div>
        catfish 还在熟悉你的工作节奏, 历史数据不够多, 暂时给不出靠谱的早安建议.
        正常用 catfish 写邮件 / 跟我聊几次, 1-2 周后早安会自动开.
      </div>
      <div style={{ marginTop: 8, fontSize: 12, opacity: 0.85 }}>
        其他 tab (工作台 / 邮件 / 知识体系) 都能正常用.
      </div>
    </div>
  );
}

// ─── helpers ─────────────────────────────────────────────────────

/** P3.4.4 (6/15 鸿波): 解析 Promise.allSettled 单个 source 结果, 保留失败原因
 *  (Tauri reject / JSON parse err / 非数组). DataDiagnosisCard 拿这条 reason
 *  匹配指引文案 (e.g. calendar.rs:451 "仅添加访问权限...必须 Cmd+Q 重启" 直接
 *  推到 UI 让员工照做).
 *
 *  替换 P3.4.4 前的 parseJsonList<T>(res) — 那版把失败 / 非数组都吞成 [],
 *  reason 全丢. 6/15 鸿波早安卡 console 看不到真错因, 排错绕一通弯路.
 */
function parseJsonListWithDiagnosis<T>(
  res: PromiseSettledResult<string>,
): { items: T[]; ok: boolean; reason?: string } {
  if (res.status === "rejected") {
    return { items: [], ok: false, reason: String(res.reason) };
  }
  try {
    const arr = JSON.parse(res.value);
    if (!Array.isArray(arr)) {
      return { items: [], ok: false, reason: `返回不是 JSON 数组: ${res.value.slice(0, 200)}` };
    }
    return { items: arr as T[], ok: true };
  } catch (e) {
    return { items: [], ok: false, reason: `JSON 解析失败: ${e}` };
  }
}

function emptyCtx(): BriefingContext {
  return {
    distilledFacts: "",
    recentSessionBriefs: [],
    // sessionGoal 5/26 删
    workplan: "",
    projects: "",
    weeklyReports: [],
    hermesMemoryRecent: "",  // P3.4.6 (6/15 鸿波)
  };
}
