/** P3.5.2 (6/16 鸿波 Dream Engine 配套): chat picker 持久化 wrapper.
 *
 * 设计 (方案 B, 6/16 鸿波拍):
 *   - chat.ts 发请求前 fire-and-forget 调 `savePickerState(model)`
 *   - Rust atomic write 到 ~/.catfish/picker_state.json
 *   - catfish-memory plugin sync_turn 读这个文件, 拿到 picker model
 *   - 优雅: 切 picker → 下次 chat → 自动写文件 → plugin sync_turn 触发时读
 *
 * 真因 (B 方案 audit, 不抄): hermes MemoryProvider.sync_turn 签名是
 * `(user_content, assistant_content, session_id)`, 没 client request header 入参.
 * plugin 没法直接拿 picker 状态. 文件中转是绕开 hermes API 限制的最简方案.
 */

import { invoke } from "@tauri-apps/api/core";

export interface PickerState {
  chat_model: string;
  updated_at: string;
}

/** Fire-and-forget. chat.ts send 前调. 失败静默 catch (chat 不该被这个 await 阻塞). */
export function savePickerState(model: string): void {
  if (!model || !model.trim()) return;
  void invoke<void>("picker_state_save", { model }).catch((e) => {
    console.warn("[picker_state] save 失败 (静默, 不影响 chat):", e);
  });
}

/** 调试用: 读当前持久化的 picker model. */
export async function getPickerState(): Promise<PickerState | null> {
  return invoke<PickerState | null>("picker_state_get");
}
