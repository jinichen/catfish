import type { ChatTransport } from "./chat";

/**
 * 一个 send 内 streamChat 可能因断线或上游错误递归重试。传输通道只以第一次
 * 真正解析出的结果为准，避免 retry 再次触发本地持久化。
 */
export function createFirstTransportHandler(
  onFirst: (transport: ChatTransport) => void,
): (transport: ChatTransport) => boolean {
  let resolved = false;
  return (transport) => {
    if (resolved) return false;
    resolved = true;
    onFirst(transport);
    return true;
  };
}

/** Hermes 会写入共享 state.db；只有直连中央 gateway 时才由 Companion 补写。 */
export function shouldPersistUserLocally(transport: ChatTransport): boolean {
  return transport === "gateway";
}
