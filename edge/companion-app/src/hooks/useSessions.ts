/** 拉 Hermes 会话列表 + 单会话详情。 */

import { useEffect, useState } from "react";
import { listSessions, getSession } from "../lib/tauri";
import type { SessionMeta, SessionDetail } from "../types/session";

export function useSessions() {
  const [sessions, setSessions] = useState<SessionMeta[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const list = await listSessions();
        if (!cancelled) setSessions(list);
      } catch (e) {
        if (!cancelled) setError(String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return { sessions, loading, error };
}

export function useSessionDetail(id: string | null) {
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  useEffect(() => {
    if (!id) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const d = await getSession(id);
        if (!cancelled) setDetail(d);
      } catch {
        if (!cancelled) setDetail(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id]);
  return detail;
}
