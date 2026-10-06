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

/// 10/6: 本地向量 (ONNX Runtime + tokenizers) 在哪些目标上编 —— 一个 cfg, 一处真相。
///
/// 7/16 起是 `cfg(target_arch = "aarch64")`: 当时 Intel Mac dmg 撞上 "ort 不发
/// x86_64-apple-darwin 预编译包", 按架构一刀切, 把 Windows x64 也连带切掉了。
/// 可 ort-sys 的分发清单 (build/download/dist.txt) 明明白白有 x86_64-pc-windows-msvc;
/// 客户端里真没有的只有 Intel Mac。所以判据改成 "aarch64 或 Windows x64"。
///
/// Windows 那边 ort 走 `load-dynamic` (Cargo.toml 的 target 表): 不链 pyke 的 /MD
/// 静态库 (跟 .cargo/config.toml 的 +crt-static /MT 撞 LNK2038, 7/13 W2.14 翻过车),
/// 运行时从 ~/.catfish/models/onnxruntime.dll 加载 —— 那个 DLL 随 embed-model 的
/// windows-x64 组件包一起下发 (services/embed_model.rs)。
fn emit_local_embedding_cfg() {
    println!("cargo::rustc-check-cfg=cfg(local_embedding)");
    let target = std::env::var("TARGET").unwrap_or_default();
    // 必须跟 Cargo.toml 里 ort 的两张 target 表**逐字对应**, 否则 cfg 开了却没有 ort
    // 依赖 → E0433 (10/6 CI: Linux x86_64 跑 cargo test 撞的就是这个)。
    //   aarch64-*           → ort 默认 (pyke 静态库)
    //   x86_64-pc-windows-* → ort load-dynamic
    //   x86_64-apple-darwin → 没有 (ort 不发预编译包)
    //   x86_64-unknown-linux-* → CI 跑测试用, 没有客户端, 不带
    let on = target.starts_with("aarch64-") || (target.starts_with("x86_64-") && target.contains("-windows-"));
    if on {
        println!("cargo:rustc-cfg=local_embedding");
    }
}

fn main() {
    emit_build_identity();
    emit_local_embedding_cfg();
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
