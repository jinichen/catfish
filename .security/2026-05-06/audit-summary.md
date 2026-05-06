# 依赖 CVE 扫描报告 (2026-05-06)

工具: pip-audit (Python) + npm audit (frontend)

## central/llm-gateway

- 扫 81 个 deps
- **0 个已知 CVE**


## central/skills-hub

- 扫 21 个 deps
- **0 个已知 CVE**


## edge/tool-bridge

- 扫 0 个 deps
- **0 个已知 CVE**


## edge/local-search

- 扫 1 个 deps
- **0 个已知 CVE**


## edge/companion-app (npm prod deps)

- **0 个已知 CVE** (critical=0 high=0 moderate=0 low=0)

## edge/companion-app/src-tauri (Rust)

5/6 鸿波本机 unset proxy 后跑通 `cargo audit` (1067 advisory loaded). 真实结果:

- **Vulnerabilities: 0**
- **Warnings: 19** (全是 informational: unmaintained / unsound, **不是漏洞**)
  - 10 个 GTK3 bindings unmaintained — Linux/X11 路径, macOS target 不走
  - 5 个 unic-* unmaintained — urlpattern 间接依赖, 上游 PR 已开
  - 2 个其他 unmaintained — proc-macro-error (build-time) / fxhash
  - 2 个 unsound — `glib::VariantStrIter` (鲶鱼无主动调用) / `rand::rng()` (鲶鱼用 env_logger 标准, 不暴露)

跟 5/6 沙箱手动比对结果**完全一致**.

完整输出: [`cargo-audit-actual.txt`](./cargo-audit-actual.txt) (19 个 warning 逐条 + dependency tree)
分析过程: [`cargo-audit-manual.md`](./cargo-audit-manual.md)

## 总结

- Python (4 组件 / 103 deps): **0 CVE**
- npm (companion-app prod): **0 CVE**
- Rust (Cargo.lock 556 deps): **0 vulnerability**, 19 informational warning (10 GTK3 unmaintained / 5 unic / 2 unsound / 2 其他), **0 个在鲶鱼实际调用路径上**

**3 大组件合计: 0 高危漏洞**, 总成 1067 + Python advisory + npm advisory 比对.

## CI 集成 (建议)

见 `.github/workflows/security.yml` (一并加).
