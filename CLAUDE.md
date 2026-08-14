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
- **测试代码** (8/14 加): `test_*.py` / `*_test.py` / `*.test.ts(x)` / `*.spec.ts(x)`,
  以及 Rust 源文件里 `#[cfg(test)]` 那几个 mod 的行数。

  理由: 测试天然是追加式的 —— 一个 bug 一条, 拆开只是把同一组断言散到两个
  文件, 收益接近零而改动有风险。而**红线的作用是逼人重新想清楚职责边界**,
  测试文件没有这个问题。

  ⚠ 仍然会**单独列出来**, 不是消失。一个 3000 行的测试文件可能在说明别的事
  (比如那个模块的接口太大), 看得见才判断得了。
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
- **跑测试验证前**: 加 `PYTHONDONTWRITEBYTECODE=1`, 或先
  `find . -name __pycache__ -type d -exec rm -rf {} +`. 见下 § 4.1.
- **触发拆分时**: 用上面的 re-export 协议, 不破 import 兼容, 跟着改测试 monkeypatch.
- **写新 skill / 新 tool / 新 plugin 前**: 先看 `docs/CATFISH-HERMES-BOUNDARY.md`, 扫 `~/.hermes/hermes-agent/skills/` 和 `~/.hermes/hermes-agent/tools/` 防重叠. hermes 有 → 写 cookbook 不写 skill. 重写 hermes 已有的轮子 = 越界.
- **写对外文案 / 销售物料 / 广告词 / demo deck 前**: 先看 `docs/CATFISH-POSITIONING-2026-05-19.md` "产品初衷 — 5 条护栏" 段 (员工拥有 / 跨雇主可携带 / 数据零出端 / 中央边缘分离 / 跨厂商 LLM 不绑死). 广告词可换皮, 初衷 5 条永不动. 5/21 新广告词 "员工成长加速器 + 组织能力沉淀器" 是包装升级, 不是替换初衷.

### 4.1 陈旧 .pyc 会让"测试通过"变成假话 (8/9 踩过)

**症状**: 测试结果跟磁盘上的代码对不上。源码怎么看都没问题, 测试就是红的
(或者更糟 —— 该红的绿了)。

**判据**: CPython 判 `.pyc` 失效**只看两个字段** ——
`(源文件 mtime 取整到秒, 源文件字节数)`。两个都对得上就直接用旧字节码,
根本不读源文件。pyc 头就 16 字节, 后 8 个字节存的就是这俩:

```
python3 -c "import struct,importlib.util;p=importlib.util.cache_from_source('m.py');\
print(struct.unpack('<II', open(p,'rb').read(16)[8:16]))"
```

**最容易踩的场景是变异测试** (故意改坏代码, 看闸红不红, 再改回来):

- `sed` 做**等宽替换** → 字节数一个不差
- 改坏 + 跑测试 + 改回来, 全在**同一秒**内 → mtime 取整后相同
- 两个字段都没变 → `.pyc` 100% 复用, 恢复后的源码**根本没被读**

8/9 就是这样: `"error",            #` 换成 `"error", "raw",     #`, 两边都是
32 字符; 恢复后测试仍报 `FACTS_JSON_KEYS` 里有 `raw`。

**⚠ 别归因成挂载 / 文件系统。** 我第一次就是这么猜的, 还写进了汇报。实测
`/Users/chenhongbo/person_task` 的 mtime 是**纳秒精度**, 文件系统一点问题没有 ——
是 CPython 的判据本身只取到秒。猜的原因写进汇报, 下次就有人照着这个错原因去
排查文件系统。

**做法**: 验证跑一律带 `PYTHONDONTWRITEBYTECODE=1`; 变异测试前后各清一次
`__pycache__`。CI 在干净容器里跑, 命不中这条 —— **它只坑本地验证, 也就是
"我说测过了"这句话的全部依据**。

### 4.2 「改了没效果」先看**跑的是不是你改的那份** (8/9 + 8/10 各踩一次)

一天之内两次, 形式不同但都是同一件事:

- **8/9**: 改了 Companion 怎么都不生效, 排查十几轮。真因是运行中的 app 在
  `/Applications/Adobe Acrobat DC/Catfish Companion.app`, 新包躺在
  `/Applications/` 没人用。
- **8/10**: 加了 400 dump 复现却没落盘。真因是 catfish gateway 是前台
  `python -m catfish_gateway.app` 跑的, **从改代码到复现之间没重启过**。

**8/10 那次有个现成的判据, 值得单独记**: traceback 的**行号跟显示的源码内容
对不上** —— 报 `line 2368` 却显示一行 `#` 注释, 报 `line 2345` 显示 `try:`
而不是真正出错的调用。

Python 的 traceback 行号来自**进程里编译好的 code object**, 而下面那行源码
是**现读磁盘**的。两者不一致 = 磁盘已经改了、进程还是旧的。
**traceback 指向注释行 = 进程没重启**, 这个信号极其可靠, 不用猜。

**别把 hermes 和 catfish gateway 搞混** (8/10 我就给错了命令):

    hermes gateway stop/start   → 只管 hermes (8642), launchd 托管
    catfish gateway (8999)      → 多数时候是自己前台 `python -m catfish_gateway.app`,
                                  只能 Ctrl-C 再起; 上面那条命令碰都碰不到它

改完 gateway 代码要生效, 先 `lsof -ti:8999` 确认是谁在跑。

**顺序**: 「改了没效果」第一条命令永远是确认**跑的是哪一份** ——
`ps` 看进程路径 / `lsof -ti:<port>` 看端口占用 / traceback 行号对不对得上。
先做这个, 再去读代码。反过来就是 8/9 那十几轮。

## 5. 已有拆分参考 (5/20-5/21 round 1+2)

可以模仿的真实拆分 commit:
- `catfish_tools.py` 6454 → 744 (round 1, 抽 5 子模块)
- `catfish_memory.py` 1183 → 685 (round 2, 抽 helpers + 14 常量)
- `adapter.py` 1113 → 688 (round 2, 抽 todo + memory + security)
- `users.py` 886 → 771 (round 2, 抽 IdentityUser model)

每条都跑过 `--strict` + full test suite + re-export 协议. 模式照抄.

---

**违反这条规则的 LLM 输出 = 鸿波的 6h 排查**. 写代码前先 `wc -l` 看一眼, 写完前再跑一次 `check_file_sizes.sh --strict`. 没花几秒钟.
