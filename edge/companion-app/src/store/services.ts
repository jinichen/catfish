import { create } from "zustand";
import type { ServiceId, ServiceStatus } from "../types/service";

interface ServicesState {
  statuses: Record<ServiceId, ServiceStatus | undefined>;
  setStatus: (id: ServiceId, status: ServiceStatus) => void;
}

export const useServicesStore = create<ServicesState>((set) => ({
  statuses: {
    gateway: undefined,
    chrome: undefined,
    local_search: undefined,
    tool_bridge: undefined,
  },
  setStatus: (id, status) =>
    set((s) => ({ statuses: { ...s.statuses, [id]: status } })),
}));
