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
  /** 用户自己最后一次打开 / 切换 / 关闭右栏的时间。这一轮里用户动过, 自动打开就不再抢 (11/07) */
  userActedAt: number;
  /** 用户点的: 胶囊「预览」、正文文件名、右栏顶上切换 */
  openArtifact: (path: string) => void;
  /** 用户点的关闭 */
  close: () => void;
  /** 小鲶写出了新文件, 自动打开; 这一轮 (since 之后) 用户自己开过 / 关过就不动 */
  autoOpen: (path: string, since: number) => void;
  /** 换对话时收起 (不算用户操作) */
  dismiss: () => void;
  setWidth: (w: number) => void;
  reload: () => void;
}

export const useArtifactStore = create<ArtifactState>((set) => ({
  path: null,
  width: loadWidth(),
  reloadKey: 0,
  userActedAt: 0,
  openArtifact: (path) => set({ path, userActedAt: Date.now() }),
  close: () => set({ path: null, userActedAt: Date.now() }),
  autoOpen: (path, since) =>
    set((s) => (s.userActedAt >= since || s.path === path ? s : { path })),
  dismiss: () => set({ path: null }),
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
