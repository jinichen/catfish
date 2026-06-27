import { create } from "zustand";
import type { ServiceId, ServiceStatus } from "../types/service";

interface ServicesState {
  statuses: Record<ServiceId, ServiceStatus | undefined>;
  setStatus: (id: ServiceId, status: ServiceStatus) => void;
}

export const useServicesStore = create<ServicesState>((set) => ({
  statuses: {
    gateway: undefined,
    hermes: undefined, // P3.5.125 (6/26 鸿波 catch "hermes hang 无监控")
    chrome: undefined,
    local_search: undefined,
    tool_bridge: undefined,
  },
  setStatus: (id, status) =>
    set((s) => ({ statuses: { ...s.statuses, [id]: status } })),
}));
