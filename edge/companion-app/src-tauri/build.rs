// build.rs — Tauri build hook + 编译 catfish-calendar Swift binary (5/21 EventKit).
//
// 行为:
//   - 仅 macOS: 当 .swift 源比 binary 新时, 跑 swift/build.sh 编译
//   - 输出: swift/catfish-calendar (本目录, tauri bundle resources 直接引用)
//   - Windows / Linux: 跳过, 落 osascript fallback
//
// 防死循环 (5/21 鸿波 tauri dev 反馈):
//   - tauri dev watch 监听 src-tauri 目录, binary 文件 mtime 变会触发 rebuild
//   - 如果每次 build.rs 都无脑 swiftc → binary mtime 变 → 再 rebuild → 死循环
//   - 解法: 用 mtime 比对, 只有源码 .swift 比 binary 新才编译; 已编过且源码没动 → 跳过
//
// 失败处理:
//   - swiftc 找不到 / Swift 编译报错 → 不挂 cargo build, log 警告, 让 Rust 端走 osascript fallback

use std::process::Command;
#[cfg(windows)]
mod build_windows;

/// 9/17: 把 git sha 编进二进制。Windows 上一周的修复没装上机器却没人能看出来 ——
/// MSI 版本一直 1.0.1，关于页、文件属性、日志全都一样。有了 sha，关于页和
/// bootstrap 日志头一眼能分辨机器上跑的是哪份代码。
/// 拿不到 git (交付包源码不带 .git / 没装 git) 时给 "unknown"，不挂构建。
fn emit_build_identity() {
    let sha = Command::new("git")
        .args(["rev-parse", "--short=10", "HEAD"])
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_owned())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "unknown".to_owned());
    let dirty = Command::new("git")
        .args(["status", "--porcelain", "--untracked-files=no"])
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map(|o| !o.stdout.is_empty())
        .unwrap_or(false);
    let sha = if dirty { format!("{sha}-dirty") } else { sha };
    // 秒级 UTC, 不引 chrono
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    println!("cargo:rustc-env=CATFISH_GIT_SHA={sha}");
    println!("cargo:rustc-env=CATFISH_BUILD_UNIX={secs}");
    // HEAD 变了就重跑 (切分支 / 新 commit)
    println!("cargo:rerun-if-changed=../../../.git/HEAD");
    println!("cargo:rerun-if-changed=../../../.git/refs/heads");
}

fn main() {
    emit_build_identity();
    #[cfg(windows)]
    build_windows::build();
    // Swift binary 只 macOS 编译; Win/Linux 跳过
    #[cfg(target_os = "macos")]
    {
        let swift_dir = std::path::PathBuf::from("swift");
        if swift_dir.exists() {
            println!("cargo:rerun-if-changed=swift/catfish-calendar.swift");
            println!("cargo:rerun-if-changed=swift/build.sh");

            let src_path = swift_dir.join("catfish-calendar.swift");
            let bin_path = swift_dir.join("catfish-calendar");

            // 防死循环: 只有源码比 binary 新才重新编译.
            // 第一次 build (binary 不存在) → 编. binary 存在 + 源码没动 → 跳过.
            let need_rebuild = {
                let src_mtime = src_path.metadata().and_then(|m| m.modified()).ok();
                let bin_mtime = bin_path.metadata().and_then(|m| m.modified()).ok();
                match (src_mtime, bin_mtime) {
                    (Some(src), Some(bin)) => src > bin,
                    (Some(_), None) => true,  // binary 不存在
                    _ => false,                // 源码也不存在? 跳
                }
            };

            if need_rebuild {
                let status = Command::new("bash")
                    .arg("build.sh")
                    .current_dir(&swift_dir)
                    .status();

                match status {
                    Ok(s) if s.success() => {
                        println!("cargo:warning=catfish-calendar Swift binary 编译完成");
                    }
                    Ok(s) => {
                        println!("cargo:warning=catfish-calendar 编译失败 (exit {:?}). Rust 走 osascript fallback.", s.code());
                    }
                    Err(e) => {
                        println!("cargo:warning=catfish-calendar 编译 spawn 失败 ({}). Rust 走 osascript fallback.", e);
                    }
                }
            }
        }
    }

    tauri_build::build();
}
