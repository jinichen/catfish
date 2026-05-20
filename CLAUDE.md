# CLAUDE.md — catfish 项目 LLM 代码生成纪律

> 这是给 Claude / Cowork / Claude Code 看的硬规则. 每次 session 必读, **任何代码改动结束前必须自检**.
>
> 详细背景见 `docs/contributors/CONVENTIONS.md`. 本文是"必读极简版", 不复述, 不软化.

---

## 1. 文件长度红线 — 800 行 (硬性)

**任何源文件 ≥ 800 行 = 红线必拆**. 写代码前先看目标文件当前行数, 写完前再看一次. 越线不允许提交.

阈值速查 (见 `scripts/check_file_sizes.sh`):

| 行数 | 状态 | 该做什么 |
|---:|---|---|
| < 300 | 目标 | 继续 |
| 300–499 | OK | 继续 |
| 500–799 | ⚠️ 警戒 | 看一眼能不能顺手拆 |
| ≥ 800 | 🔴 **红线** | **必拆**, 不许提交 |

**例外** (这些不算源文件, 不卡):
- 配置 (`*.yaml`, `*.toml`, `*.json`)
- schema / 数据文件 (`*_schemas.py` 单个 OpenAI tools list 这种)
- 测试数据 fixtures
- 依赖目录 (`.venv/`, `node_modules/`, `target/`)
- 第三方上游 patch / build artifacts (`.companion-state/` 等)

## 2. 自检命令 — 改完代码必跑

```bash
bash scripts/check_file_sizes.sh --strict
```

返回非 0 = 有 ≥ 800 红线文件, 不许 commit. **改任何 `.py` / `.ts` / `.tsx` / `.rs` / `.js` 文件后, 都要跑一遍.**

只想看自己改的文件:
```bash
git diff --name-only HEAD | xargs -I{} wc -l {} 2>/dev/null | sort -rn | head
```

## 3. 拆分协议 (touchstone-style re-export)

拆大文件时**保 import 兼容**, 不破老 caller:

1. 抽子模块到 `<original>_<purpose>.py` (例: `adapter.py` → `adapter_todo.py`, `adapter_memory.py`).
2. 老文件顶部 re-export 抽出去的符号 — 别的模块还能 `from <original> import X` 拿到:
   ```python
   from .adapter_todo import (  # noqa: F401
       _get_todo_store,
       _persist_todo_store,
       # ... 所有被外部引用的符号
   )
   ```
3. **测试 monkeypatch 路径**要跟着搬: 函数移到新模块, 老测试 `monkeypatch.setattr(adapter, "X", ...)` 不再生效 (Python `from X import Y` 是值复制). 要么把 fixture 改成 patch 新模块, 要么两个模块都 patch.
4. 模块级全局 (cache dict, init flag) 跟函数一起搬, 别拆两半.
5. 子模块需要老模块的私有符号 (例 `_r()`) → 用**延迟 import** 防 circular:
   ```python
   def _r():
       from . import adapter  # noqa: PLC0415
       return adapter._r()
   ```

## 4. 默认行为约束

- **生成代码前**: 看一下目标文件多大. > 500 行就考虑拆而不是继续堆.
- **改完代码后**: 跑 `bash scripts/check_file_sizes.sh --strict` 自检.
- **commit 前**: 自检过 + 测试过. 测试退化 0 容忍.
- **触发拆分时**: 用上面的 re-export 协议, 不破 import 兼容, 跟着改测试 monkeypatch.

## 5. 已有拆分参考 (5/20-5/21 round 1+2)

可以模仿的真实拆分 commit:
- `catfish_tools.py` 6454 → 744 (round 1, 抽 5 子模块)
- `catfish_memory.py` 1183 → 685 (round 2, 抽 helpers + 14 常量)
- `adapter.py` 1113 → 688 (round 2, 抽 todo + memory + security)
- `users.py` 886 → 771 (round 2, 抽 IdentityUser model)

每条都跑过 `--strict` + full test suite + re-export 协议. 模式照抄.

---

**违反这条规则的 LLM 输出 = 鸿波的 6h 排查**. 写代码前先 `wc -l` 看一眼, 写完前再跑一次 `check_file_sizes.sh --strict`. 没花几秒钟.
