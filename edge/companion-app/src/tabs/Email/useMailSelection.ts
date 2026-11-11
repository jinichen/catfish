/** 邮件页选哪封 (11/11, 1.2.147)。从 EmailTab 拆出来: 那个文件贴着 800 行红线。
 *
 * 之前: 进邮件页右边是一句「👈 左边选一封邮件看详情」, 删掉一封后又回到这句话,
 * 员工得再点一下才能接着看 —— 处理一串邮件时每封都多一步。
 *
 * 现在:
 *   · 进来 (换文件夹、关掉新建也一样) 自动打开列表第一封。自动打开的**不立刻标已读**:
 *     正文读出来时不让客户端标 (markRead:false), 在右边停够 AUTO_READ_DWELL_MS 才标
 *     (见 useEmailDetail)。只是路过邮件页、3 秒内切走, 它还是未读。
 *   · 删掉一封 → 打开它下面那封 (更早的); 删的是最后一封就打开上面那封; 删空了才留白。
 *     也按「自动打开」算, 连删时不会把一路经过的信都标成已读。
 *   · 手动点的、从对话跳过来的草稿 (useDraftFocus) 照旧: 指定哪封开哪封, 立刻标已读。
 *     有待跳的草稿时不抢着开第一封。
 *   · 新建邮件开着时不自动开 (右边是新建面板)。
 *
 * 11/11 (1.2.150) 鸿波截图: 对话里存了草稿跳过来, 列表是草稿箱, 右边却是收件箱第一封。跳转那一步
 * 消费 store.pendingDraftFocus 时 React 先同步重画一次, 那一帧里选草稿 / 换草稿箱的 setState 还没生效,
 * 自动打开看到「没选中、没待跳」就选了缓存里的收件箱第一封, 它的更新排在后面把草稿盖了。
 * 所以「开第一封」只在**真正生效那一刻**还没选中时才算数 (openFirst 用函数式更新), 永远不盖掉指定的那封。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useUIStore } from "../../store/ui";

export const AUTO_READ_DWELL_MS = 3000;

export interface MailSelection {
  selectedId: string | null;
  /** 这封是自动打开的 (进页面 / 删后接着开), 还没被员工点过 */
  autoSelected: boolean;
  /** 员工点的 / 跳转指定的 / 清空选择 */
  setSelectedId: (id: string | null) => void;
  /** 自动打开某封 (null = 清空); 删除后接着开下一封用 */
  selectAuto: (id: string | null) => void;
  /** 还没选中才打开这封 (进页面自动开第一封用): 排在它前面的指定选择 (草稿跳转) 不会被盖掉 */
  openFirst: (id: string) => void;
}

export function useMailSelection(): MailSelection {
  const [sel, setSel] = useState<{ id: string | null; auto: boolean }>({ id: null, auto: false });
  const setSelectedId = useCallback((id: string | null) => setSel({ id, auto: false }), []);
  const selectAuto = useCallback((id: string | null) => setSel({ id, auto: id !== null }), []);
  const openFirst = useCallback((id: string) => setSel((cur) => (cur.id ? cur : { id, auto: true })), []);
  return { selectedId: sel.id, autoSelected: sel.auto, setSelectedId, selectAuto, openFirst };
}

/** 删掉 deletedId 之后该开哪封: 下面那封, 没有就上面那封, 都没有 null */
export function nextAfterDelete(list: readonly { id: string }[], deletedId: string): string | null {
  const i = list.findIndex((m) => m.id === deletedId);
  if (i < 0) return list[0]?.id ?? null;
  return list[i + 1]?.id ?? list[i - 1]?.id ?? null;
}

/** 没选中时自动开第一封; 返回「删掉这封后接着开下一封」 */
export function useAutoOpenFirst(
  sel: MailSelection,
  list: readonly { id: string }[],
  hold: boolean,
): (deletedId: string) => void {
  const pendingDraft = useUIStore((s) => s.pendingDraftFocus);
  const { selectedId, selectAuto, openFirst } = sel;
  const firstId = list[0]?.id ?? null;
  useEffect(() => {
    if (selectedId || hold || pendingDraft || !firstId) return;
    openFirst(firstId);
  }, [selectedId, hold, pendingDraft, firstId, openFirst]);
  const listRef = useRef(list);
  listRef.current = list;
  return useCallback(
    (deletedId: string) => selectAuto(nextAfterDelete(listRef.current, deletedId)),
    [selectAuto],
  );
}
