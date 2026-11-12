/**
 * 发邮件的附件合计上限 (11/12 鸿波「附件的大小最好有地方设置」)。
 *
 * 默认 25 MB (多数企业邮箱单封 20–50 MB, 编码后还要大三分之一)。员工在「邮件 → 设置」里改 (5–200 MB),
 * 写信面板 (ComposeCore) 照这个提示 / 拦, 存草稿时也传给 catfish-email CLI (--max-attach-mb)。
 * 只存这台电脑 (localStorage); 读不到就用默认。
 */
const KEY = "catfish.email.attachLimitMb";
export const DEFAULT_ATTACH_MB = 25;
export const MIN_ATTACH_MB = 5;
export const MAX_ATTACH_MB = 200;

const listeners = new Set<() => void>();

export function clampAttachMb(mb: number): number {
  if (!Number.isFinite(mb)) return DEFAULT_ATTACH_MB;
  return Math.min(MAX_ATTACH_MB, Math.max(MIN_ATTACH_MB, Math.round(mb)));
}

export function getAttachLimitMb(): number {
  try {
    const v = localStorage.getItem(KEY);
    return v ? clampAttachMb(Number(v)) : DEFAULT_ATTACH_MB;
  } catch {
    return DEFAULT_ATTACH_MB;
  }
}

export function setAttachLimitMb(mb: number): number {
  const v = clampAttachMb(mb);
  try {
    localStorage.setItem(KEY, String(v));
  } catch {
    /* 存不下就这次用 */
  }
  listeners.forEach((f) => f());
  return v;
}

export const attachLimitBytes = () => getAttachLimitMb() * 1024 * 1024;

/** 别处改了上限 (设置页 / 发邮件弹窗里) 时通知 */
export function onAttachLimitChange(f: () => void): () => void {
  listeners.add(f);
  return () => listeners.delete(f);
}
