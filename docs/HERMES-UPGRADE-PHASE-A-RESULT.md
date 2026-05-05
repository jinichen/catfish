# Hermes 升级阶段 A 结果 (5/5 下午起, 进行中)

> **状态**: A.1 ✅ A.2 ✅ A.3 ⏳ A.4 ⏳ A.5 ⏳
>
> **目的**: 阶段 A 全做完后, 给鸿波 go/no-go 决策依据 (是否继续阶段 B brand patch 重构 + 真升级 hermes 0.12).
> **demo 倒计时**: 9 天 (5/14)

---

## A.1 ✅ git tag 备份点 (5/5 17:xx)

```
pre-hermes-upgrade-0.10  ← BL-MM2 ship 后状态
```

回滚命令:
```bash
git reset --hard pre-hermes-upgrade-0.10
```

---

## A.2 ✅ verify is_agent_created + skill_manage pin API (5/5 17:xx)

### 抓的源码

`https://raw.githubusercontent.com/NousResearch/hermes-agent/v2026.4.30/tools/skill_usage.py` ✅ HTTP 200

### 关键发现 1: `is_agent_created()` 判定逻辑

```python
def is_agent_created(skill_name: str) -> bool:
    """Whether *skill_name* is neither bundled nor hub-installed."""
    off_limits = _read_bundled_manifest_names() | _read_hub_installed_names()
    return skill_name not in off_limits
```

不在 `~/.hermes/skills/.bundled_manifest` **且**不在 `~/.hermes/skills/.hub/lock.json` 的 `installed` map 里 → 算 agent-created. Curator 只动 agent-created.

### 关键发现 2: catfish skills 物理隔离

`catfish_skill_install` 装到 `catfish/skills/<namespace>/<skill_name>/` — **不是** `~/.hermes/skills/`. 来源: `_catfish_skills_root()` (catfish_tools.py:1880)

```python
def _catfish_skills_root() -> Optional[Path]:
    """优先 CATFISH_SKILLS_DIR env, 否则从本文件向上找含 skills/department/ 的目录"""
```

而 hermes Curator 扫的是 `get_hermes_home() / "skills"` (skill_usage.py: `_skills_dir()`).

**结论**: catfish 装的 skill 跟 hermes Curator **物理隔离**, Curator 根本扫不到. 跟 5/4 文档 § 8 的"6 个冲突场景" 推测一致, 但更强 — **不是"is_agent_created 过滤"挡住, 是路径根本不重叠**.

### 关键发现 3: pin API 形态

公开函数 `set_pinned(skill_name: str, pinned: bool) -> None`. 实现写 `~/.hermes/skills/.usage.json` 该 skill 的 `pinned` 字段.

**关键 bug-trap**: `_mutate()` 内部第一行 `if not is_agent_created(skill_name): return` — **即 hub-installed/bundled skill, set_pinned 是 no-op**. 反过来想这是"双保险": 你 pin 不动也无所谓, 因为 Curator 也跳过.

### 关键发现 4: archive/restore 的双重保险

`archive_skill()` 和 `restore_skill()` 都先查 `is_agent_created()`, 不通过返 error. 防止误档/越权恢复.

### 对升级方案 § 8 的影响

| 原方案步骤 | 修订 | 理由 |
|---|---|---|
| 步骤 1: 保守参数 (interval=168h / idle=4h / stale=60d / archive=180d) | ✅ 保留 | 跟 catfish 隔离无关, 是给 hermes agent-created skill 的人道主义参数 |
| 步骤 2: catfish_skill_install 装完自动 pin | ❌ **取消** | catfish skill 物理隔离, Curator 扫不到, pin 是 no-op |
| 步骤 3: Onboarding explicit consent | ✅ 保留 (改文案) | 员工对"自动归档" 仍敏感, consent 仍要. 但文案要改: 这管的是**员工自己写的脚本** (agent-created), 跟 catfish 装的脚本无关 |
| 步骤 4: Dashboard "整理记录"卡 | ✅ 保留 | 透明可控仍是核心卖点 |
| 步骤 5: 反向数据流 → Hub 健康度 | ✅ 保留 (Phase 3) | 不变 |

### 阶段 B brand patch 重构成本影响

**没影响** — brand patch 跟 Curator 是两条独立线:
- brand patch 改 hermes UI 字符串 (banner/Welcome 等)
- Curator 是 hermes 内部 skill housekeeping
- 二者不交叉

### Verify 总结

✅ **不需要写 hermes hub lock.json 的复杂代码**
✅ **不需要从 catfish 端调 pin API**
✅ **集成方案从 5 步简化为 4 步** (取消步骤 2)
✅ **整体升级风险等级**: 维持原 § 3 评估 — Curator 一项从 🟡 中 降到 🟢 低

---

## A.3 ✅ dry-run apply_brand_patch.py 在 0.12 源码上

### 执行环境

- 源码: `git clone --depth 1 --branch v2026.4.30 NousResearch/hermes-agent /tmp/hermes-012` (78MB)
- 跑法: 复制 `apply_brand_patch.py` 到 `/tmp/apply_brand_patch_test.py`, sed-改 `HERMES_ROOT = Path("/tmp/hermes-012")`, 跑 dry-run (默认)

### dry-run 结果 (22 条规则)

```
WILL  19 条 — 字面量直接命中, 改起来直接 work
SKIP  2 条  — bootBanner.ts 文件不存在
MISS  1 条  — banner.py: agent_name skin fallback 字符串变了
```

汇总: **7 个文件将改, 0 条已 patched, 2 个文件跳过**

### 比文档预期好太多

5/4 文档 § 3 说"0.11 React/Ink 重写 → AST 节点全变 → apply_brand_patch.py 大概率全断". 实际跑下来 **86% (19/22) 命中**, 远好于预期.

为啥比预期好? 0.11 React/Ink 重写**确实**改了 ui-tui 内部结构, 但**字符串字面量大多保留**, 只是搬家:
- `bootBanner.ts` 删了 → 其字符串迁到 `branding.tsx` (我们已覆盖)
- `banner.py` 顶部 banner 字符串保留
- `skin_engine.py` 5 处 welcome/4 处 goodbye 一个不漏全在

### 失败的 3 条详细分析

| 规则 | 失败原因 | 修法 |
|---|---|---|
| `bootBanner: tagline` | `ui-tui/src/bootBanner.ts` 文件被删 (改名 `banner.ts` 但内容不一样) | **删除规则** — 字符串迁到 `branding.tsx`, 已被另一条规则覆盖 |
| `bootBanner: fallback ASCII` | 同上 | 同上, **删除** |
| `banner.py: agent_name skin fallback` | `agent_name = _skin_branding("agent_name", "Hermes Agent")` 这行字面量 0.11+ 没了 (整个 fallback 路径换了) | **降级**: 不致命, agent_name 主路径还在 (`skin_engine.py` 4 处全命中) |

### 0.12 **新增**漏点 (0.10 没覆盖, 0.12 新加)

| 文件 | 行 | 字符串 | 影响 | 修法 |
|---|---|---|---|---|
| `cli.py` | 1727 | `"⚕ NOUS HERMES - AI Agent Framework"` | 启动 banner alt 路径 | **加规则** |
| `cli.py` | 1728 | `"⚕ NOUS HERMES"` | 同上 tiny line | **加规则** |
| `cli.py` | 9343-9345 | `"Goodbye! ⚕"` (新位置, hermes_cli/skin_engine 之外的副本) | exit 时显示 | **加规则** |
| `cli.py` | 9557-9560 | `"Welcome to Hermes Agent! ..."` (新位置) | session 开始 | **加规则** |
| `hermes_cli/main.py` | 5099 | `print(f"Hermes Agent v{__version__} ({__release_date__})")` | `--version` 子命令输出 | **加规则** |
| `rl_cli.py` | 396, 419 | `"Goodbye!"` | rl_cli 入口的退出语 (员工可能不用这个入口) | **加规则** (低优先) |
| `agent/curator.py` | 262 | `"You are running as Hermes' background skill CURATOR..."` | LLM system prompt, **员工看不到** | **不动** |

### Curator 字符串审查

`agent/curator.py` 全文唯一 Hermes 字眼 (line 262) 是 system prompt **给 auxiliary LLM 看的**, 员工 UI 永远看不到. **不需要 patch**.

但 Curator 的 `last_run_summary` 字段会被 Dashboard "整理记录"卡读 (集成方案步骤 4), 这个内容里如果含 `~/.hermes` 路径要走 dispatch scrub 过滤. 已在 `adapter.py:scrub_brand_in_result` 覆盖范围内, 不需要新加规则.

### 工时重估 (核心结果)

| 原文档预测 | 实际 dry-run 后 |
|---|---|
| AST patch 全断 → 阶段 B 重写 ~1.8 天 | **23 条规则微调** (删 2 + 改 1 + 加 7) ~30-60min |
| 文件 line offset 全漂 (0.12 lazy import) → sed 全断 | sed-based 还没测但字符串字面量稳定, 大概率也只是微调 |
| Curator 集成 ~2-3 天 | 物理隔离 (A.2 verify), Curator 不动 catfish skill, **0 代码改动** |

**重估总工时**: 阶段 B 从 ~1.8 天 → **~1 天**

### 风险等级再评估

| 模块 | 原 (5/4 文档 § 3) | 现 (5/5 dry-run) | 理由 |
|---|---|---|---|
| `apply_brand_patch.py` | 🔴 极高 | 🟡 中 | 86% 命中率, 失败的 3 条有清晰修法 |
| `rebrand.sh` (sed) | 🔴 高 | 🟡 中 (待测) | 跟 AST 同源 — 字面量保留度高, 大概率类似 |
| `string-map.yaml` | 🔴 高 | 🟡 中 (待测) | 同上 |
| `adapter.py:scrub_brand_in_result` | 🟡 中 | 🟢 低 | 已覆盖 Curator 输出范围 |
| Skills Hub vs Curator | 🟡 中 | 🟢 低 | A.2 verify: 物理隔离 |

整体风险等级: **🔴 高 → 🟡 中**. 升级**可控**.



---

## A.4 ✅ HERMES-UPGRADE-CHECKLIST.md

写完, 见 `docs/HERMES-UPGRADE-CHECKLIST.md`. 12 大节, 80+ 条机器可验 + 人工目检项. 升级时按章节顺序逐条勾.

涵盖: brand 字符串 (1) / tool registry 接口 (2) / dispatch scrub (3) / SOUL inject (4) / BL-E 系列 (5) / 主动闲聊 (6) / Quota+RBAC (7) / Skills Hub (8) / Curator (9) / SSO (10) / brand assets (11) / 性能 (12).

---

## A.5 ✅ go/no-go 决策书 (5/5 阶段 A 全完后, 给鸿波拍板)

### TL;DR

**推荐**: 阶段 B 的 brand patch 重构**今晚 / 明天可以开干**, 但 ⚠️ **不要在 demo 前 (5/14) 真升 hermes 0.12** — 阶段 B 是跟 hermes 解耦的, 单独做 0 风险; 真升级阶段 C 是 demo 后 5/15 起.

### A 阶段汇总

| 任务 | 结果 | 关键产出 |
|---|---|---|
| A.1 git tag 备份 | ✅ | `pre-hermes-upgrade-0.10` 标记 BL-MM2 ship 后状态 |
| A.2 verify | ✅ | catfish skill 跟 hermes Curator **物理隔离**, 集成方案从 5 步简化为 4 步 (取消自动 pin) |
| A.3 dry-run | ✅ | 86% 命中率 (19/22), 远好于预期; 阶段 B 工时 ~1.8 天 → ~1 天 |
| A.4 checklist | ✅ | 80+ 条回归项, 升级时按章勾 |

### 风险等级再评估

```
原 (5/4 文档):  apply_brand_patch.py 🔴极高 / Curator 🟡中 / 整体 🔴高
现 (5/5 dry-run): apply_brand_patch.py 🟡中 / Curator 🟢低 / 整体 🟡中
```

**阶段 B 工时**: ~1.8 天 → **~1 天** (3 子阶段简化为 2)

### 升级路径选项 (鸿波拍板)

#### 选项 1: 现在就走完阶段 B (brand patch 重构), demo 后 (5/15+) 真升级 — **推荐**

**今晚 / 明天 (5/5-5/6) 做的事** (跟 hermes 升级解耦, 0 demo 风险):
1. 改 23 条规则 (删 2 + 改 1 + 加 7), 让 apply_brand_patch.py 跑 0.12 0 个 SKIP/MISS — 30-60min
2. 写 `tests/hermes_brand_check.sh` 自动验证脚本 — 30min (从 catfish 启 → grep 不含 hermes 字样)
3. 改 `edge/branding/catfish` wrapper subprocess (原文档 § 5 阶段 B.1) — ~3h, awk filter stdout 替换字面量, 跟 hermes 升级彻底解耦
4. **不动机器**, 不真升级 hermes 0.10

**5/15+ demo 后做的事**:
- 真升级 hermes 0.10 → 0.12, 跑 CHECKLIST 12 节
- 写 Curator 集成 (4 步: 保守 config / consent toggle / Dashboard "整理记录"卡 / Phase 3 反向数据流)

**风险**: 极低. 阶段 B 重构纯前端 (catfish 侧), 不动 hermes 源码. demo 前留 9 天给阶段 B + 缓冲.

#### 选项 2: 现在就真升级 0.12 — **不推荐**

**理由不推荐**:
- 距 demo 9 天 (5/14)
- dry-run 虽然好, 但**没**测过 sed-based rebrand.sh (跟 AST 同源, 但需独立验证)
- 没测过 0.12 lazy import 是否破坏现有运行时行为
- 没测过 Curator 默认参数是否真的不动 catfish skill (虽然 verify 说会)
- 升级中真出问题 → demo 砸场

#### 选项 3: 完全不动 — **保守**

继续 0.10. 阶段 A 的产出 (checklist + 报告) 5/15+ 用. **0 风险但 0 进展**.

### 推荐路径执行清单 (选项 1)

如果鸿波选选项 1, 接下来要做:

#### 阶段 B.1 修 brand patch (今晚 / 明天, ~1.5h)
- [ ] 改 `apply_brand_patch.py`: 删 2 条 SKIP, 改 1 条 MISS, 加 7 条新规则 (cli.py 的 NOUS HERMES + Welcome + Goodbye + main.py Hermes Agent v + rl_cli.py Goodbye)
- [ ] 跑 dry-run 在 `/tmp/hermes-012` 上, 期望 0 SKIP 0 MISS
- [ ] 备份点: 改前再打个 git tag `phase-b-1-start`

#### 阶段 B.2 写 hermes_brand_check.sh (~30min)
- [ ] catfish 启动 → 抓头 100 行 stdout → grep 不含 Hermes/Nous Research/⚕/Goodbye!
- [ ] CI 集成: 加到 GitHub Actions / 本地 pre-commit

#### 阶段 B.3 wrapper subprocess (~2-3h)
- [ ] `edge/branding/catfish` 改 shell 入口, awk filter stdout
- [ ] 替换: Hermes Agent v* → 鲶鱼 v0.1.0, Hermes → 鲶鱼, Nous Research → 鲶鱼平台, ⚕ → 🐟
- [ ] 启动时自动发 `/skin warm-lightmode\n` 持久化 (hermes 0.10 不持久)
- [ ] 测试: 启动 → grep 仍不含 hermes (跟 source patch 双重保险)

#### 阶段 B.4 (5/15+ 跟 0.12 升级一起) Shell Hooks + 兜底 patch 裁短
- 0.11+ 的 lifecycle hook (on_session_start / pre_exit) 替代部分 source patch
- apply_brand_patch.py 从 468 行裁到 100-150 行 (兜底层)

### 跟 BL-MM3 / MM4 / MM6 的关系

阶段 B 完成后:
- BL-MM4 v1 (catfish_remember 版本卡) 可以开做, 不依赖 hermes 升级
- BL-MM6 (显式 feedback UI) 可以开做, 不依赖
- BL-MM3 (hermes memory_save 包版本化) 等真升级 0.12 后做 (5/15+)

### 时间表 (推荐 / 选项 1)

| 时间 | 动作 |
|---|---|
| **5/5 (今晚 / 下午)** | ✅ 阶段 A 全完, 写报告 (本文档) |
| **5/5 晚 / 5/6** | 阶段 B.1 (修 brand patch, 1.5h) + B.2 (check 脚本, 30min) |
| **5/6 ~ 5/8** | 阶段 B.3 (wrapper subprocess, 2-3h) — 可分散到几个晚上 |
| **5/9 ~ 5/14** | demo 准备 (BL-E14 PPT 吐槽 + 真机彩排 ×2 + 录视频). hermes 不动. |
| **5/14** | demo. 0.10 跑. |
| **5/15 ~ 5/22** | 真升级 hermes 0.12 + Curator 4 步集成 + 阶段 B.4 (Shell Hooks) + BL-MM3 |
| **5/23 ~ 6/5** | 完整回归 (CHECKLIST), Dashboard "整理记录"卡 (BL-MM4 v2), BL-MM6 |
| **6 月** | Phase 3 联邦数据流 (Curator → Hub 健康度) |

### 现在请鸿波拍板

**选哪个?**
1. **选项 1 (推荐)**: 现在动阶段 B (brand patch 重构) — ~4h 分散到几晚, 0 demo 风险, 5/15+ 真升级时能 reuse 全部成果
2. **选项 2 (不推荐)**: 现在真升级 0.12 — 9 天 buffer 太紧, 风险大
3. **选项 3 (保守)**: 不动, 5/15+ 再说

如果选 1, 可以立即起阶段 B.1 (修 brand patch 23 条规则, 30-60min).

---

最后更新: 2026-05-05 下午 (阶段 A 全完)


---

## 阶段 B.1 ✅ apply_brand_patch.py 23 → 27 条规则 (5/5 17:xx)

### 改动

- **删 2 条 SKIP**: `bootBanner.ts` 两条 (字符串迁去 branding.tsx 已被覆盖, 删了避免 SKIP 警告)
- **删 1 条 MISS**: `banner.py: agent_name skin fallback` (字面量 0.11+ 没了, 不致命 — agent_name 主路径还在 skin_engine.py 4 处)
- **加 6 条新规则** (cli.py 4 + main.py 1 + rl_cli.py 2):
  1. `cli.py: default skin banner line1` — `"⚕ NOUS HERMES - AI Agent Framework"` → `"🐟 鲶鱼 - 员工的数字副手"`
  2. `cli.py: default skin banner tiny_line` — `"⚕ NOUS HERMES"` → `"🐟 鲶鱼"`
  3. `cli.py: get_active_goodbye 入参` — `get_active_goodbye("Goodbye! ⚕")` → `get_active_goodbye("再见 🐟")`
  4. `cli.py: goodbye fallback 赋值` — `goodbye = "Goodbye! ⚕"` → `goodbye = "再见 🐟"`
  5. `main.py: --version 输出` — `print(f"Hermes Agent v{__version__} ({__release_date__})")` → `print(f"鲶鱼 v...")`
  6. `rl_cli.py: 退出语 ×2` (正常 + Interrupted)

### 顺序冲突防御

加的 cli.py 两条 NOUS HERMES 规则: 长字面量 (`"⚕ NOUS HERMES - AI Agent Framework"`) 必须放在短字面量 (`"⚕ NOUS HERMES"`) **前**, 避免短规则先 replace 把长字面量的子串改了, 长规则就 MISS. 已按顺序排列.

cli.py 的 goodbye 字面量用**精确赋值/调用**绑死 (`get_active_goodbye("Goodbye! ⚕")` / `goodbye = "Goodbye! ⚕"`), 不用裸 `"Goodbye! ⚕"`, 防跟 skin_engine.py 的 `'"goodbye": "Goodbye! ⚕"'` (含 key 前缀) 子串重叠.

### 验证结果

| hermes 版本 | 规则总数 | WILL | SKIP | MISS | 备注 |
|---|---|---|---|---|---|
| **0.12 (v2026.4.30)** | 27 | **27** | 0 | 0 | ✅ B.1 核心目标 |
| 0.10 (v2026.4.16) | 27 | 21 | 6 | 0 | ✅ 6 SKIP 是 ui-tui 还没引入 (0.11+ 才加), 是**预期**非 regression |

### git tag

- `phase-b-1-start` — 改前
- (B.1 commit pending)


---

## 阶段 B.2 ✅ 写 hermes_brand_check.sh + 抓到漏点修复 (5/5 17:xx)

### 产出

`tests/hermes_brand_check.sh` — 升级后自动验证 brand patch 没断的脚本.

两层验证:
1. **Layer 1 源文件 grep** (apply_brand_patch.py 应有的范围):
   - 21 条 `check_absent` 反向断言 (file 不含黑名单字符串) + 1 条 `check_present` (tips.py 应含鲶鱼字符串)
   - 自动跳过 ui-tui 目录 (0.10 没有, 0.11+ 才加)
2. **Layer 2 hermes 命令实测** (如果 `HERMES_BIN` 在 PATH):
   - `hermes --version` 输出脱敏
   - `hermes --help` 头 100 行脱敏 (warn 而非 fail, --help 文案部分员工看不到)

退出码: 0 = 全过, 1 = 有 fail, 2 = HERMES_DIR 不存在.

### 跑出来抓到 1 个漏点 (B.1 时漏的)

第一次跑 brand check 在 patched 0.12 上, 抓到:
```
FAIL  branding.tsx ASCII 大字已替换 — 'NOUS HERMES' 仍在 ui-tui/src/components/branding.tsx
```

定位: `ui-tui/src/components/branding.tsx:52` 有个 fallback `{t.brand.icon} NOUS HERMES`, 终端列数 < LOGO_WIDTH 时显示 (员工窗口太窄). B.1 删掉 bootBanner.ts 两条规则时**误判**这个字符串已被 branding.tsx 现有 2 条规则覆盖, 实际没有.

### 加 1 条新规则 (RULES 总数 27 → 28)

```python
(
    "ui-tui/src/components/branding.tsx",
    "{t.brand.icon} NOUS HERMES",
    "{t.brand.icon} 鲶鱼",
    "branding: ASCII fallback when terminal too narrow",
),
```

### Re-验证

revert → re-apply → re-check:
- **0.12 (v2026.4.30)**: 21 PASS / 0 FAIL / 0 WARN ✅
- 28 条规则 100% 命中

### B.2 价值证明

如果没写这个 check 脚本, B.1 这条漏点会等到员工把终端拉窄一次, 看到 "NOUS HERMES" 才报. CI/手动每次 patch 后跑 check 是阶段 B 的核心防线 — 防 patch 漂移. **强烈建议: 升级 hermes 后必跑.**


---

## 阶段 B.3 (修订) ✅ catfish 启动 self-heal brand patch (5/5 18:xx)

### 设计修订背景

原 5/4 文档 § 5 阶段 B.1 计划 awk filter hermes stdout, 实际不行:
- 0.11+ 的 React/Ink TUI 用 ANSI escape sequences 控制屏幕, awk 字面量替换会破坏光标位置
- 真改 ANSI-aware filter 工作量大, 价值低 (源码 patch 已经搞定 99% 的字符串)

**修订设计**: 把 wrapper 的核心价值从"实时 filter 字符串"改成"**启动前 self-heal brand patch**" — hermes 升级后 catfish 第一次启动**自动**检测 patch 漂移并重 apply, 员工无感.

### 改动

`edge/branding/catfish` 入口脚本:
- 加 `locate_catfish_repo()` 函数 — 从脚本所在位置往上找 catfish 仓库根, 或读 `CATFISH_REPO_ROOT` env
- 加 `ensure_brand_patch()` 函数 — 启动前跑 `tests/hermes_brand_check.sh` 验证, fail 时自动调 `apply_brand_patch.py --apply` 自愈, 100ms 量级 NOOP, 升级后第一次额外 1-2s
- 加 `cmd_brand_check` / `cmd_brand_fix` 子命令 — 员工/admin 手动跑
- `cmd_doctor` 加 "Brand patch" 板块, 一眼看 patch 状态
- 所有调用统一: `HERMES_DIR="${HERMES_DIR:-$HOME/.hermes/hermes-agent}"` 让 admin/CI 能跑别的 hermes 路径

`edge/hermes-fork/apply_brand_patch.py`:
- `HERMES_ROOT` 改读 `HERMES_DIR` env (默认 `~/.hermes/hermes-agent`), 让 admin/CI 场景能跑别处

### 容错矩阵

| 场景 | 行为 |
|---|---|
| catfish 仓库找不到 (员工装的二进制版) | 跳过 self-heal, 正常 exec hermes (不阻塞) |
| `hermes_brand_check.sh` 不存在 | 跳过 |
| `apply_brand_patch.py` 不存在 | 跳过 |
| `python3` 不在 PATH | 跳过 |
| `CATFISH_BRAND_PATCH_AUTO_HEAL=0` | 只警告不 apply (CI/admin 场景) |
| brand check fail + re-apply 也 fail | 警告 + 提示 admin 调试, **但不阻塞启动** (员工能进 hermes, 只是看到部分 hermes 字眼) |

### 端到端验证

测试场景: 模拟 hermes 升级后第一次 catfish 启动

```bash
# 1. revert (模拟 hermes 升级把 patch 抹了)
HERMES_DIR=/tmp/hermes-012 python3 apply_brand_patch.py --revert

# 2. 模拟员工敲 catfish (mock hermes binary)
HERMES_BIN=/tmp/mock-hermes HERMES_DIR=/tmp/hermes-012 \
  CATFISH_REPO_ROOT=<repo> \
  bash edge/branding/catfish --no-banner --version
# 输出:
#   brand patch 漂移 (hermes 升级了?), 自动修中…
#   brand patch 已自愈.
#   [mock hermes] called with: --version

# 3. 验证 banner.py 已 re-patch
grep "Hermes Agent v" banner.py    # → 0 occurrences
grep "鲶鱼 v" banner.py             # → 1 occurrence

# 4. 第二次启动 (NOOP 路径, 期望静默)
bash edge/branding/catfish --no-banner --version
# 输出: [mock hermes] called with: --version  (静默, 没 self-heal 信息)
```

✅ **全套链路工作**: detect 漂移 → 自动 re-apply → 透明 exec → 第二次启动 NOOP 静默

### catfish doctor 集成

```
[Brand patch]
    OK brand patch 完整, hermes 升级也安全
```

升级有问题时变成:
```
[Brand patch]
    WARN brand patch 有泄漏. 跑 catfish brand-fix 修复
```

### 为啥这是真实价值

升级 hermes 是**高频危险操作** (catfish 0.x ~ 1.0 至少 10 次, 之后每月 1-2 次):
- 没 self-heal: 员工每次升级后看到 hermes 字眼 → 报 bug → 工程手动 patch → 解释半天 → 鸿波背锅
- 有 self-heal: 员工敲 catfish, 第一次多 1-2s, 然后无感. 工程不背锅. 升级体验从"团队大事" 降到"无感"

### B.3 简化后比原计划好的地方

| 原计划 (5/4 文档 § 5 阶段 B) | B.3 修订 |
|---|---|
| 工时 ~1.8 天 | ~30min (单文件 shell + 2 行 Python env) |
| awk filter ANSI sequence 易破屏 | 完全不动 hermes 输出, 0 风险 |
| 治表 (filter 输出但 patch 仍漂移) | 治本 (patch 真修复, 不靠 filter 兜底) |
| TUI 模式可能假死 | 跟 TUI 完全解耦 |

### git tag

- `phase-b-1-start` — 改前
- (B.1 + B.2 + B.3 commit pending, 等 git lock 清掉)







---

最后更新: 2026-05-05 下午 (A.2 完成)
