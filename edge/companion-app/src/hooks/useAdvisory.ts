/** useAdvisory hook — 6/7 BL-MANIFESTO-ADVISORY-PHASE1.
 *
 * 启动时 + 每 6h polling 拉 advisory feed + 本机 state, 给 AdvisoryBanner 用.
 *
 * 跟 catfish-central-manifesto 公理 4 一致: client pull-based, server 不 push.
 */

import { useCallback, useEffect, useState } from "react";

import {
  advisoryListLocalStates,
  fetchAdvisoryFeed,
  getMatchedAdvisories,
  shouldShowBanner,
} from "../lib/advisory";
import type { Advisory, AdvisoryLocalState } from "../types/advisory";

const POLL_INTERVAL_MS = 6 * 60 * 60 * 1000; // 6h

interface UseAdvisoryResult {
  /** 当前应弹 banner 的 advisory (按 severity 排序). 空 = 无 banner. */
  banners: Advisory[];
  /** 本机所有 advisory state (用于 advisory list 页). */
  localStates: AdvisoryLocalState[];
  /** 手动触发刷新 (e.g. 员工 ack/dismiss 后 reload). */
  reload: () => Promise<void>;
}

export function useAdvisory(): UseAdvisoryResult {
  const [banners, setBanners] = useState<Advisory[]>([]);
  const [localStates, setLocalStates] = useState<AdvisoryLocalState[]>([]);

  const reload = useCallback(async () => {
    const feed = await fetchAdvisoryFeed();
    if (!feed) {
      // feed 拉失败 / 没 advisory → 不展示 banner (静默)
      setBanners([]);
      return;
    }
    const matched = await getMatchedAdvisories(feed);
    const states = await advisoryListLocalStates();
    setLocalStates(states);

    // 决定哪些该弹
    const stateByid = new Map(states.map((s) => [s.advisoryId, s]));
    const toShow = matched.filter((a) =>
      shouldShowBanner(a, stateByid.get(a.id) ?? null),
    );
    setBanners(toShow);
  }, []);

  useEffect(() => {
    void reload();
    const interval = setInterval(() => {
      void reload();
    }, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [reload]);

  return { banners, localStates, reload };
}
