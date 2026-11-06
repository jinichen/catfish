/** 11/07 对话右侧成果区的开关 / 当前文件 / 宽度。宽度记在本机 (localStorage), 下次打开还是这么宽。 */
import { create } from "zustand";

const WIDTH_KEY = "catfish.artifactPanel.width";
export const ARTIFACT_MIN_WIDTH = 360;
export const ARTIFACT_DEFAULT_WIDTH = 560;

function loadWidth(): number {
  try {
    const n = Number(localStorage.getItem(WIDTH_KEY));
    return Number.isFinite(n) && n >= ARTIFACT_MIN_WIDTH ? n : ARTIFACT_DEFAULT_WIDTH;
  } catch {
    return ARTIFACT_DEFAULT_WIDTH;
  }
}

interface ArtifactState {
  /** 右栏正在显示的文件; null = 右栏关着 */
  path: string | null;
  width: number;
  /** 点「刷新」+1, 让显示区重读 */
  reloadKey: number;
  openArtifact: (path: string) => void;
  close: () => void;
  setWidth: (w: number) => void;
  reload: () => void;
}

export const useArtifactStore = create<ArtifactState>((set) => ({
  path: null,
  width: loadWidth(),
  reloadKey: 0,
  openArtifact: (path) => set({ path }),
  close: () => set({ path: null }),
  setWidth: (w) => {
    const width = Math.max(ARTIFACT_MIN_WIDTH, Math.round(w));
    try {
      localStorage.setItem(WIDTH_KEY, String(width));
    } catch {
      /* 存不了就只在这次生效 */
    }
    set({ width });
  },
  reload: () => set((s) => ({ reloadKey: s.reloadKey + 1 })),
}));
