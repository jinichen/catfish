# Windows msi Resources (W1 BL-CATFISH-OFFLINE-INSTALL 7/11)

msi 打包时内嵌到 `INSTALLDIR/hermes/` 的 4 个 artifact.
员工机装完点 Companion.msi → wix CustomAction 跑 `install.ps1` 全离线装 hermes.

## 4 个 artifact (打包时产出, 不进 git)

| 文件 | 大小 (est) | 来源 | 打包命令 |
|---|---|---|---|
| `install.ps1` | ~130 KB | 上游 `~/.hermes/hermes-agent/scripts/install.ps1` + catfish offline patch | `python3 ../../../hermes-fork/patch_install_ps1_offline.py --input ~/.hermes/hermes-agent/scripts/install.ps1 --output install.ps1` |
| `uv.exe` | ~15 MB | astral python-build-standalone (Windows x64 build) | `bash ../../scripts/build-windows-resources.sh` step 2 |
| `cpython-3.11.15-embed.zst` | ~30 MB | astral python-build-standalone (Windows x64 build) | `bash ../../scripts/build-windows-resources.sh` step 3 |
| `hermes-agent-bundle.tar.gz` | ~150-200 MB | git clone hermes-agent 上游 tag → tar 打包 | `bash ../../scripts/build-windows-resources.sh` step 4 |

**总 msi 体积估算**: ~330-400 MB (含 Tauri Companion ~20 MB + WebView2 bootstrapper).

## 打包 (macOS/Linux dev 机跑一次, CI 每 release 跑)

```bash
cd edge/companion-app
bash src-tauri/scripts/build-windows-resources.sh
```

脚本干 4 步: (1) 跑 patch tool → 出 offline install.ps1 (2) 下 uv-windows-x86_64 (3) 下 cpython-3.11.15 windows-x86_64 zst (4) tar hermes-agent 源码.

## 上游 pin (drift 保护)

- `install.ps1` SHA256 由 `patch_install_ps1_offline.py` 硬 pin. 上游变了 → exit 1 报错要求 audit.
- hermes-agent 版本由 `edge/companion-app/.hermes-target-version` pin. build script 读这个决定 tar 哪个 tag.
- uv / Python 版本由 build script 顶部常量 pin.

## Placeholder + git (W2.6 fix 7/11)

**4 个 artifact 名称的 empty placeholder 必须进 git**, 否则 tauri build 跨平台
validate 挂 (`resource path ... doesn't exist`). Tauri v2 `WindowsConfig` 无
platform-specific `resources` 字段 (config.rs:1042-1085), `bundle.resources`
top-level 声明**所有 build target 都 validate**.

流程:
- **macOS build (今天)**: 用 empty placeholder, 打进 dmg 的是 4 个 <1KB 空文件,
  实际 macOS app 不用这些 Windows-only 资源, 无功能影响
- **Windows build (Week 3 集成阶段)**:
  1. `bash src-tauri/scripts/build-windows-resources.sh` 覆盖 placeholder 成真文件
  2. `npx tauri build --target x86_64-pc-windows-msvc --bundles msi` 出 msi
  3. `git checkout resources/windows/*.{exe,ps1,zip,gz}` 恢复空 placeholder,
     working tree clean

见 `.gitignore` (log/temp 排除, 4 个 placeholder 空文件 track).
