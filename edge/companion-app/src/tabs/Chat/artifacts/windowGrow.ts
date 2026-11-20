/**
 * 11/20 鸿波「工作区被切成两部分不合理，应该是在右侧增加一块区域作为预览区」:
 * 以前打开右侧栏 (成果 / 浏览器) 是从原来的对话区里切一半出去, 对话和预览都挤。
 * 现在打开时窗口向右长出右侧栏那么宽, 对话区保持原宽; 关掉 (或离开对话页) 再缩回去。
 *
 * - 右边屏幕不够: 窗口先往左挪; 整块屏幕都不够: 能长多少长多少, 剩下的照旧从对话区里让 (ArtifactPanel 的并排 / 浮起逻辑)。
 * - 窗口最大化 / 全屏: 不动窗口。
 * - 开着期间员工自己拖了窗口大小: 关掉时不再缩, 以员工的为准。
 *
 * 全用物理像素算 (窗口位置、屏幕可用区都是物理像素), 免得高分屏来回换算差一两像素。
 */

export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface GrowPlan {
  /** 窗口加宽多少 (物理像素) */
  grow: number;
  /** 窗口新的左边位置; 不用挪就是原位置 */
  x: number;
}

/** 少于这么多 (逻辑像素) 就不折腾窗口了 */
const MIN_GROW_CSS = 40;

/**
 * 算窗口怎么长: win = 窗口外框 (位置 + 外尺寸), area = 所在屏幕的可用区 (去掉菜单栏 / 程序坞), want = 想加的宽度。
 * 都是物理像素。长不出 minGrow 就返回 null。
 */
export function planGrow(win: Rect, area: Rect, want: number, minGrow: number): GrowPlan | null {
  const maxWidth = area.width;
  const grow = Math.min(want, Math.max(0, maxWidth - win.width));
  if (grow < minGrow) return null;
  const rightRoom = area.x + area.width - (win.x + win.width);
  const shift = Math.max(0, grow - Math.max(0, rightRoom));
  const x = Math.max(area.x, win.x - shift);
  return { grow, x };
}

interface Grown {
  grow: number;
  /** 长之前窗口左边在哪; 长的时候往左挪过才需要挪回去 */
  oldX: number;
  newX: number;
  /** 长完之后的外宽; 关的时候对不上 = 员工自己拖过, 不缩 */
  width: number;
}

let grown: Grown | null = null;
let queue: Promise<void> = Promise.resolve();

function inTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

function serial(job: () => Promise<void>): Promise<void> {
  queue = queue.then(job).catch(() => {
    /* 窗口操作失败就保持现状: 右侧栏照旧从对话区里让位置 */
  });
  return queue;
}

/** 右侧栏打开: 窗口向右长出 widthCss (逻辑像素) */
export function growWindowFor(widthCss: number): Promise<void> {
  if (!inTauri()) return Promise.resolve();
  return serial(async () => {
    if (grown) return;
    const { getCurrentWindow, currentMonitor, PhysicalPosition, PhysicalSize } = await import("@tauri-apps/api/window");
    const win = getCurrentWindow();
    if ((await win.isFullscreen()) || (await win.isMaximized())) return;
    const monitor = await currentMonitor();
    if (!monitor) return;
    const [pos, outer, inner] = await Promise.all([win.outerPosition(), win.outerSize(), win.innerSize()]);
    const scale = monitor.scaleFactor || 1;
    const area = monitor.workArea ?? { position: monitor.position, size: monitor.size };
    const plan = planGrow(
      { x: pos.x, y: pos.y, width: outer.width, height: outer.height },
      { x: area.position.x, y: area.position.y, width: area.size.width, height: area.size.height },
      Math.round(widthCss * scale),
      Math.round(MIN_GROW_CSS * scale),
    );
    if (!plan) return;
    if (plan.x !== pos.x) await win.setPosition(new PhysicalPosition(plan.x, pos.y));
    await win.setSize(new PhysicalSize(inner.width + plan.grow, inner.height));
    grown = { grow: plan.grow, oldX: pos.x, newX: plan.x, width: outer.width + plan.grow };
  });
}

/** 右侧栏关掉: 缩回打开前的宽度 (和位置) */
export function shrinkWindowBack(): Promise<void> {
  if (!inTauri()) return Promise.resolve();
  return serial(async () => {
    const g = grown;
    grown = null;
    if (!g) return;
    const { getCurrentWindow, PhysicalPosition, PhysicalSize } = await import("@tauri-apps/api/window");
    const win = getCurrentWindow();
    if ((await win.isFullscreen()) || (await win.isMaximized())) return;
    const [pos, outer, inner] = await Promise.all([win.outerPosition(), win.outerSize(), win.innerSize()]);
    if (Math.abs(outer.width - g.width) > 2) return; // 员工自己拖过窗口
    await win.setSize(new PhysicalSize(Math.max(1, inner.width - g.grow), inner.height));
    if (g.newX !== g.oldX && pos.x === g.newX) await win.setPosition(new PhysicalPosition(g.oldX, pos.y));
  });
}

/** 测试用 */
export function _resetGrown(): void {
  grown = null;
  queue = Promise.resolve();
}
