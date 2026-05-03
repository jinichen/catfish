/** 当前用户信息 hook — 五一 sprint 5/2 RBAC.
 *
 * 给 Dashboard 按 role 条件渲染卡片用. 一次取, 不轮询 (role 不会运行时变).
 */

import { useEffect, useState } from "react";

import { fetchMe, type MeInfo } from "../lib/me";

export function useMe() {
  const [me, setMe] = useState<MeInfo | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const m = await fetchMe();
        if (!cancelled) {
          setMe(m);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  return { me, error };
}
