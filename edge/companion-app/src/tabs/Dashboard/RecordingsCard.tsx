/** @deprecated 6/2 BL-DASHBOARD-DROP-RECORDINGS-CARD (鸿波 6/2 凌晨): 整卡删.
 *
 * 5/25 #75 ship 时 RecordingsCard 是"员工主权: 看 + 删录屏"的可见兑现. 6/2 #17
 * BL-RECMODE-AUTO-CLEAN-RAW 默认 skill 生成完自动清原料后, ~/.catfish/recordings/
 * 99% 时间是空 / 只剩 KB 级 meta + skill_draft. 这卡 99% 空 = UI noise.
 *
 * 替代方案 (PrivacyCard 🟢 本机存储 加 1 行):
 *   "你录过的屏 (skill 生成完自动清掉, 没生成的留着等你保存)"
 *
 * Tauri 命令 recordings_list / recordings_show_in_finder / recordings_delete
 * 仍保留 (src-tauri/src/commands/recordings.rs), 给 CLI / 调试 / 未来 power-user
 * 入口用. lib/recordings.ts 跟着 deprecate.
 *
 * 文件本身保留作 git blame 锚, 不引用 (DashboardTab.tsx 删了 import).
 * 周一 review 鸿波拍后再决定是否物理 rm.
 */
export {};
