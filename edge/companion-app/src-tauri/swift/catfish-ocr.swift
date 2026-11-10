// catfish-ocr — 用 macOS 自带的文字识别 (Vision) 读图片里的字 (11/10)。
//
// 为什么: 本地搜索收不进图片和扫描版 PDF (没有文字层)。鸿波机器上 18 个盖章 / 扫描 PDF (401 页)
// 和 267 张图片搜不到。系统自带识别全在本机跑, 不上传、不花钱; 实测中文正文基本全对
// (公司章程扫描件), 每页约 0.4 秒。
//
// 用法: catfish-ocr <图片> [<图片> ...]
//       catfish-ocr --stdin          (常驻: 从标准输入一行读一个图片路径, 读到 EOF 退出)
//   每张图输出一行 JSON: {"path": ..., "ok": true, "text": "..."} 或 {"path": ..., "ok": false, "error": "..."}
//   PDF 由 local-search 先用 pypdfium2 渲染成图片再交给这里。
//   实测每个进程加载识别模型约 15 秒, 之后每张约 0.4 秒 —— 所以 local-search 用 --stdin 常驻一份,
//   闲一阵子关掉, 而不是一张图起一个进程。
//
// 识别中文要 macOS 11+; 老系统上每张图都报 "needs macOS 11", local-search 记成「本机没法识别」。

import AppKit
import Foundation
import Vision

func emit(_ obj: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: obj, options: []),
       let line = String(data: data, encoding: .utf8) {
        print(line)
        fflush(stdout)
    }
}

func recognize(_ path: String) {
    guard #available(macOS 11.0, *) else {
        emit(["path": path, "ok": false, "error": "needs macOS 11"])
        return
    }
    autoreleasepool {
        let url = URL(fileURLWithPath: path)
        guard let src = CGImageSourceCreateWithURL(url as CFURL, nil),
              let cg = CGImageSourceCreateImageAtIndex(src, 0, nil) else {
            emit(["path": path, "ok": false, "error": "cannot read image"])
            return
        }
        let req = VNRecognizeTextRequest()
        req.recognitionLevel = .accurate
        req.recognitionLanguages = ["zh-Hans", "en-US"]
        req.usesLanguageCorrection = true
        do {
            try VNImageRequestHandler(cgImage: cg, options: [:]).perform([req])
            let lines = (req.results ?? []).compactMap { $0.topCandidates(1).first?.string }
            emit(["path": path, "ok": true, "text": lines.joined(separator: "\n")])
        } catch {
            emit(["path": path, "ok": false, "error": "\(error)"])
        }
    }
}

let args = Array(CommandLine.arguments.dropFirst())
if args == ["--stdin"] {
    while let line = readLine(strippingNewline: true) {
        let path = line.trimmingCharacters(in: .whitespaces)
        if !path.isEmpty { recognize(path) }
    }
} else if args.isEmpty {
    FileHandle.standardError.write("usage: catfish-ocr <image> [<image> ...] | --stdin\n".data(using: .utf8)!)
    exit(2)
} else {
    for path in args { recognize(path) }
}
