/** P3.5.17.c.2 (6/17 鸿波) DEPRECATED — banner 砍.
 *
 * 真因: Companion `info.usage.prompt_tokens` (chat.ts:668) 是 hermes 一个 chat
 * completion 内**多个 LLM API call 累加** prompt_tokens (cumulative cost), 不是
 * 当前 ctx 占用. 鸿波 6/17 banner 显 443K, 真上游单次最大才 135K (135K << 256K
 * ctx, 没 overflow). 拿 cumulative cost 跟 ctx_window 比数学就错, 误报触发.
 *
 * 5/13 ContextCounter (状态栏小标签, tabs/Chat/ContextCounter.tsx) 保留 —
 * cumulative cost 语义合理, 不跟 ctx_window 比 %.
 *
 * P3.5.18 主动压缩 + 弹窗进度的触发入口待 fresh session 重新设计 (见
 * docs/P3.5.18-design.md). 现在 banner 砍掉, 不再有 80%+ 自动 trigger 路径.
 *
 * 文件保留作 deprecation marker — 真删用 `git rm` 鸿波本机执行.
 * App.tsx 已拆 import + 挂载.
 */

export default function ContextOverflowBanner() {
  return null;
}
