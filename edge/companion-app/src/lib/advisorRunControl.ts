/** 单一 advisor in-flight 的真实取消控制。
 *
 * UI 切到旧缓存不等于取消网络请求；这里把“停止”接到 Rust HTTP proxy 的
 * request id。finish 带 generation，旧任务结束时不会误清新任务。
 */
let active: { generation: number; controller: AbortController } | null = null;
let nextGeneration = 1;

export function beginAdvisorRun(): {
  signal: AbortSignal;
  finish: () => void;
} {
  const generation = nextGeneration++;
  const controller = new AbortController();
  active = { generation, controller };
  return {
    signal: controller.signal,
    finish: () => {
      if (active?.generation === generation) active = null;
    },
  };
}

export function cancelBriefingAdvisor(): boolean {
  if (!active || active.controller.signal.aborted) return false;
  active.controller.abort();
  return true;
}

export function advisorRunIsActive(): boolean {
  return Boolean(active && !active.controller.signal.aborted);
}

export function __resetAdvisorRunForTest(): void {
  active?.controller.abort();
  active = null;
  nextGeneration = 1;
}
