import { useEffect } from "react";

import { hideDeleted } from "../lib/emailDeleted";
import type { EmailAccountItem, EmailDigestItem } from "../lib/tauri";
import { useEmailStore } from "../store/email";

/** 10/31: 重启后的第一份列表来自磁盘缓存 (~/.catfish/email_list_cache.json, 挂载时读)。
 *
 * 读是异步的 (毫秒级, 但比首帧晚) —— 读完那一刻手里还是空的, 就把缓存铺上去;
 * 已经有列表 (实时拉回来了 / 切 tab 回来的内存缓存) 就不动。删过的照样过墓碑。 */
export function useEmailListCacheHydrate(
  empty: boolean,
  setItems: (v: EmailDigestItem[]) => void,
  setSentItems: (v: EmailDigestItem[]) => void,
  setAccounts: (v: EmailAccountItem[]) => void,
): void {
  const listCache = useEmailStore((s) => s.listCache);
  useEffect(() => {
    // store 方法 + 可选调用: 测试里 mock 的 store 没有它也不炸
    void useEmailStore.getState().hydrateListCache?.();
  }, []);
  useEffect(() => {
    if (!empty || !listCache) return;
    setItems(hideDeleted(listCache.items as EmailDigestItem[], useEmailStore.getState().deleted));
    setSentItems(listCache.sentItems as EmailDigestItem[]);
    setAccounts(listCache.accounts as EmailAccountItem[]);
  }, [empty, listCache, setItems, setSentItems, setAccounts]);
}
