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
 */
import { useEffect, useRef, useState } from "react";
import { emailReadMessage } from "../../lib/tauri";
import type { FullMessage } from "./components/DetailPane";

export const MAIL_GONE_TEXT =
  "这封邮件已经不在这里了 —— 可能已在手机、网页或邮件 App 里移走或删除。已从列表拿掉并刷新。";

export function isMailGoneError(message: string): boolean {
  return message.includes("邮件不存在");
}

export function useEmailDetail(opts: {
  selectedId: string | null;
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

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let cancelled = false; // 读的过程中换了一封, 旧结果不许覆盖新的
    setDetailLoading(true);
    setDetailError(null);
    setDetail(null);
    emailReadMessage(selectedId)
      .then((json) => {
        if (cancelled) return;
        const parsed = JSON.parse(json) as FullMessage;
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

  return { detail, detailLoading, detailError };
}
