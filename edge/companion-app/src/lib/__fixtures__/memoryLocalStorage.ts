/** 测试用内存 localStorage (11/07, 跟 taskDoneStore.test.ts / me.test.ts 同款)。
 *
 * Node 22 自带一个实验性 localStorage 全局, 没 --localstorage-file 时只是个空壳
 * (clear/getItem 都不是函数), 还会盖住 jsdom 的。直接替掉最省事。 */
export function installMemoryLocalStorage(): Storage {
  const store = new Map<string, string>();
  const ls: Storage = {
    get length() {
      return store.size;
    },
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => {
      store.set(k, String(v));
    },
    removeItem: (k: string) => {
      store.delete(k);
    },
    clear: () => store.clear(),
    key: (i: number) => [...store.keys()][i] ?? null,
  };
  Object.defineProperty(globalThis, "localStorage", { value: ls, configurable: true, writable: true });
  if (typeof window !== "undefined" && window !== globalThis) {
    Object.defineProperty(window, "localStorage", { value: ls, configurable: true, writable: true });
  }
  return ls;
}
