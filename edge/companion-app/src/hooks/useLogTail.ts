/** 订阅 Tauri 后端发的 log 事件流（Rust 端 emit "log:<service>"）。 */

import { useEffect, useState } from "react";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { tailLogs } from "../lib/tauri";

interface LogLine {
  service: string;
  line: string;
  ts: string;
}

export function useLogTail(service: string, maxLines = 500) {
  const [lines, setLines] = useState<LogLine[]>([]);

  useEffect(() => {
    let unlisten: UnlistenFn | undefined;
    let cancelled = false;

    (async () => {
      try {
        await tailLogs(service);
      } catch {
        // TODO: 命令未实现时静默
      }
      const fn = await listen<LogLine>(`log:${service}`, (e) => {
        if (cancelled) return;
        setLines((prev) => {
          const next = [...prev, e.payload];
          return next.length > maxLines ? next.slice(-maxLines) : next;
        });
      });
      if (cancelled) fn();
      else unlisten = fn;
    })();

    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, [service, maxLines]);

  return lines;
}
