/** 选中邮件 → 拉全文。从 EmailTab 拆出来: 那个文件贴着 800 行红线。
 *
 * 9/27 加: 读到「邮件不存在」怎么办。
 *
 * 列表是一张快照 (还带跨挂载缓存, 切回邮件页先显示上一份)。员工在手机、网页或
 * 「邮件」App 里把信移走 / 删掉之后, 列表里它还在, 一点开就是一段红色的
 * 「Mail 里找不到这条消息 (id 错 / 邮件已删 / 不在该账号下)」—— 看起来像坏了,
 * 其实只是列表过时。Mac 上 9/27 截图里那一屏 Google 收件箱的信, 跑诊断时收件箱
 * 只剩 3 封, 截图里点的那封已经不在了, 就是这种情况。
 *
 * 现在: 从列表拿掉这一封、安静刷新一次列表, 说一句员工看得懂的话。
 * 判据是 CLI 对 DataNotFoundError 统一打的「邮件不存在」(退出码 3), 各来源
 * (Apple Mail / IMAP / Outlook) 都走这一条。
 *
 * 11/11 (1.2.147) 加: 自动打开的那封 (useMailSelection) 读正文时不标已读,
 * 在右边停够 AUTO_READ_DWELL_MS 再标; 员工点了它 (autoRead 变 false) 就立刻标。
 * 手动点开的照旧由 CLI read 顺手标掉, 这里不再多标一次。
 */
import { useEffect, useRef, useState } from "react";
import { emailMarkRead, emailReadMessage } from "../../lib/tauri";
import { AUTO_READ_DWELL_MS } from "./useMailSelection";
import type { FullMessage } from "./components/DetailPane";

export const MAIL_GONE_TEXT =
  "这封邮件已经不在这里了 —— 可能已在手机、网页或邮件 App 里移走或删除。已从列表拿掉并刷新。";

export function isMailGoneError(message: string): boolean {
  return message.includes("邮件不存在");
}

export function useEmailDetail(opts: {
  selectedId: string | null;
  /** 自动打开的: 读正文不标已读, 停够 AUTO_READ_DWELL_MS 才标 */
  autoRead?: boolean;
  /** 读到了且已标已读: 列表 / store 跟着改 */
  onRead: (id: string) => void;
  /** 这封已经不在了: 从列表拿掉并刷新 */
  onGone: (id: string) => void;
}) {
  const { selectedId } = opts;
  const [detail, setDetail] = useState<FullMessage | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  // 回调每次渲染都是新的: 放进 ref, 不让它们进依赖 (进了就会反复重读同一封)
  const callbacks = useRef(opts);
  callbacks.current = opts;
  const autoRead = !!opts.autoRead;
  // detail 是为哪个 id 读的 (CLI 返回的 id 写法不一定跟列表一致, 不拿 detail.id 比)
  const loadedFor = useRef<string | null>(null);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let cancelled = false; // 读的过程中换了一封, 旧结果不许覆盖新的
    setDetailLoading(true);
    setDetailError(null);
    setDetail(null);
    loadedFor.current = null;
    emailReadMessage(selectedId, callbacks.current.autoRead ? { markRead: false } : undefined)
      .then((json) => {
        if (cancelled) return;
        const parsed = JSON.parse(json) as FullMessage;
        loadedFor.current = selectedId;
        setDetail(parsed);
        // CLI 读完已在客户端那侧标已读; is_read 是它返回的最新状态
        if (parsed.is_read) callbacks.current.onRead(selectedId);
      })
      .catch((e) => {
        if (cancelled) return;
        const message = e instanceof Error ? e.message : String(e);
        if (isMailGoneError(message)) {
          setDetailError(MAIL_GONE_TEXT);
          callbacks.current.onGone(selectedId);
        } else {
          setDetailError(message);
        }
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  // 读出来还是未读: 自动打开的停够 3 秒再标, 员工点过的 (或点了这封自动打开的) 立刻标。
  // 切走 / 离开邮件页 → 计时清掉, 不标。
  useEffect(() => {
    if (!selectedId || !detail || detail.is_read || loadedFor.current !== selectedId) return;
    const id = selectedId;
    const timer = setTimeout(() => {
      emailMarkRead(id, true)
        .then(() => {
          setDetail((d) => (d && loadedFor.current === id ? { ...d, is_read: true } : d));
          callbacks.current.onRead(id);
        })
        .catch((e) => console.warn("[email] 标已读失败", id, e));
    }, autoRead ? AUTO_READ_DWELL_MS : 0);
    return () => clearTimeout(timer);
  }, [selectedId, detail, autoRead]);

  return { detail, detailLoading, detailError };
}
