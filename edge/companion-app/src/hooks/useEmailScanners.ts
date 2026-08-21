/** 邮件评级(分诊)+钓鱼扫描的两个后台 effect (8/21 从 EmailTab.tsx 纯搬迁)。
 *
 * 搬迁原因: EmailTab 撞 800 行红线 (分诊筛选接线把它推到 814)。
 * **逻辑一行未改** —— 这两个 effect 是 8/15 烧 2470 万 token 那个永动机的
 * 修复现场, 护栏 (ratedRef 本地提交记录 / map 不进依赖 / 失败不重试) 全在
 * 注释里, 搬家时原文保留。改这里之前把下面两段长注释读完。
 */
import { useEffect, useRef } from "react";

import {
  emailClassifyNow,
  emailPhishingScanNow,
  type EmailDigestItem,
  type PhishingScanResult,
} from "../lib/tauri";
import { useEmailStore } from "../store/email";

export function useEmailScanners(opts: {
  items: EmailDigestItem[];
  urgencyMap: Record<string, string>;
  actionMap: Record<string, { action: string; deadline?: string }>;
  phishingMap: Record<string, PhishingScanResult>;
  setUrgencyMap: (m: Record<string, string>) => void;
  setPhishingMap: (m: Record<string, PhishingScanResult>) => void;
}): void {
  const { items, urgencyMap, actionMap, phishingMap, setUrgencyMap, setPhishingMap } = opts;

  // 5/18 BL-EMAIL-URGENCY-BADGE: 列表加载完后主动评级所有未评 id.
  // scheduler 只评 diff 新邮件, 启动时已有的历史邮件永远无评级 → badge 空白.
  // 这里主动 batch 评级 (LLM call 一次, 已 cache 的跳过省 token).
  // 分批 30 防 prompt 太长, 顺序 await 不并发避免烧 quota.
  //
  // ⚠ 8/15: 下面这两个 ref 是修一个**自触发循环**加的, 别把判据挪回 state.
  //
  // 原来是 `}, [items, urgencyMap])` —— effect 依赖 urgencyMap, 体内又调
  // setUrgencyMap, 每评完一批就把自己重新触发一次。而最后一行注释写的是
  // 「依赖 items.length + 第一条 id」, 跟实际依赖对不上, 说明本意就不是这样。
  //
  // 它之所以不是"多跑几轮就停", 是跟另外两处咬在一起:
  //   · 列表上限 500 (MAX_EMAIL_LIST_LIMIT / email.rs:164 clamp(1,500)),
  //     而 Rust 侧 urgency_cache 上限**只有 200**, 超了丢 100 条;
  //   · store 的 setUrgencyMap 是**整份覆盖**不是 merge。
  // 于是: 评完一批 → Rust 缓存超 200 丢一半 → 返回的 map 变小 → 覆盖掉本地
  // 更全的 map → unrated 反而变多 → 再评。**永动机**。
  //
  // 8/15 实测: 09:03–09:17 打了 527 次评级请求, 只对应 87 种不同的首封标题,
  // 最狠一封重复评了 127 次, 间隔 4–5 秒。加上钓鱼那侧同款, 当天 83 分钟
  // 烧掉 2470 万 token, 把百炼周配额从 07:54 重置烧到 09:17 见底。
  //
  // 三处一起修 (缺一条都还会漏): 这里改成 ref 记已提交的 id、Rust 缓存上限
  // 抬到 600 > 500、store 改 merge。这里是第一道 —— 判定"评没评过"用的是
  // **本地提交记录**, 不再是那份会被覆盖的 map, 所以后端返什么都不会再触发自己。
  const ratedRef = useRef<Set<string>>(new Set());
  const scannedRef = useRef<Set<string>>(new Set());

  // 这几个 ref 只是让 effect **读得到最新的 map 而不用把它写进依赖**。
  // 同步用的 effect 自己不发任何请求, 所以它重跑没有代价。
  const urgencyMapRef = useRef(urgencyMap);
  const actionMapRef = useRef(actionMap);  // 8/21 二修: 补评判据要看它
  const phishingMapRef = useRef(phishingMap);
  useEffect(() => {
    urgencyMapRef.current = urgencyMap;
  }, [urgencyMap]);
  useEffect(() => {
    actionMapRef.current = actionMap;
  }, [actionMap]);
  useEffect(() => {
    phishingMapRef.current = phishingMap;
  }, [phishingMap]);

  useEffect(() => {
    if (items.length === 0) return;
    // 8/21 二修: urgency **或** action 缺一个就要评 —— 第一版只看 urgency,
    // 336 封存量全被跳过, action badge 永远出不来 (「升级了但啥也看不到」)。
    // 防永动机: (1) ratedRef 本会话每封只提交一次 (8/15 修复, 不动);
    // (2) Rust 侧对"评过但模型没给 action"的写空串哨兵进 actionMap,
    //     所以 !actionMap[id] 在补评一轮之后就是 false, 不会反复重评。
    // 注意判据是 `actionMap[it.id] === undefined` 而不是 truthy —— 哨兵
    // {action:""} 也是"有", 用 truthy 判会把哨兵当缺失, 永动机就回来了。
    const unrated = items.filter(
      (it) =>
        (!urgencyMapRef.current[it.id] || actionMapRef.current[it.id] === undefined)
        && !ratedRef.current.has(it.id),
    );
    if (unrated.length === 0) return;
    let cancelled = false;
    (async () => {
      const BATCH = 30;
      for (let i = 0; i < unrated.length; i += BATCH) {
        if (cancelled) return;
        const batch = unrated.slice(i, i + BATCH);
        // 先记再发: 请求失败也不重试。重试的代价是每封 1 万 token, 而代价
        // 收益完全不对等 —— badge 空白一格 vs 烧穿配额。下次 loadList
        // (员工手动刷新 / 切 tab) 自然会重来。
        batch.forEach((it) => ratedRef.current.add(it.id));
        try {
          // 8/21 分诊升级: 带 body_text (snippet) —— 不带的话分诊只看得到
          // 主题+发件人, 截止日永远提不出来; 返回从裸 map 变 {urgency, actions}。
          const updated = await emailClassifyNow(batch.map((it) => ({
            id: it.id,
            subject: it.subject,
            sender: it.sender,
            account: it.account,
            date: it.date,
            is_read: it.is_read,
            body_text: it.body_text,
          })));
          if (!cancelled) {
            setUrgencyMap(updated.urgency);
            useEmailStore.getState().mergeActionMap(updated.actions);
          }
        } catch {
          // 评级失败 (gateway 挂 / token 过期) — 跳过, badge 维持空白不阻塞 UI
          return;
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // 只依赖 items —— 它是 useState 原始状态 (不是 filteredItems 那种派生值),
    // 引用只在 loadList 调 setItems 时变。urgencyMap **不能**进依赖: 体内会写它。
  }, [items, setUrgencyMap]);

  // P3.3.58 段 2B (6/12 鸿波): 钓鱼扫描. 跟 urgency 评级同款 — 已扫的跳过省 LLM,
  // 新 id 走 light scan + LLM batch. 失败静默不阻塞 UI.
  // 8/15: 跟上面的评级 effect 是**同一个 bug** —— 原来是 `}, [items, phishingMap])`,
  // 体内又调 setPhishingMap。修法一致, 理由见上面那段长注释。
  // 钓鱼复审比评级还贵 (companion-phishing-scan 每次约 4 万 token, 8/15 改前
  // 的实测均值 40,695), 所以这条一样不能重试。
  useEffect(() => {
    if (items.length === 0) return;
    const unscanned = items.filter(
      (it) => !phishingMapRef.current[it.id] && !scannedRef.current.has(it.id),
    );
    if (unscanned.length === 0) return;
    let cancelled = false;
    (async () => {
      const BATCH = 20;
      for (let i = 0; i < unscanned.length; i += BATCH) {
        if (cancelled) return;
        const batch = unscanned.slice(i, i + BATCH);
        batch.forEach((it) => scannedRef.current.add(it.id));
        try {
          const updated = await emailPhishingScanNow(batch.map((it) => ({
            id: it.id,
            subject: it.subject,
            sender: it.sender,
            account: it.account,
            date: it.date,
            is_read: it.is_read,
          })));
          if (!cancelled) setPhishingMap(updated);
        } catch {
          // 扫描失败 (gateway 挂 / 规则 panic), badge 维持空白不阻塞
          return;
        }
      }
    })();
    return () => { cancelled = true; };
    // 同上: phishingMap 不进依赖 (体内会写它), 用 phishingMapRef 读最新值。
  }, [items]);
}
