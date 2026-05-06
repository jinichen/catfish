# Cargo audit 手动比对报告 (2026-05-06)

> 鸿波本机 `cargo install cargo-audit` 因 ustc 镜像 + Clash 没起失败.
> 沙箱里手动跑等价检查: 解析 Cargo.lock + 比对 RustSec advisory-db.

## 方法
1. 拉 RustSec advisory-db tarball (https://github.com/RustSec/advisory-db)
2. 解析 `src-tauri/Cargo.lock` 拿 556 个 (name, version) 对 (470 unique crate)
3. 跟 `crates/` 下 812 个有 advisory 的 crate 求交集 → 68 个
4. 对每个交集 crate 读所有 `RUSTSEC-*.md`, 匹配 `[versions] patched / unaffected` 字段, 跑 SemVer 比对

## 结果

**556 个依赖 / 0 个高危 RCE / 2 个 informational (不在调用路径)**:

### ⚠️ glib 0.18.5 — `RUSTSEC-2024-0429` (informational: unsound)
- 路径: tauri → webkit2gtk-rs → glib (**仅 Linux 平台**)
- patched: `>= 0.20.0`
- 受影响函数: `VariantStrIter::next/nth/last/next_back/nth_back` (NULL ptr deref crash)
- **鲶鱼是否实际暴露**: ❌ 鲶鱼目标平台 macOS, 不走 GTK/WebKit Linux 路径; 即便 Linux 部署也没主动调 `VariantStrIter`
- **何时自动消除**: webkit2gtk/gtk crate 升 glib 0.20 后 (Tauri 上游已经在做)

### ⚠️ rand 0.7.3 — `RUSTSEC-2026-0097` (informational: unsound)
- 路径: 间接依赖 (webview/getrandom 链)
- patched: `>= 0.8.6` (跟 0.10.1 / 0.9.3)
- 触发条件: ① `log` + `thread_rng` features 都开 ② **用户自定义 Logger** ③ logger 内调 `rand::rng()` ④ 64KB 后 reseed
- **鲶鱼是否实际暴露**: ❌ 鲶鱼用 `env_logger` 标准 logger, 不写自定义 logger, 也不在 logger 里调 `rand::rng()`. 触发条件不成立.

## 总结

| | 数量 |
|---|---|
| Cargo.lock 总依赖 | 556 (470 unique crate) |
| 在 RustSec advisory-db 有记录的 | 68 |
| 高危 (RCE / 需立即升级) | **0** |
| Informational unsound (不在鲶鱼调用路径) | 2 |

**给客户结论**: 0 已知高危漏洞. 两个 informational 是 GTK Linux 间接依赖 + rand 特定不常见用法, 鲶鱼不暴露.

## 给鸿波本机正式跑 cargo audit

启 Clash/V2Ray 让 7890 端口活, 或 unset proxy:

```bash
cd ~/person_task/catfish/edge/companion-app/src-tauri

# 方法 1: 启 Clash 后
cargo install cargo-audit --locked && cargo audit

# 方法 2: 临时 unset proxy
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy \
  cargo install cargo-audit --locked
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy \
  cargo audit
```

预期输出跟本报告一致 (2 informational, 0 high), 出新结果发我比对.
