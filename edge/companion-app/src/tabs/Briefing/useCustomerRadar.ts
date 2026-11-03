/** 早安「客户跟进」的数据 (11/3 从 CustomerFollowupStrip 拆出来): 跟进卡片条和问候行里的客户小标签共用一份。 */
import { useCallback, useEffect, useState } from "react";

import { loadActiveClosures } from "../../lib/advisor_closed";
import { filterClosedCards } from "../../lib/customerRadar";
import { salesCustomerRadar, type RadarCard } from "../../lib/tauri_sales";

export interface CustomerRadarState {
  cards: RadarCard[] | null;
  customers: number;
  error: string | null;
  load: () => Promise<void>;
  setError: (e: string | null) => void;
}

export function useCustomerRadar(): CustomerRadarState {
  const [cards, setCards] = useState<RadarCard[] | null>(null);
  const [customers, setCustomers] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [r, closures] = await Promise.all([salesCustomerRadar(), loadActiveClosures()]);
      setCustomers(r.customers);
      setCards(filterClosedCards(r.cards, closures));
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return { cards, customers, error, load, setError };
}
