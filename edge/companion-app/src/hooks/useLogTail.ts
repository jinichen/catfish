/** 订阅 Tauri 后端发的 log 事件流(Rust 端 emit "log:<service>"). */

import { useEffect, useState } from "react";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { tailLogs, stopTailLogs } from "../lib/tauri";

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
    setLines([]);

    (async () => {
      try {
        const fn = await listen<LogLine>(`log:${service}`, (e) => {
          if (cancelled) return;
          setLines((prev) => {
            const next = [...prev, e.payload];
            return next.length > maxLines ? next.slice(-maxLines) : next;
          });
        });

        if (cancelled) {
          fn();
          return;
        }
        unlisten = fn;

        await tailLogs(service);
      } catch (e) {
        // 失败时也通过事件流送一条假 log 让用户能在 LogPanel 看到出了啥事
        console.warn(`[useLogTail] failed for ${service}:`, e);
      }
    })();

    return () => {
      cancelled = true;
      unlisten?.();
      stopTailLogs(service).catch(() => {
        // tail 已停或没起,忽略
      });
    };
  }, [service, maxLines]);

  return lines;
}
