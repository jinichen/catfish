# BL-RECMODE-DASHBOARD-UI 实施 plan (#75)

> 5/25 ship 完 #74 (backend 撤掉 cleanup daemon + 加 `list_recordings_with_meta` 给 Dashboard 用), 但 Companion UI 这层留 follow-up (需要 Tauri cargo build, sandbox 没 cargo). 这个 doc 给你回头一气做参考.

## 工作量估算

| 部分 | 时间 |
|---|---|
| Tauri Rust commands (3 个) | 1h |
| TypeScript wrapper (`src/lib/recordings.ts`) | 30 min |
| React `RecordingsCard.tsx` Dashboard 卡 | 1.5h |
| 集成 DashboardTab + 单测 + 真测 | 1h |
| **合计** | **3-4h** |

## 1. Tauri Rust commands (`src-tauri/src/commands/recordings.rs`)

```rust
// 新建 src-tauri/src/commands/recordings.rs
use std::fs;
use std::path::PathBuf;
use std::process::Command;
use serde::{Deserialize, Serialize};

#[derive(Serialize, Deserialize, Debug)]
pub struct RecordingMeta {
    pub session_id: String,
    pub started_at: f64,
    pub size_bytes: u64,
    pub kept_forever: bool,
    pub skill_drafts: Vec<String>,
    pub path: String,
}

#[tauri::command]
pub fn recordings_list() -> Result<Vec<RecordingMeta>, String> {
    // 直接走 gateway HTTP loopback (gateway 的 list_recordings_with_meta)
    // 或者本机 fs 直读 (zero HTTP, 更快). 推荐后者跟 backend 同 pattern.
    let home = std::env::var("HOME").map_err(|e| e.to_string())?;
    let root = PathBuf::from(&home).join(".catfish").join("recordings");
    if !root.exists() {
        return Ok(vec![]);
    }
    let mut out = Vec::new();
    for entry in fs::read_dir(&root).map_err(|e| e.to_string())? {
        let entry = entry.map_err(|e| e.to_string())?;
        let path = entry.path();
        if !path.is_dir() { continue; }
        // 解析 meta.json 拿 started_at
        let meta_path = path.join("meta.json");
        let started_at = if meta_path.exists() {
            // 读 JSON started_at 字段
            // (省略, 参考 backend list_recordings_with_meta)
            0.0
        } else { 0.0 };
        // 算 size 递归累加 (跟 backend cleanup _is_kept_forever 同算法)
        let size = walk_size(&path);
        out.push(RecordingMeta {
            session_id: path.file_name().unwrap().to_string_lossy().to_string(),
            started_at,
            size_bytes: size,
            kept_forever: path.join(".keep_forever").exists(),
            skill_drafts: vec![],  // TODO: glob skill_draft/*/*/SKILL.md
            path: path.to_string_lossy().to_string(),
        });
    }
    out.sort_by(|a, b| b.session_id.cmp(&a.session_id));  // 新的在前
    Ok(out)
}

#[tauri::command]
pub fn recordings_show_in_finder(path: String) -> Result<(), String> {
    // macOS: open -R path  (在 Finder 高亮文件)
    Command::new("open")
        .args(["-R", &path])
        .spawn()
        .map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
pub fn recordings_delete(session_id: String) -> Result<u64, String> {
    let home = std::env::var("HOME").map_err(|e| e.to_string())?;
    let sd = PathBuf::from(&home).join(".catfish").join("recordings").join(&session_id);
    if !sd.exists() { return Err(format!("session {} 不存在", session_id)); }
    let size = walk_size(&sd);
    fs::remove_dir_all(&sd).map_err(|e| e.to_string())?;
    Ok(size)
}

fn walk_size(path: &PathBuf) -> u64 {
    // 递归累加 file size (省略, 见 backend cleanup.py 同算法)
    0
}
```

注册到 `lib.rs`:
```rust
.invoke_handler(tauri::generate_handler![
    // ... 现有 commands
    commands::recordings::recordings_list,
    commands::recordings::recordings_show_in_finder,
    commands::recordings::recordings_delete,
])
```

## 2. TypeScript wrapper (`src/lib/recordings.ts`)

```typescript
import { invoke } from "@tauri-apps/api/tauri";

export interface RecordingMeta {
  session_id: string;
  started_at: number;
  size_bytes: number;
  kept_forever: boolean;
  skill_drafts: string[];
  path: string;
}

export async function listRecordings(): Promise<RecordingMeta[]> {
  return invoke("recordings_list");
}

export async function showInFinder(path: string): Promise<void> {
  return invoke("recordings_show_in_finder", { path });
}

export async function deleteRecording(sessionId: string): Promise<number> {
  return invoke("recordings_delete", { sessionId });
}
```

## 3. React component (`src/tabs/Dashboard/RecordingsCard.tsx`)

```tsx
import { useEffect, useState } from "react";
import * as recApi from "../../lib/recordings";

export function RecordingsCard() {
  const [recordings, setRecordings] = useState<recApi.RecordingMeta[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    recApi.listRecordings().then(r => { setRecordings(r); setLoading(false); });
  }, []);

  const totalMB = recordings.reduce((s, r) => s + r.size_bytes, 0) / 1024 / 1024;

  if (loading) return <div>📹 加载录屏...</div>;
  if (recordings.length === 0) {
    return <div>📹 没有录屏 — RecMode 是 opt-in, 你点 🎬 按钮才会录</div>;
  }

  const handleDelete = async (sid: string) => {
    if (!confirm(`真删 ${sid}? 会从你硬盘删除录屏文件夹.`)) return;
    await recApi.deleteRecording(sid);
    setRecordings(rs => rs.filter(r => r.session_id !== sid));
  };

  return (
    <div>
      <h3>📹 我的录屏 (~{totalMB.toFixed(1)} MB · 100% 在你电脑本机)</h3>
      <p style={{ fontSize: 12, color: "#888" }}>
        catfish 不会自动删. 你想清就用 Finder 或下方按钮.
      </p>
      <table>
        <thead><tr><th>Session</th><th>大小</th><th>含 skill</th><th>操作</th></tr></thead>
        <tbody>
          {recordings.map(r => (
            <tr key={r.session_id}>
              <td>{r.session_id}{r.kept_forever && " ⭐"}</td>
              <td>{(r.size_bytes / 1024 / 1024).toFixed(1)} MB</td>
              <td>{r.skill_drafts.join(", ") || "—"}</td>
              <td>
                <button onClick={() => recApi.showInFinder(r.path)}>📁</button>
                <button onClick={() => handleDelete(r.session_id)}>🗑️</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

## 4. 集成 DashboardTab

```tsx
// DashboardTab.tsx 加一个 card
<RecordingsCard />
```

## 5. 真测步骤

```bash
cd ~/person_task/catfish/edge/companion-app
npm run tauri dev   # 启 dev mode
# 打开 Dashboard, 看到 "📹 我的录屏" 卡
# 测: 点 📁 → Finder 弹出该文件夹
# 测: 点 🗑️ → 二次确认 → 真删
# 测: 没录屏时显示友好提示
```

## 注意事项

1. **跟 #74 backend 一致性**: `recordings_list` 应该读取 `_is_kept_forever` flag (跟 backend 同逻辑). 可以让 Rust 端直接调 gateway HTTP `/api/learn/cleanup?dry_run=true&ttl_days=0` 拿 inventory, 比自己 reimplement 简单, 但加 gateway 依赖.

2. **安全确认**: `recordings_delete` 必须二次确认 — 这是删用户文件不可逆.

3. **国际化**: 字符串放 `i18n.ts` (catfish 已经有 i18n 框架).

4. **错误处理**: gateway 没起 / 文件夹无权限 → 友好提示, 不挂 UI.

5. **设计原则**: 这个 UI 是 **信息展示 + 显式触发**, **不该有自动清理** (跟 #74 BL-RECMODE-NO-AUTO-DELETE 哲学一致).
