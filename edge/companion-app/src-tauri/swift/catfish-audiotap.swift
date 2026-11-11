// catfish-audiotap —— 录「电脑播放的声音」(11/11, 会议录音录线上会议对方的声音)。
//
// 为什么要它: 会议录音原来只录麦克风。腾讯会议 / AONE 这类线上会议, 员工戴耳机时对方的声音只进耳机,
// 麦克风收不到 —— 录下来只有自己说的话。这个小程序把会议软件播出来的声音交给 Companion,
// 跟麦克风那条分开存成第二条音轨。
//
// 两种取法 (按系统版本):
//   · macOS 14.2+ : Core Audio 进程抓取 (process tap)。指定了程序就只录那几个进程 (会议软件的提示音
//                   以外, 音乐 / 别的通知都不进来); 没指定就录全部电脑声音 (不含鲶鱼自己)。
//                   权限: 系统设置 → 隐私与安全性 → 「屏幕与系统录音」里的「仅系统录音」。
//   · macOS 13–14.1: ScreenCaptureKit 只要声音 (画面取 2×2 像素丢掉)。要「屏幕录制」权限。
//   · 更老的系统: 报 unsupported。
//
// 用法:
//   catfish-audiotap probe                     → {"mode":"tap"|"sck"|"none","os":"14.5.0"}
//   catfish-audiotap list                      → [{"name","bundle_id","pids":[..],"playing":bool,"meeting":bool}]
//   catfish-audiotap capture [--pids 1,2] [--bundle com.x]
//       stderr 第一行: {"event":"started","rate":48000,"channels":2,"mode":"tap"}
//       stdout: 32 位浮点交错 PCM, 一直写到 stdin 被关掉 (Companion 停止录音) 或收到 SIGTERM。
//       出错: stderr 一行 {"event":"error","kind":"permission"|"unsupported"|"failed","message":...}, 退出码 1。
//
// 编译: build-audiotap.sh (x86_64 + arm64 通用二进制, 最低 10.15 / 11, 运行时 #available 判断)。

import AVFoundation
import AppKit
import CoreAudio
import CoreMedia
import Foundation
import ScreenCaptureKit

// MARK: - 输出

let stderrHandle = FileHandle.standardError
let stdoutHandle = FileHandle.standardOutput

func emitEvent(_ obj: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: obj, options: []) {
        stderrHandle.write(data)
        stderrHandle.write("\n".data(using: .utf8)!)
    }
}

func fail(_ kind: String, _ message: String) -> Never {
    emitEvent(["event": "error", "kind": kind, "message": message])
    exit(1)
}

func printJSON(_ obj: Any) {
    if let data = try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys]) {
        stdoutHandle.write(data)
        stdoutHandle.write("\n".data(using: .utf8)!)
    }
}

/// 认得的会议软件 (bundle id 前缀)。只用来在列表里排前面、默认选中; 不在表里的程序照样能选。
let meetingBundlePrefixes = [
    "com.tencent.meeting", "com.tencent.wemeet", "com.tencent.tencentmeeting",
    "com.alibaba.aone", "com.alibaba.dingtalk", "com.alibaba.DingTalkMac",
    "us.zoom.xos", "com.microsoft.teams", "com.microsoft.teams2",
    "com.bytedance.lark", "com.electron.lark", "com.ss.lark", "com.feishu",
    "com.cisco.webexmeetingsapp", "com.webex.meetingmanager", "com.google.Chrome.app",
    "com.huawei.welink", "com.tencent.xinWeChat",
]

func isMeetingApp(_ bundleID: String, _ name: String) -> Bool {
    let b = bundleID.lowercased()
    if meetingBundlePrefixes.contains(where: { b.hasPrefix($0.lowercased()) }) { return true }
    let n = name.lowercased()
    return ["腾讯会议", "voov", "aone", "钉钉", "dingtalk", "zoom", "teams", "飞书", "lark", "webex", "welink"]
        .contains(where: { n.contains($0) })
}

// MARK: - Core Audio 小工具

func getProperty<T>(_ obj: AudioObjectID, _ selector: AudioObjectPropertySelector, _ value: inout T) -> OSStatus {
    var addr = AudioObjectPropertyAddress(mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal,
                                          mElement: kAudioObjectPropertyElementMain)
    var size = UInt32(MemoryLayout<T>.size)
    return withUnsafeMutablePointer(to: &value) { AudioObjectGetPropertyData(obj, &addr, 0, nil, &size, $0) }
}

func getString(_ obj: AudioObjectID, _ selector: AudioObjectPropertySelector) -> String? {
    var cf: Unmanaged<CFString>? = nil
    guard getProperty(obj, selector, &cf) == noErr, let s = cf?.takeRetainedValue() else { return nil }
    return s as String
}

func getObjectList(_ obj: AudioObjectID, _ selector: AudioObjectPropertySelector) -> [AudioObjectID] {
    var addr = AudioObjectPropertyAddress(mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal,
                                          mElement: kAudioObjectPropertyElementMain)
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(obj, &addr, 0, nil, &size) == noErr, size > 0 else { return [] }
    var ids = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
    guard AudioObjectGetPropertyData(obj, &addr, 0, nil, &size, &ids) == noErr else { return [] }
    return ids
}

func defaultOutputDeviceUID() -> String? {
    var dev = AudioObjectID(0)
    guard getProperty(AudioObjectID(kAudioObjectSystemObject), kAudioHardwarePropertyDefaultOutputDevice, &dev) == noErr,
          dev != 0 else { return nil }
    return getString(dev, kAudioDevicePropertyDeviceUID)
}

// MARK: - 列出正在用声音的程序

struct AppGroup {
    var name: String
    var bundleID: String
    var pids: [Int32]
    var playing: Bool
}

/// 帮手进程 (com.tencent.meeting.helper) 归到主程序 (com.tencent.meeting) 名下:
/// 声音常常是帮手进程放的, 员工只认得主程序的名字。
func owningApp(bundleID: String, pid: Int32) -> (name: String, bundleID: String) {
    if let app = NSRunningApplication(processIdentifier: pid), app.activationPolicy == .regular,
       let b = app.bundleIdentifier {
        return (app.localizedName ?? b, b)
    }
    let regular = NSWorkspace.shared.runningApplications.filter { $0.activationPolicy == .regular }
    if !bundleID.isEmpty,
       let parent = regular
        .filter({ ($0.bundleIdentifier ?? "").count > 0 && bundleID.hasPrefix($0.bundleIdentifier!) })
        .max(by: { ($0.bundleIdentifier ?? "").count < ($1.bundleIdentifier ?? "").count }) {
        return (parent.localizedName ?? parent.bundleIdentifier!, parent.bundleIdentifier!)
    }
    if let app = NSRunningApplication(processIdentifier: pid) {
        return (app.localizedName ?? (bundleID.isEmpty ? "进程 \(pid)" : bundleID), bundleID)
    }
    return (bundleID.isEmpty ? "进程 \(pid)" : bundleID, bundleID)
}

@available(macOS 14.2, *)
func listTapApps() -> [AppGroup] {
    // 自己和启动自己的 Companion 都不列 (鲶鱼的语音播报不该录进会议)
    let me = ProcessInfo.processInfo.processIdentifier, parent = getppid()
    var groups: [String: AppGroup] = [:]
    for obj in getObjectList(AudioObjectID(kAudioObjectSystemObject), kAudioHardwarePropertyProcessObjectList) {
        var pid: pid_t = 0
        guard getProperty(obj, kAudioProcessPropertyPID, &pid) == noErr, pid > 0, pid != me, pid != parent else { continue }
        let bundle = getString(obj, kAudioProcessPropertyBundleID) ?? ""
        // 没名字的系统进程 (声音服务的客户端) 员工认不出, 不列
        if bundle.isEmpty && NSRunningApplication(processIdentifier: pid) == nil { continue }
        var running: UInt32 = 0
        _ = getProperty(obj, kAudioProcessPropertyIsRunningOutput, &running)
        let owner = owningApp(bundleID: bundle, pid: pid)
        if owner.bundleID.hasPrefix("com.apple.") && !owner.bundleID.hasPrefix("com.apple.Safari") { continue }
        let key = owner.bundleID.isEmpty ? owner.name : owner.bundleID
        var g = groups[key] ?? AppGroup(name: owner.name, bundleID: owner.bundleID, pids: [], playing: false)
        g.pids.append(pid)
        g.playing = g.playing || running != 0
        groups[key] = g
    }
    return Array(groups.values)
}

func listRegularApps() -> [AppGroup] {
    NSWorkspace.shared.runningApplications
        .filter { $0.activationPolicy == .regular && !($0.bundleIdentifier ?? "").hasPrefix("com.apple.") }
        .map { AppGroup(name: $0.localizedName ?? ($0.bundleIdentifier ?? "?"), bundleID: $0.bundleIdentifier ?? "",
                        pids: [$0.processIdentifier], playing: false) }
}

func cmdList() {
    var apps: [AppGroup]
    if #available(macOS 14.2, *) { apps = listTapApps() } else { apps = listRegularApps() }
    apps.sort { a, b in
        let ma = isMeetingApp(a.bundleID, a.name), mb = isMeetingApp(b.bundleID, b.name)
        if ma != mb { return ma }
        if a.playing != b.playing { return a.playing }
        return a.name < b.name
    }
    printJSON(apps.map { ["name": $0.name, "bundle_id": $0.bundleID, "pids": $0.pids, "playing": $0.playing,
                          "meeting": isMeetingApp($0.bundleID, $0.name)] as [String: Any] })
}

func cmdProbe() {
    let v = ProcessInfo.processInfo.operatingSystemVersion
    let os = "\(v.majorVersion).\(v.minorVersion).\(v.patchVersion)"
    var mode = "none"
    if #available(macOS 14.2, *) { mode = "tap" } else if #available(macOS 13.0, *) { mode = "sck" }
    printJSON(["mode": mode, "os": os])
}

// MARK: - 写 PCM

/// 把一块音频 (可能是分声道的) 交错成 [Float] 写到 stdout。
func writeInterleaved(_ abl: UnsafePointer<AudioBufferList>, channels: Int, nonInterleaved: Bool) {
    let list = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: abl))
    if !nonInterleaved || list.count == 1 {
        for buf in list {
            guard let p = buf.mData, buf.mDataByteSize > 0 else { continue }
            stdoutHandle.write(Data(bytes: p, count: Int(buf.mDataByteSize)))
        }
        return
    }
    let frames = Int(list[0].mDataByteSize) / MemoryLayout<Float>.size
    var out = [Float](repeating: 0, count: frames * channels)
    for (c, buf) in list.enumerated() where c < channels {
        guard let p = buf.mData?.assumingMemoryBound(to: Float.self) else { continue }
        for i in 0..<frames { out[i * channels + c] = p[i] }
    }
    out.withUnsafeBytes { stdoutHandle.write(Data($0)) }
}

// MARK: - macOS 14.2+: process tap

/// pid → Core Audio 的进程对象 (这个进程没用过声音就没有)
func processObject(_ pid: Int32) -> AudioObjectID? {
    var p = pid
    var obj = AudioObjectID(kAudioObjectUnknown)
    var addr = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyTranslatePIDToProcessObject,
                                          mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
    var size = UInt32(MemoryLayout<AudioObjectID>.size)
    let st = AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr,
                                        UInt32(MemoryLayout<pid_t>.size), &p, &size, &obj)
    return st == noErr && obj != kAudioObjectUnknown ? obj : nil
}

@available(macOS 14.2, *)
final class TapRecorder {
    var tapID = AudioObjectID(kAudioObjectUnknown)
    var aggID = AudioObjectID(kAudioObjectUnknown)
    var procID: AudioDeviceIOProcID?

    func start(pids: [Int32]) {
        let objects = pids.compactMap(processObject)
        if !pids.isEmpty && objects.isEmpty {
            fail("failed", "选的程序现在没有在用声音 (可能已退出), 重新选一下")
        }
        let desc: CATapDescription
        if objects.isEmpty {
            // 全部电脑声音, 不含鲶鱼自己 (语音播报之类)
            let mine = [ProcessInfo.processInfo.processIdentifier, getppid()].compactMap(processObject)
            desc = CATapDescription(stereoGlobalTapButExcludeProcesses: mine)
        } else {
            desc = CATapDescription(stereoMixdownOfProcesses: objects)
        }
        desc.name = "catfish meeting tap \(ProcessInfo.processInfo.processIdentifier)"
        desc.isPrivate = true
        desc.muteBehavior = .unmuted  // 照常从耳机 / 喇叭放出来

        var st = AudioHardwareCreateProcessTap(desc, &tapID)
        guard st == noErr else { fail("failed", "建声音抓取失败 (OSStatus \(st))") }

        var fmt = AudioStreamBasicDescription()
        st = getProperty(tapID, kAudioTapPropertyFormat, &fmt)
        guard st == noErr, fmt.mSampleRate > 0 else { cleanup(); fail("failed", "读声音格式失败 (OSStatus \(st))") }
        guard fmt.mFormatID == kAudioFormatLinearPCM, fmt.mFormatFlags & kAudioFormatFlagIsFloat != 0,
              fmt.mBitsPerChannel == 32 else {
            cleanup(); fail("failed", "声音格式不是 32 位浮点 (\(fmt.mFormatID) / \(fmt.mBitsPerChannel) 位)")
        }
        let channels = Int(fmt.mChannelsPerFrame)
        let nonInterleaved = fmt.mFormatFlags & kAudioFormatFlagIsNonInterleaved != 0

        var agg: [String: Any] = [
            kAudioAggregateDeviceNameKey: "catfish-meeting-tap",
            kAudioAggregateDeviceUIDKey: "com.catfish.meeting-tap.\(UUID().uuidString)",
            kAudioAggregateDeviceIsPrivateKey: true,
            kAudioAggregateDeviceIsStackedKey: false,
            kAudioAggregateDeviceTapAutoStartKey: true,
            kAudioAggregateDeviceTapListKey: [[
                kAudioSubTapDriftCompensationKey: true,
                kAudioSubTapUIDKey: desc.uuid.uuidString,
            ]],
        ]
        // 以默认输出设备为时钟 (没插耳机 / 喇叭时也有内建输出); 拿不到就只挂 tap
        if let out = defaultOutputDeviceUID() {
            agg[kAudioAggregateDeviceMainSubDeviceKey] = out
            agg[kAudioAggregateDeviceSubDeviceListKey] = [[kAudioSubDeviceUIDKey: out]]
        }
        st = AudioHardwareCreateAggregateDevice(agg as CFDictionary, &aggID)
        guard st == noErr else { cleanup(); fail("failed", "建录音设备失败 (OSStatus \(st))") }

        let queue = DispatchQueue(label: "catfish.audiotap.io", qos: .userInitiated)
        st = AudioDeviceCreateIOProcIDWithBlock(&procID, aggID, queue) { _, inData, _, _, _ in
            writeInterleaved(inData, channels: channels, nonInterleaved: nonInterleaved)
        }
        guard st == noErr else { cleanup(); fail("failed", "接声音失败 (OSStatus \(st))") }
        st = AudioDeviceStart(aggID, procID)
        guard st == noErr else { cleanup(); fail("permission", "开始录电脑声音失败 (OSStatus \(st)) —— 检查 系统设置 → 隐私与安全性 → 屏幕与系统录音") }
        emitEvent(["event": "started", "rate": Int(fmt.mSampleRate), "channels": channels, "mode": "tap",
                   "processes": objects.count])
    }

    func cleanup() {
        if aggID != kAudioObjectUnknown {
            if let p = procID {
                AudioDeviceStop(aggID, p)
                AudioDeviceDestroyIOProcID(aggID, p)
            }
            AudioHardwareDestroyAggregateDevice(aggID)
            aggID = AudioObjectID(kAudioObjectUnknown)
        }
        if tapID != kAudioObjectUnknown {
            AudioHardwareDestroyProcessTap(tapID)
            tapID = AudioObjectID(kAudioObjectUnknown)
        }
    }
}

// MARK: - macOS 13–14.1: ScreenCaptureKit (只要声音)

@available(macOS 13.0, *)
final class SckRecorder: NSObject, SCStreamOutput, SCStreamDelegate {
    var stream: SCStream?
    let rate = 48000
    let channels = 2
    var announced = false

    func start(bundleIDs: [String]) {
        let sem = DispatchSemaphore(value: 0)
        var content: SCShareableContent?
        var err: Error?
        SCShareableContent.getExcludingDesktopWindows(true, onScreenWindowsOnly: false) { c, e in
            content = c; err = e; sem.signal()
        }
        sem.wait()
        guard let content, let display = content.displays.first else {
            fail("permission", "拿不到屏幕信息 —— 系统设置 → 隐私与安全性 → 屏幕录制 里打开鲶鱼 (\(err?.localizedDescription ?? "无权限"))")
        }
        let me = ProcessInfo.processInfo.processIdentifier
        let apps = content.applications.filter { app in
            bundleIDs.isEmpty ? app.processID != me : bundleIDs.contains(app.bundleIdentifier)
        }
        if !bundleIDs.isEmpty && apps.isEmpty { fail("failed", "选的程序没在运行, 重新选一下") }
        let filter = bundleIDs.isEmpty
            ? SCContentFilter(display: display, excludingApplications: content.applications.filter { $0.processID == me },
                              exceptingWindows: [])
            : SCContentFilter(display: display, including: apps, exceptingWindows: [])
        let cfg = SCStreamConfiguration()
        cfg.capturesAudio = true
        cfg.excludesCurrentProcessAudio = true
        cfg.sampleRate = rate
        cfg.channelCount = channels
        cfg.width = 2
        cfg.height = 2
        cfg.minimumFrameInterval = CMTime(value: 1, timescale: 1)
        let s = SCStream(filter: filter, configuration: cfg, delegate: self)
        do {
            try s.addStreamOutput(self, type: .audio, sampleHandlerQueue: DispatchQueue(label: "catfish.audiotap.sck"))
        } catch {
            fail("failed", "接声音失败: \(error.localizedDescription)")
        }
        let started = DispatchSemaphore(value: 0)
        var startErr: Error?
        s.startCapture { e in startErr = e; started.signal() }
        started.wait()
        if let startErr { fail("permission", "开始录电脑声音失败: \(startErr.localizedDescription) —— 检查 系统设置 → 隐私与安全性 → 屏幕录制") }
        stream = s
        emitEvent(["event": "started", "rate": rate, "channels": channels, "mode": "sck", "processes": apps.count])
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sb: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio, sb.isValid else { return }
        var blockBuffer: CMBlockBuffer?
        // 两个声道分开放 (非交错), AudioBufferList 要按实际大小分配
        var size = 0
        CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sb, bufferListSizeNeededOut: &size, bufferListOut: nil, bufferListSize: 0,
            blockBufferAllocator: nil, blockBufferMemoryAllocator: nil, flags: 0, blockBufferOut: nil)
        guard size > 0 else { return }
        let raw = UnsafeMutableRawPointer.allocate(byteCount: size, alignment: 16)
        defer { raw.deallocate() }
        let ablp = raw.bindMemory(to: AudioBufferList.self, capacity: 1)
        guard CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sb, bufferListSizeNeededOut: nil, bufferListOut: ablp, bufferListSize: size,
            blockBufferAllocator: nil, blockBufferMemoryAllocator: nil, flags: 0, blockBufferOut: &blockBuffer) == noErr
        else { return }
        writeInterleaved(ablp, channels: channels, nonInterleaved: true)
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        fail("failed", "录电脑声音中断: \(error.localizedDescription)")
    }

    func cleanup() {
        let sem = DispatchSemaphore(value: 0)
        stream?.stopCapture { _ in sem.signal() }
        _ = sem.wait(timeout: .now() + 2)
    }
}

// MARK: - capture

func cmdCapture(_ args: [String]) {
    var pids: [Int32] = []
    var bundles: [String] = []
    var i = 0
    while i < args.count {
        switch args[i] {
        case "--pids" where i + 1 < args.count:
            pids = args[i + 1].split(separator: ",").compactMap { Int32($0) }
            i += 2
        case "--bundle" where i + 1 < args.count:
            bundles.append(args[i + 1])
            i += 2
        default:
            i += 1
        }
    }
    signal(SIGPIPE, SIG_IGN)
    var stopper: () -> Void = {}
    if #available(macOS 14.2, *) {
        // --bundle 也认: 按 bundle id 找出它 (含帮手进程) 现在的音频进程
        if pids.isEmpty && !bundles.isEmpty {
            pids = listTapApps().filter { g in bundles.contains(g.bundleID) }.flatMap { $0.pids }
            if pids.isEmpty { fail("failed", "选的程序现在没有在用声音 (会议还没开始?), 先进会议再开始录") }
        }
        let r = TapRecorder()
        r.start(pids: pids)
        stopper = { r.cleanup() }
    } else if #available(macOS 13.0, *) {
        if bundles.isEmpty && !pids.isEmpty {
            bundles = pids.compactMap { NSRunningApplication(processIdentifier: $0)?.bundleIdentifier }
        }
        let r = SckRecorder()
        r.start(bundleIDs: bundles)
        stopper = { r.cleanup() }
    } else {
        fail("unsupported", "这台 Mac 的系统太老 (要 macOS 13 以上) 录不了电脑声音, 只能录麦克风")
    }
    let finish = { stopper(); exit(0) }
    // Companion 关掉 stdin = 停止
    DispatchQueue.global().async {
        while true {
            let d = FileHandle.standardInput.availableData
            if d.isEmpty { break }
        }
        finish()
    }
    let term = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
    signal(SIGTERM, SIG_IGN)
    term.setEventHandler { finish() }
    term.resume()
    let intr = DispatchSource.makeSignalSource(signal: SIGINT, queue: .main)
    signal(SIGINT, SIG_IGN)
    intr.setEventHandler { finish() }
    intr.resume()
    dispatchMain()
}

let argv = Array(CommandLine.arguments.dropFirst())
switch argv.first {
case "probe": cmdProbe()
case "list": cmdList()
case "capture": cmdCapture(Array(argv.dropFirst()))
default:
    FileHandle.standardError.write("用法: catfish-audiotap probe | list | capture [--pids 1,2] [--bundle id]\n".data(using: .utf8)!)
    exit(2)
}
