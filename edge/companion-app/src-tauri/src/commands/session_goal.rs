//! STUB — DEPRECATED 5/26 (hermes 0.14 原生 /goal + /subgoal 替代).
//!
//! # 历史
//!
//! BL-BRIEFING-GOAL-INPUT (5/20) 加的 BriefingCard 🎯 今日重点输入框 Tauri 后端,
//! 写 ~/.catfish/session_goal.txt 跟 gateway session_goals.py 共享.
//!
//! # 5/26 砍
//!
//! hermes 0.14 原生 /goal + /subgoal (#25449) 替代. catfish 自己实现的两套
//! (catfish gateway 注入 ~/.catfish/session_goal.txt + hermes 注入 hermes 内部 goal)
//! 互不知道, 是状态分裂源. 让员工直接在 chat 输 /goal xxx, hermes 拦.
//!
//! # 这个 stub 留作
//!
//! 防回归: 任何 Tauri invoke 调 session_goal_read/write/clear 都返 error.
//! 跟 gateway session_goals.py 同性质 stub.

/// DEPRECATED 5/26: hermes 0.14 原生 /goal 替代. 调用者应改用 chat 里直接输 /goal.
#[tauri::command]
pub fn session_goal_read() -> Result<Option<String>, String> {
    Err("session_goal_read 5/26 deprecated — hermes 0.14 原生 /goal 替代. 在 chat 输 /goal 看当前.".to_string())
}

#[tauri::command]
pub fn session_goal_write(_text: String) -> Result<(), String> {
    Err("session_goal_write 5/26 deprecated — hermes 0.14 原生 /goal 替代. 在 chat 输 /goal <text>.".to_string())
}

#[tauri::command]
pub fn session_goal_clear() -> Result<(), String> {
    Err("session_goal_clear 5/26 deprecated — hermes 0.14 原生 /goal 替代. 在 chat 输 /goal clear.".to_string())
}
