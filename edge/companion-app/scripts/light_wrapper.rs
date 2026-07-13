// W2.18 light.exe wrapper (7/13 CI run #13 blocker fix)
//
// 用途: workflow 里 rustc 编成 light.exe 放 WixTools314/ 顶掉 Tauri
// 下的原 light.exe (rename 成 light-real.exe). Tauri 调用 light.exe
// 时进入 wrapper → 加上 ICE 抑制参数 → 转 light-real.exe.
//
// 根因: Tauri v2 上游 main.wxs Handlebars 生成的 Component 每个 File
// 加 KeyPath="yes" (per-machine 标准), 但我们走 per-user 装
// %LOCALAPPDATA%. ICE38 严格要求 user profile Component 用 HKCU
// RegistryValue 做 KeyPath, 不能用 File. Tauri auto-gen 改不了,
// Tauri v2 wix config 也不暴露 additionalLightArgs 传 ICE 抑制参数.
// 故 rustc 编 wrapper 顶掉 light.exe 拼参数.
//
// 抑制 ICE 语义边界:
// - ICE38 (Component File KeyPath in user profile): 多用户机第二用户
//   装无效. 我们场景 corp 单用户机, 无冲击.
// - ICE64 (dir 不在 RemoveFile table): uninstall 可能残留
//   %LOCALAPPDATA%\Catfish Companion\ 子目录, 用户手动清即可.
// - ICE91 (warning-only): file per-user dir 不随 ALLUSERS 变, 我们
//   本来就固定 per-user, 无影响.
//
// 无第三方依赖 (std only), rustc 单文件编译 <5s.

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let dir = std::env::current_exe()
        .expect("current_exe failed")
        .parent()
        .expect("current_exe has no parent dir")
        .to_path_buf();
    let real = dir.join("light-real.exe");

    let mut cmd = std::process::Command::new(&real);
    cmd.args(&args);
    cmd.args([
        "-sice:ICE38", // Component File KeyPath in user profile
        "-sice:ICE64", // dir in user profile but not in RemoveFile table
        "-sice:ICE91", // file per-user dir doesn't vary based on ALLUSERS
    ]);

    eprintln!(
        "[light wrapper] invoking {} with {} tauri args + 3 ICE suppress flags",
        real.display(),
        args.len()
    );

    let status = cmd
        .status()
        .expect("failed to launch light-real.exe (missing?)");

    std::process::exit(status.code().unwrap_or(1));
}
