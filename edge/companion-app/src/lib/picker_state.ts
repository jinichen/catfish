/** P3.5.2 (6/16 鸿波 Dream Engine 配套): chat picker 持久化 wrapper.
 *
 * 设计 (方案 B, 6/16 鸿波拍):
 *   - **员工点 picker 时**调 `savePickerState(model)` (ChatModelPicker.tsx)
 *   - Rust atomic write 到 ~/.catfish/picker_state.json
 *   - catfish-memory plugin sync_turn 读这个文件, 拿到 picker model
 *
 * 真因 (B 方案 audit, 不抄): hermes MemoryProvider.sync_turn 签名是
 * `(user_content, assistant_content, session_id)`, 没 client request header 入参.
 * plugin 没法直接拿 picker 状态. 文件中转是绕开 hermes API 限制的最简方案.
 *
 * ⚠ 8/10 改了触发时机: 原来是"chat.ts 发请求前 fire-and-forget 调", 也就是
 * **每发一条消息写一次**, 写的是 store.model。而 store.model 在员工还没选过时
 * 是 ChatTab catalog effect 灌的 catalog.default (roles.yaml chat_default =
 * deepseek), 于是这条路把系统默认值伪装成员工的选择写进了文件。
 * 现在只在员工真的点 picker 时写 —— 这个文件的语义是"员工选了什么", 不是
 * "上一次请求用了什么"。
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
