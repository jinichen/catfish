/** 小鲶秘书的统一入口 (11/09): 按 lib/secretary.ts 的登记起计时器、订群消息。App 里只挂这一个。
 *  每个能力的开关在它自己的 tick / 处理函数里看 (关着就那一轮什么都不做), 这里只负责调度和不叠轮。 */
import { useEffect } from "react";

import { onRoomEvents } from "../lib/roomEventBus";
import { isTauriRuntime } from "../lib/runtime";
import { SECRETARY, type SecretaryAbility } from "../lib/secretary";

export function startSecretary(abilities: SecretaryAbility[] = SECRETARY): () => void {
  const timers: number[] = [];
  for (const a of abilities) {
    if (!a.schedule) continue;
    const { tick, everyMs, firstMs } = a.schedule;
    let running = false;
    const run = () => {
      if (running) return;   // 上一轮还没完 (读存档 / 调模型慢) 就不叠
      running = true;
      tick()
        .catch((e) => console.warn(`[secretary:${a.id}] 这一轮没跑成:`, e))
        .finally(() => {
          running = false;
        });
    };
    if (firstMs <= 0) run();
    else timers.push(window.setTimeout(run, firstMs));
    timers.push(window.setInterval(run, everyMs));
  }
  const unsub = onRoomEvents((events) => {
    for (const a of abilities) {
      a.onRoomEvents?.(events).catch((e) => console.warn(`[secretary:${a.id}]`, e));
    }
  });
  return () => {
    for (const t of timers) {
      window.clearTimeout(t);
      window.clearInterval(t);
    }
    unsub();
  };
}

export function useSecretary(): void {
  useEffect(() => {
    if (!isTauriRuntime()) return;
    return startSecretary();
  }, []);
}
