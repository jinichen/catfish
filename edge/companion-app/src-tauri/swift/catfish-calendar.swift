// catfish-calendar — EventKit CLI 替换 osascript 调 Calendar.app.
//
// 背景 (5/21 鸿波拍板提前): osascript 调 Calendar.app 冷启动 1-3s, 峰值 20+s,
// 经常撞 15s 超时. EventKit 直调 macOS 原生 API < 100ms 稳定, 不依赖 Calendar.app
// 进程, 不走 AppleScript 解析.
//
// 子命令:
//   today  --json     → 输出今日 events JSON
//   week   --json     → 输出未来 7 天 events JSON
//   list-cals --json  → 列所有日历账号
//   create --title=X --start=ISO --end=ISO [--location=L] [--description=D] [--calendar=C]
//                     → 创建事件, 返 JSON {ok, event_id}
//
// 输出 JSON 格式跟 Rust 端 osascript 路径**严格一致** (字段名 / 类型), Rust 端 deserialize
// 不用改 (跟 commands/calendar.rs JournalEvent / CalendarEvent struct 对齐).
//
// 退出码:
//   0 成功
//   1 参数错误
//   2 权限拒绝 (EKEventStore.requestAccess returned false)
//   3 EventKit 内部错误
//
// 编译:
//   swiftc -o catfish-calendar catfish-calendar.swift -framework EventKit -framework Foundation
//
// 部署:
//   tauri build 把 binary 拷到 .app/Contents/Resources/catfish-calendar
//   src-tauri/build.rs 负责编译 + 拷贝
//
// 跨平台:
//   Swift 只 macOS. Windows/Linux Companion 走 osascript fallback (本来就只 macOS 走日历).

import Foundation
import EventKit

// ── 输出 ────────────────────────────────────────────

func printJSON<T: Encodable>(_ obj: T) {
    let encoder = JSONEncoder()
    encoder.dateEncodingStrategy = .iso8601
    encoder.outputFormatting = [.withoutEscapingSlashes]
    do {
        let data = try encoder.encode(obj)
        if let s = String(data: data, encoding: .utf8) {
            print(s)
        }
    } catch {
        FileHandle.standardError.write("JSON encode 失败: \(error)\n".data(using: .utf8)!)
        exit(3)
    }
}

func printErr(_ msg: String) {
    FileHandle.standardError.write("\(msg)\n".data(using: .utf8)!)
}

// ── 权限 ────────────────────────────────────────────

func requestAccess() -> EKEventStore? {
    let store = EKEventStore()
    let semaphore = DispatchSemaphore(value: 0)
    var granted = false

    // macOS 14+: requestFullAccessToEvents; 老版本: requestAccess(to:completion:)
    if #available(macOS 14.0, *) {
        store.requestFullAccessToEvents { ok, err in
            granted = ok
            if let err = err {
                printErr("EventKit requestFullAccessToEvents 错: \(err.localizedDescription)")
            }
            semaphore.signal()
        }
    } else {
        store.requestAccess(to: .event) { ok, err in
            granted = ok
            if let err = err {
                printErr("EventKit requestAccess 错: \(err.localizedDescription)")
            }
            semaphore.signal()
        }
    }

    _ = semaphore.wait(timeout: .now() + 10)  // 10s 超时, 远小于 osascript 15s

    if !granted {
        printErr("EventKit 权限被拒. 系统设置 → 隐私与安全性 → 日历 → Catfish Companion → 完全日历访问权限.")
        return nil
    }
    return store
}

// ── 数据结构 (跟 Rust CalendarEvent struct 对齐) ─────────────────

struct EventOut: Encodable {
    let uid: String
    let summary: String
    let start: String     // ISO 8601
    let end: String       // ISO 8601
    let all_day: Bool
    let location: String
    let calendar: String
    let attendees: [String]
    let description: String
}

struct CalendarOut: Encodable {
    let identifier: String
    let title: String
    let source: String        // iCloud / Google / Local
    let allows_modifications: Bool
}

struct CreateResult: Encodable {
    let ok: Bool
    let event_id: String?
    let error: String?
}

// ── 工具 ────────────────────────────────────────────

let isoFormatter: ISO8601DateFormatter = {
    let f = ISO8601DateFormatter()
    f.formatOptions = [.withInternetDateTime]
    return f
}()

func eventToOut(_ e: EKEvent) -> EventOut {
    let attendeeNames: [String]
    if let participants = e.attendees {
        attendeeNames = participants.compactMap { p in
            p.name ?? p.url.absoluteString.replacingOccurrences(of: "mailto:", with: "")
        }
    } else {
        attendeeNames = []
    }
    return EventOut(
        uid: e.eventIdentifier ?? "",
        summary: e.title ?? "(无标题)",
        start: isoFormatter.string(from: e.startDate),
        end: isoFormatter.string(from: e.endDate),
        all_day: e.isAllDay,
        location: e.location ?? "",
        // macOS SDK 新版本 e.calendar 是 EKCalendar?, 用 ?? 兜底
        calendar: e.calendar?.title ?? "未知日历",
        attendees: attendeeNames,
        description: e.notes ?? ""
    )
}

func filterSubscribed(_ events: [EKEvent]) -> [EKEvent] {
    // 跳订阅日历 (节假日 / 法定假日等), 跟 osascript 路径一致
    return events.filter { evt in
        // EKCalendar.allowsContentModifications == false 通常表示订阅日历
        // 但有时 iCloud 默认日历也 false, 保险起见 OR
        // macOS SDK 新版本 evt.calendar 是 Optional, nil 视为非订阅保留
        guard let cal = evt.calendar else { return true }
        let title = cal.title.lowercased()
        let isSubscribed = title.contains("假日") || title.contains("holiday")
            || title.contains("中国") || title.contains("birthday")
            || title.contains("生日") || title.contains("subscribed")
        return !isSubscribed
    }
}

// ── 子命令 ──────────────────────────────────────────

func cmdToday() {
    guard let store = requestAccess() else { exit(2) }

    let cal = Calendar.current
    let startOfDay = cal.startOfDay(for: Date())
    let endOfDay = cal.date(byAdding: .day, value: 1, to: startOfDay)!.addingTimeInterval(-1)

    let predicate = store.predicateForEvents(
        withStart: startOfDay,
        end: endOfDay,
        calendars: nil  // 所有日历
    )
    let events = filterSubscribed(store.events(matching: predicate))
        .sorted { $0.startDate < $1.startDate }

    printJSON(events.map(eventToOut))
}

func cmdWeek() {
    guard let store = requestAccess() else { exit(2) }

    let cal = Calendar.current
    let startOfDay = cal.startOfDay(for: Date())
    let endOfWeek = cal.date(byAdding: .day, value: 7, to: startOfDay)!.addingTimeInterval(-1)

    let predicate = store.predicateForEvents(
        withStart: startOfDay,
        end: endOfWeek,
        calendars: nil
    )
    let events = filterSubscribed(store.events(matching: predicate))
        .sorted { $0.startDate < $1.startDate }

    printJSON(events.map(eventToOut))
}

func cmdListCalendars() {
    guard let store = requestAccess() else { exit(2) }
    let cals = store.calendars(for: .event)
    let out = cals.map { c in
        CalendarOut(
            identifier: c.calendarIdentifier,
            title: c.title,
            source: c.source.title,
            allows_modifications: c.allowsContentModifications
        )
    }
    printJSON(out)
}

func parseArg(_ args: [String], _ key: String) -> String? {
    let prefix = "--\(key)="
    for a in args {
        if a.hasPrefix(prefix) {
            return String(a.dropFirst(prefix.count))
        }
    }
    return nil
}

func cmdCreate(args: [String]) {
    guard let title = parseArg(args, "title"),
          let startStr = parseArg(args, "start"),
          let endStr = parseArg(args, "end") else {
        printErr("create 缺参数: 需要 --title=X --start=ISO --end=ISO")
        exit(1)
    }
    guard let startDate = isoFormatter.date(from: startStr),
          let endDate = isoFormatter.date(from: endStr) else {
        printErr("create start / end ISO 解析失败 (要 2026-05-21T14:00:00+08:00 这种)")
        exit(1)
    }
    guard let store = requestAccess() else { exit(2) }

    let event = EKEvent(eventStore: store)
    event.title = title
    event.startDate = startDate
    event.endDate = endDate
    event.location = parseArg(args, "location")
    event.notes = parseArg(args, "description")

    // 选 calendar: --calendar=工作 名字; 缺省用 defaultCalendarForNewEvents
    if let calName = parseArg(args, "calendar") {
        if let cal = store.calendars(for: .event).first(where: { $0.title == calName }) {
            event.calendar = cal
        } else {
            printErr("找不到日历 '\(calName)', 用默认日历")
            event.calendar = store.defaultCalendarForNewEvents
        }
    } else {
        event.calendar = store.defaultCalendarForNewEvents
    }

    do {
        try store.save(event, span: .thisEvent, commit: true)
        printJSON(CreateResult(ok: true, event_id: event.eventIdentifier, error: nil))
    } catch {
        printErr("EventKit save 失败: \(error.localizedDescription)")
        printJSON(CreateResult(ok: false, event_id: nil, error: error.localizedDescription))
        exit(3)
    }
}

// ── main ────────────────────────────────────────────

let args = CommandLine.arguments
guard args.count >= 2 else {
    printErr("用法: catfish-calendar today|week|list-cals|create [--args]")
    exit(1)
}

let cmd = args[1]
let rest = Array(args.dropFirst(2))

switch cmd {
case "today":
    cmdToday()
case "week":
    cmdWeek()
case "list-cals":
    cmdListCalendars()
case "create":
    cmdCreate(args: rest)
default:
    printErr("未知子命令: \(cmd)")
    exit(1)
}
