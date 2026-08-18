# WiX Burn Bootstrapper Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将 Catfish Companion 的 Windows 安装改为一个可显示进度、可恢复、支持离线运行时部署的 WiX Burn 一键安装器。

**Architecture:** 保留 Tauri 生成的 Companion MSI 作为主程序包；新增 Burn Bundle 作为统一入口。Hermes、Chromium、Python 和 uv 组成独立的离线 Runtime 包，由 Runtime 安装器执行解压、校验和版本标记，Burn 负责统一下载/缓存/进度/回滚流程，不再依赖 MSI 内部的长时间同步 CustomAction。

**Tech Stack:** WiX 3.x/已有 candle/light 工具链、WiX Burn Bootstrapper、PowerShell、现有 Windows 本地构建脚本、Tauri MSI。

---

### Task 1: 固定安装包边界与产物契约

**Files:**
- Modify: `edge/companion-app/src-tauri/tauri.conf.json`
- Modify: `edge/companion-app/src-tauri/wix/catfish-postinstall.wxs`
- Modify: `edge/companion-app/scripts/build-windows-msi-local.md`

**Step 1:** 明确 Companion MSI 只负责主程序、快捷方式、WebView2 资源和必要的静态资源。

**Step 2:** 将 Hermes Runtime 的安装职责从 MSI 的 `CatfishRunInstall` CustomAction 移出；保留旧 CustomAction 作为兼容开关或删除，不能让 MSI 和 Burn 重复解压 Runtime。

**Step 3:** 在文档中定义三个产物：`Catfish Companion.msi`、`catfish-runtime-windows-x64.exe`、`Catfish-Companion-Setup.exe`。

**Step 4:** 验证 Tauri MSI 不再因为 Runtime 解压失败而回滚主程序安装。

---

### Task 2: 新增离线 Runtime 安装器

**Files:**
- Create: `edge/companion-app/installer/runtime/install-runtime.ps1`
- Create: `edge/companion-app/installer/runtime/runtime-manifest.json`
- Create: `edge/companion-app/installer/runtime/README.md`

**Step 1:** 让脚本接收 Runtime payload 目录、目标目录、版本和日志路径。

**Step 2:** 对 Hermes、Chromium、Python、uv 逐项执行：检查源文件、解压/复制、校验关键文件、写入版本标记。

**Step 3:** 使用 `robocopy /E /MT:16 /R:1 /W:1` 复制 Hermes 小文件树；输出阶段进度和耗时，禁止静默长时间运行。

**Step 4:** 若目标目录已有相同 manifest hash，则跳过重复安装；若中断，下一次安装清理临时目录后重新执行。

**Step 5:** 为脚本增加 PowerShell 语法检查和离线 dry-run 检查。

---

### Task 3: 新增 WiX Runtime 包装器

**Files:**
- Create: `edge/companion-app/installer/runtime/runtime-installer.wxs`
- Create: `edge/companion-app/installer/runtime/build-runtime-installer.ps1`

**Step 1:** 使用 WiX 生成一个只包含 Runtime payload 和安装入口的独立 EXE/MSI 组件，不把 Runtime 再复制进 Companion MSI。

**Step 2:** 让 Runtime 安装器支持检测已安装版本、失败返回非零、重复运行幂等。

**Step 3:** 输出可供 Burn `ExePackage` 消费的签名/未签名 Runtime installer 产物。

**Step 4:** 在构建脚本中记录压缩前后大小、文件数、manifest hash，避免生成空占位包。

---

### Task 4: 新增 Burn Bundle

**Files:**
- Create: `edge/companion-app/installer/burn/CatfishCompanionBundle.wxs`
- Create: `edge/companion-app/installer/burn/bundle-manifest.json`
- Create: `edge/companion-app/installer/burn/build-burn-bootstrapper.ps1`

**Step 1:** 定义 Burn `Bundle`，链式安装 Companion MSI 和 Runtime installer。

**Step 2:** 添加检测条件：已安装相同 Companion 版本和 Runtime manifest 时跳过对应包。

**Step 3:** 配置统一安装目录、日志位置、失败返回码和回滚行为。

**Step 4:** 确保安装界面显示 MSI 安装、Runtime 解压、Chromium 部署等阶段，不出现无输出的长时间 CustomAction。

**Step 5:** 让 Bundle 支持升级：先安装新版本 Runtime，验证成功后再清理旧版本；不得破坏旧版本回滚能力。

---

### Task 5: 接入 Windows 本地构建流程

**Files:**
- Modify: `edge/companion-app/scripts/build-msi-local.ps1`
- Create: `edge/companion-app/scripts/build-burn-local.ps1`
- Modify: `edge/companion-app/scripts/build-windows-msi-local.md`

**Step 1:** 将现有资源准备逻辑抽成可复用阶段，避免 MSI 和 Burn 各自下载/打包一遍 Hermes、Chromium、Python。

**Step 2:** 先生成 Companion MSI，再生成 Runtime installer，最后生成 Burn Bundle。

**Step 3:** 增加构建前断言：禁止 0 字节 Windows 资源进入最终产物；检查 `hermes-agent-bundle.tar.gz`、`chromium-embed.tar.gz`、Python zip 和 uv.exe。

**Step 4:** 输出最终安装包路径、大小、SHA-256 和资源明细。

**Step 5:** 在 Windows 机器上执行真实构建，不依赖 GitHub Actions 或 Docker。

---

### Task 6: 验证安装性能与故障恢复

**Files:**
- Create: `edge/companion-app/installer/tests/test-burn-install.ps1`
- Modify: `edge/companion-app/scripts/build-windows-msi-local.md`

**Step 1:** 在干净 Windows 虚拟机或测试机上首次安装，记录总耗时和各阶段耗时。

**Step 2:** 在已有相同 Runtime 的机器上重复安装，确认 Runtime 被跳过。

**Step 3:** 在 Hermes 解压阶段中断安装，重新运行，确认能够清理临时目录并恢复。

**Step 4:** 检查 Companion 启动、Hermes 离线运行、Chromium 工具和卸载流程。

**Step 5:** 运行项目自检：

```bash
bash scripts/check_file_sizes.sh --strict
git diff --check
```

预期：新增源文件均低于 800 行；安装器可以在无公网环境完成；MSI 不再执行长时间 Hermes post-install。

