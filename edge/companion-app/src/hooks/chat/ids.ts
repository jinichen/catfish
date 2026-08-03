/** id / 时间戳小工具 —— useChat.ts 与 runOneRound.ts 共用。
 *
 * 8/3 拆 useChat.ts 时抽出来: 这两个函数原本是 useChat.ts 的模块级 helper,
 * runOneRound 搬走后两边都要用, 再各留一份就会漂。
 */

export function uuid(): string {
  return crypto.randomUUID
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

export function nowIso(): string {
  return new Date().toISOString();
}
