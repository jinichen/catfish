# Windows 客户端构建 (BL-WIN1, 5/8)

## 目标范围 (今晚做的)

只做 cross-build (mac 出 Windows .exe), **不做** 验证 / 真机跑通 / MSI 打包 / 代码签名.
出包之后拷 Windows 机器手动装 WebView2 Runtime, 双击 .exe 看能不能起来.

## 跑

```bash
cd ~/person_task/catfish/edge/companion-app
./scripts/build-windows.sh
```

输出: `src-tauri/target/x86_64-pc-windows-gnu/release/catfish-companion-app.exe`

第一次会装 mingw-w64 (`brew install mingw-w64`) + Rust target x86_64-pc-windows-gnu.

## 已经做的 (代码层)

- Cargo `[target.x86_64-pc-windows-gnu]` linker 配置 (`src-tauri/.cargo/config.toml`)
- `src-tauri/src/commands/speech.rs` 已经有 Windows stub (whisper 录音返"暂不支持"), 不阻塞编译
- `src-tauri/src/lib.rs:400` macOS-only 的 RunEvent::Reopen 已 cfg-gated
- `src-tauri/src/services/process.rs` Unix / Windows 双路径已写
- Tauri `keyring = "3"` crate 自动支持 Windows Credential Manager (替代 mac Keychain)

## 还没做的 (BL-WIN2 / WIN3 / WIN4 — 真要 Windows 客户上线时分批做)

| BL | 说明 | 工作量 |
|---|---|---|
| BL-WIN2 | tool-bridge `secret_resolver` 加 `credman://` 替代 `keychain://` (mac 专用) | 半天 |
| BL-WIN3 | catfish-browser-launcher Windows Chrome 路径自动检测 (`%ProgramFiles%\Google\Chrome\Application\chrome.exe`) | 1-2 小时 |
| BL-WIN4 | email-agent `outlook_win.py` (pywin32 COM 调 Outlook) | 1-2 天 |
| BL-WIN5 | local-search `daemon_windows.py` (Windows Service 或 startup folder) | 半天 |
| BL-WIN6 | MSI / NSIS installer + Authenticode 签名 (需 Windows 机器 + 证书) | 半天 + 证书申请 |
| BL-WIN7 | sandbox.py Windows AppContainer (代替 sandbox-exec) — 安全侧, 不阻塞 demo | 2-3 天 |

## 已知限制 (cross-build)

- ✗ **不能签名**: signtool 只 Windows
- ✗ **不能出 MSI**: WiX 只 Windows
- ✗ **不能出 NSIS**: makensis 在 mac 上能装 (`brew install makensis`) 但 Tauri-bundler 这条路径有 bug
- ✓ **能出 raw .exe**: 拷过去手动跑

## demo 5/14 的策略建议

- **方案 A** (推荐): demo **mac 上跑**. Windows 客户端等到 demo 之后正式分批做 (BL-WIN2+).
- **方案 B**: 如果客户必须 Windows demo, 找一台 Windows 真机, 把 BL-WIN2 / WIN3 优先级提前, 5/12 之前 ship.

不建议路径: cross-build 出 .exe 直接拿到客户现场跑 — 因为没真机验证过, 现场撞 bug 没救.
