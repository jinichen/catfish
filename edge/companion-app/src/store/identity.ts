import { create } from "zustand";

interface Identity {
  user: string | null;
  soulPath: string | null;
  skin: string | null;
  ssoLoggedIn: boolean;
}

interface IdentityState extends Identity {
  setIdentity: (id: Partial<Identity>) => void;
}

export const useIdentityStore = create<IdentityState>((set) => ({
  user: null,
  soulPath: null,
  skin: null,
  ssoLoggedIn: false,
  setIdentity: (id) => set((s) => ({ ...s, ...id })),
}));
