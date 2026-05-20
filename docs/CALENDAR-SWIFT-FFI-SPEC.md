# Calendar Swift FFI 接 EventKit — Spec (BL-CALENDAR-INTEGRATION step3)

5/20 鸿波 / chenhongbo

## 背景

当前 (5/20 ship) `calendar_today_fetch` / `calendar_week_fetch` 走 osascript JXA 调 Calendar.app object model. 实测 cold start 2-5s, warm 500ms-1s. BriefingCard 第一次切 tab 看不到日历会卡明显. 5 分钟缓存能挡多数重复 fetch, 但首次仍卡.

## 目标

把 osascript shell out 换成 Swift binary 直调 EventKit framework. 调用 < 100ms (一个数量级提升).

## 不动什么

- `calendar.rs` 公开 API (`calendar_today_fetch` / `calendar_week_fetch`) 签名跟返回 JSON 格式保持不变 — Swift binary 输出 ISO date + summary + location 仍按现 `CalendarEvent` schema
- 5 分钟缓存层逻辑保留 (calls 仍可能 50ms, 缓存仍有意义减 Swift binary 进程 spawn)
- TS 前端 / BriefingCard 零改动 — Rust 层透明替换

## 实现路径

### 步骤 1: 写 Swift binary

新文件 `src-tauri/binaries/catfish-calendar/main.swift`:

```swift
import EventKit
import Foundation

// 解析参数: --today | --week
let mode = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "--today"

let store = EKEventStore()
let sema = DispatchSemaphore(value: 0)
var grantedAccess = false

store.requestAccess(to: .event) { granted, _ in
    grantedAccess = granted
    sema.signal()
}
sema.wait()

guard grantedAccess else {
    FileHandle.standardError.write("权限被拒 — 系统设置 → 隐私 → 日历 → catfish-calendar".data(using: .utf8)!)
    exit(2)
}

let calendar = Calendar.current
let today = calendar.startOfDay(for: Date())
let endDate: Date = {
    if mode == "--week" {
        return calendar.date(byAdding: .day, value: 7, to: today)!
    }
    return calendar.date(byAdding: .day, value: 1, to: today)!
}()

let predicate = store.predicateForEvents(withStart: today, end: endDate, calendars: nil)
let events = store.events(matching: predicate)

let iso = ISO8601DateFormatter()
iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]

struct CalendarEvent: Encodable {
    let calendar: String
    let summary: String
    let start: String
    let end: String
    let all_day: Bool
    let location: String?
}

let out = events.map { e in
    CalendarEvent(
        calendar: e.calendar.title,
        summary: e.title ?? "(无标题)",
        start: iso.string(from: e.startDate),
        end: iso.string(from: e.endDate),
        all_day: e.isAllDay,
        location: e.location
    )
}.sorted { $0.start < $1.start }

let encoder = JSONEncoder()
let data = try encoder.encode(out)
FileHandle.standardOutput.write(data)
```

### 步骤 2: 编 binary + 嵌 Tauri sidecar

```bash
# 编译 (universal arm64 + x86_64)
swiftc -O -target arm64-apple-macos11 -o catfish-calendar-aarch64-apple-darwin main.swift
swiftc -O -target x86_64-apple-macos11 -o catfish-calendar-x86_64-apple-darwin main.swift

# 放 Tauri sidecar 路径
cp catfish-calendar-* src-tauri/binaries/
```

`src-tauri/tauri.conf.json` 加 `bundle.externalBin`:

```json
{
  "bundle": {
    "externalBin": ["binaries/catfish-calendar"]
  }
}
```

`src-tauri/capabilities/default.json` 加 sidecar 权限 (Tauri 2.0):

```json
{
  "permissions": [
    {
      "identifier": "shell:allow-execute",
      "allow": [
        { "name": "binaries/catfish-calendar", "sidecar": true }
      ]
    }
  ]
}
```

### 步骤 3: Rust 改 calendar.rs 调 sidecar

```rust
use tauri::Manager;
use tauri_plugin_shell::ShellExt;

#[tauri::command]
pub async fn calendar_today_fetch(
    app: tauri::AppHandle,
    force_refresh: Option<bool>,
) -> Result<String, String> {
    // ... 同前 cache 判断 ...

    let result = app
        .shell()
        .sidecar("catfish-calendar")
        .map_err(|e| e.to_string())?
        .args(["--today"])
        .output()
        .await
        .map_err(|e| format!("sidecar 调用失败: {e}"))?;

    if !result.status.success() {
        let stderr = String::from_utf8_lossy(&result.stderr).to_string();
        return Err(format!("catfish-calendar 失败: {stderr}"));
    }

    let json = String::from_utf8_lossy(&result.stdout).to_string();
    // ... 写 cache 同前 ...
    Ok(json)
}
```

### 步骤 4: Info.plist 加授权描述

`src-tauri/Info.plist` (macOS bundle 用):

```xml
<key>NSCalendarsUsageDescription</key>
<string>鲶鱼 Companion 需要读您的日历来给您写早安播报</string>
```

### 步骤 5: 签名 + 公证 (生产分发)

Swift binary + .app bundle 都要重新签名:

```bash
codesign --force --sign "Developer ID Application: ..." src-tauri/binaries/catfish-calendar-*
codesign --force --sign "Developer ID Application: ..." --options runtime path/to/catfish.app
xcrun notarytool submit catfish.app.zip --apple-id ... --wait
```

## 灰度切换

`~/.catfish/companion.yaml` 加 feature flag:

```yaml
calendar:
  # 默认 false (走老 osascript). 全员 sanity check 通过 → 切 true.
  use_swift_ffi: false
```

`calendar.rs` 根据 flag 选 backend:

```rust
let cfg = read_calendar_config();
if cfg.use_swift_ffi {
    // sidecar 路径
} else {
    // osascript 路径 (保留至少 1 个 release 周期)
}
```

## 风险 / 已知坑

- **macOS 11 最低版本**: EventKit `requestAccess(to: .event)` API 在 macOS 11+. 鲶鱼 Companion 现在 minOSVersion 是多少? 看 tauri.conf.json
- **沙盒授权弹窗**: 第一次跑会弹"catfish-calendar 想访问日历", 跟 osascript 路径同行为. Info.plist NSCalendarsUsageDescription 控文案
- **Swift 编译时间**: 鸿波本机要装 Xcode CLI tools (`xcode-select --install`). CI build 也要 macOS runner + Xcode
- **universal binary 体积**: arm64 + x86_64 各 ~500KB, 接受
- **公证延迟**: notarytool 5-15 分钟. CI 要等
- **EventKit 提供商差异**: iCloud / Google / Exchange 后端 events 全统一从 EKEventStore 出, 跟 osascript 行为一致

## 预估工作量

- 写 Swift binary: 2 小时
- Tauri sidecar 嵌入 + 测试: 3 小时
- 签名 + 公证 pipeline: 2-4 小时 (depends on CI 状态)
- 灰度 flag + 灰度测试: 2 小时

**合计 9-11 小时** (1-2 天). 排在 hermes 0.13 之后 / EmailDigest 完整 ship 之后做.

## 不做的事

- 不一次性砍 osascript 路径 — 至少保留 1 个 release 周期作灰度回滚
- 不在 Swift binary 内做缓存 — Rust 层 cache 是 truth source
- 不支持自定义 day range (--days N) — 当前 today / week 两档够

## 验收标准

- [ ] `cargo test` 全过 (Rust 层 unit test 用 mock binary)
- [ ] 鸿波本机 dev mode 起来 → 切早安 tab → 日历 < 200ms 出
- [ ] BriefingCard 4 行 + 详情区 + 未来 7 天 section 渲染跟 osascript 路径一致
- [ ] 灰度 flag 切回 osascript 不挂

## 关联 ticket

- BL-CALENDAR-INTEGRATION step1 (5/20 上午, ship): osascript JXA `calendar_today_fetch`
- BL-CALENDAR-INTEGRATION step2 (5/20 下午, ship): 5 分钟内存缓存
- BL-CALENDAR-WEEK (5/20 下午, ship): osascript JXA `calendar_week_fetch` 7 天
- **BL-CALENDAR-INTEGRATION step3** (本 spec): Swift FFI 加速
