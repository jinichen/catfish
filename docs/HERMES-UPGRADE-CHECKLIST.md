# Hermes 升级回归 Checklist

> 升级 hermes 后**必跑**的回归清单. 任一条 ❌, 不上线.
>
> **使用方法**: 升级完, 按章节顺序逐条勾, 不通过的写到底部 issue list, 修完再跑.
> 全 ✅ → 升级完成, 可以推 staging / 给客户用.

**首次创建**: 2026-05-05 (5/5 阶段 A.4)

**跑过的版本** (5/13 鸿波"记录有问题" 反馈后补落档):
- ✅ **5/7 0.10 → 0.12** (BL-D14.5, 全 PASS), 同步 ship git hooks 升级保护 + Curator 集成
- ✅ **5/13 21:01 0.12 → 0.13.0** (BL-HERMES-UPGRADE-013, 25 分钟内全 PASS):
  - `git checkout -f v2026.5.7` 强制切, HEAD = `498bfc7bc chore: release v0.13.0`
  - `apply_brand_patch.py --apply`: 26/27 patched, 1 MISS (良性 — 函数级 RULE `build_welcome_banner 已替换为极简版` DONE 覆盖了, banner.py line 418 实际渲染 `[bold]鲶鱼平台[/]`), 0 ERROR
  - `apply_brand_patch.py --verify`: exit=0 (4 关键文件全过)
  - `hermes --version`: "鲶鱼 v0.13.0 (2026.5.7)"
  - TUI startup banner 截图: 完全鲶鱼, 0 "Hermes Agent / Nous Research / ⚕" 泄露
  - catfish-gateway 跟 0.13 兼容: 7 model 加载 / 6 路由挂载 / 4 公网 LLM ✓ / `/v1/catalog` 200 OK
  - backend 909 测试 + tool-bridge 573 测试: 跟升级前一致
  - 所谓"4 件撞车点" 实际全部不撞 (hermes / catfish-gateway / tool-bridge 是 3 个独立进程, hermes 内置功能跟我们各组件平行存在)
  - 详见 `CHANGELOG.md` 5/13 段任务 #41 / #42

**升级版本**: 0.x → 0.x (每次升级填)

---

## 1. brand 字符串 (员工不看到 hermes / Nous Research / ⚕)

启动 catfish 后, **完整滚屏一次**, grep 不应命中:

```bash
# 启动 catfish, capture 头 100 行 stdout
catfish 2>&1 | head -100 > /tmp/catfish_boot.txt

# 必须返 0 命中
grep -E "Hermes|Nous Research|⚕|Goodbye!" /tmp/catfish_boot.txt
```

逐项:

- [ ] **启动 banner 顶部** 显鲶鱼 (不显 "Hermes Agent v0.x.x")
- [ ] **启动 banner 副标题** 显"鲶鱼平台 · 员工的数字副手" (不显 "Nous Research · Messenger of the Digital Gods")
- [ ] **NOUS HERMES 大字** 不出现 (0.12 新增 cli.py:1727 漏点, 必查)
- [ ] **状态栏 icon** 显 🐟 (不显 ⚕)
- [ ] **Welcome 提示** 显"欢迎回来. 输入消息或 /help 看命令." (不显 "Welcome to Hermes Agent")
- [ ] **退出语** 显"再见 🐟" (不显 "Goodbye! ⚕")
- [ ] **`catfish --version`** 输出"鲶鱼 v0.x.x" (不显 "Hermes Agent v")
- [ ] **HERMES-AGENT 大字 ASCII** 不出现 (apply_brand_patch.py 已清空)
- [ ] **蛇杖 ASCII** 不出现 (caduceus 15 行 braille)
- [ ] **TIPS 列表** 显鲶鱼 10 条 (不显 hermes 原版 "Try /help")
- [ ] **/skin 命令** 列表里**没有** Hermes 字样 (skin name 本身可保留)

## 2. tool registry 接口稳定 (升级不破 catfish 调用层)

- [ ] **`adapter.list_tools()`** 返 60+ 个 tool name (升级前后差不超过 ±5%, 防 hermes 砍 / 加大量内置)
- [ ] **`adapter.dispatch("memory_save", {...})`** 返成功 (hermes builtin 还能调)
- [ ] **`adapter.dispatch("catfish_today_summary", {})`** 返成功 (catfish native 还能调)
- [ ] **`adapter.dispatch("catfish_remember", {key:..., value:...})`** 返成功 + revision_count = 1 (BL-MM2 兼容)
- [ ] **`adapter.dispatch("memory_recall", {query:...})`** 返成功
- [ ] **`adapter.get_schema(name)`** 对每个 tool 返合法 OpenAI tool schema (无 type=null bug, BL-D11)
- [ ] **`adapter.get_emoji(name)`** 不抛 (升级新加的 tool 有 emoji 或返默认)
- [ ] **🔥 行为级 contract test (5/16 RCA 教训)** —
      `cd edge/tool-bridge && pytest tests/test_memory_store_injection.py -v`
      期望 8/8 全过. 守住:
        * memory 工具 dispatch 时 kw['store']=MemoryStore 注入 (5/3 hermes 升 0.13
          时这条断了 6 周没察觉, 因为只测"调用成功" 没测"真写盘")
        * todo 工具 per-session TodoStore 隔离
        * MemoryStore / TodoStore init fail 兜底
      升级**任何 hermes 版本**后必跑这条. 不过 = 立即回滚, 别上线.
      详见 `RCA-MEMORY-PLUMBING-20260516.md`.

## 3. dispatch 响应 brand 脱敏 (~/.hermes 字样不漏给员工)

调几次 `memory_*` / `skill_*` 工具, 看返回 message:

- [ ] **memory_save 返回**里 `~/.hermes/memories/...` → 已脱敏成"鲶鱼本机存储" 或类似 (`scrub_brand_in_result`)
- [ ] **memory_recall 返回**里若引 hermes 路径, 同上脱敏
- [ ] **skill_view 返回**里 `~/.hermes/skills/...` 已脱敏
- [ ] **0.12 新加路径** `display_hermes_home()` 输出 (含 profile name) 也覆盖
- [ ] **Curator last_run_summary** 输出里 hermes 字样脱敏 (走 dispatch scrub 即可)

## 4. SOUL.md 注入到 system prompt (品牌铁律 + 情绪 + 命名权 + BL-MM1 + BL-MM5)

- [ ] **新 session 第一条 chat** 后, 抓 gateway log 看 system prompt 含:
  - [ ] "你是鲶鱼" 自我介绍段
  - [ ] § 复述模式 (硬事实 quote)
  - [ ] § 凭据 ref 纪律
  - [ ] § BL-MM1 记忆覆盖纪律 (含 BL-MM2 段)
  - [ ] § BL-MM5 主动学习员工偏好
  - [ ] § BL-E11 命名权 preamble (员工自定义名)
  - [ ] § BL-E19 情绪规约
- [ ] **session_facts inject** 末尾追加 (如果 `~/.catfish/session_facts.json` 非空)
- [ ] **多 revision 的 fact** 显示 "上次值: X (已更新 N 次)" 提示 (BL-MM2)

## 5. BL-E 系列功能

- [ ] **BL-E11 命名权**: 改名 → LLM 自我介绍用新名, agent_name 状态栏跟随
- [ ] **BL-E15 专注模式**: Cmd+Shift+F → 切伪 IDE 视图, 再按一次切回
- [ ] **BL-E16 关系建立 RelationCard**:
  - [ ] 显"鲶鱼对你的印象", recent_entries 5+ 条 (BL-E16 修复后从 30 条拉)
  - [ ] 标题用 LLM `### 主题`, 不显 session_id
  - [ ] 同 meta 去重, 留最长的 body
  - [ ] 默认显摘要 100 字 + 点击展开
  - [ ] today_count = 0 时不显 "0" (RelationCard "0" bug fix)
  - [ ] 滚动条能往下滚, 看到老的条目 (max-height 360 + overflow)
  - [ ] **清空印象**按钮 → rm 后再聊几次能正常长回来
- [ ] **BL-E19 情绪**: 鲶鱼回话时偶尔带情绪 (累 / 高兴), 不机械

## 6. 主动闲聊 (proactive)

- [ ] **9:30 / 14:00 / 17:30** macOS 通知触发 (3 个时间点)
- [ ] 通知点击 → 浮窗弹出 + 鲶鱼主动开场
- [ ] 主动闲聊用 `proactive_starter` 候选模型, 不耗员工 quota (BL-F17 X-Catfish-Internal)

## 7. Quota / RBAC

- [ ] **chat 超 per_user_minute** → 返 429 + 友好话术"鲶鱼累了, 1 分钟后再来"
- [ ] **chat 超 per_user_day** → 同上, 话术按"今日额度满"
- [ ] **summarizer 不耗员工 quota** (X-Catfish-Internal: true header skip)
- [ ] **manager 角色** Dashboard 显本部门员工概览卡
- [ ] **admin 角色** Dashboard 显公司级 dashboard + skill 健康度
- [ ] **employee 角色** 不显管理面卡

## 8. Skills Hub

- [ ] **catfish_skill_install** 装本机 skill 到 `catfish/skills/<ns>/<name>/` ✅
- [ ] **catfish_skill_install 不写** `~/.hermes/skills/.hub/lock.json` (物理隔离, A.2 verify)
- [ ] **BL-C13 dedup 检查** 装相似 skill 时阻挡, 提示 force_install
- [ ] **skill_lifecycle** 4 步基础 (init / patch / backup / delete) 跑通
- [ ] **catfish_run_skill** 调本机 skill 返成功

## 9. Curator (0.12 新加, 集成方案 4 步)

- [ ] **`~/.hermes/config.yaml`** 含 `curator.enabled: true` + 4 个保守参数 (interval=168 / idle=4 / stale=60 / archive=180)
- [ ] **catfish skills (`catfish/skills/...`)** Curator 一个不动 (物理隔离验证)
- [ ] **agent-created skills (`~/.hermes/skills/...`)** 跑一次 Curator, 没 hub-lock 的 skill 走正常 active/stale/archived 流转
- [ ] **archive 是 reversible** — `restore_skill` 能还原
- [ ] **pin 机制** — set_pinned(name, True) 后 Curator 跳过该 skill
- [ ] **Onboarding consent** toggle 不勾 → catfish 写 `curator.enabled: false`, Curator 不跑
- [ ] **Dashboard "小鲶整理记录"卡** (Phase 2.5 步骤 4, 升级时不强求, 后做也行)

## 10. SSO / Auth

- [ ] **catfish login** 走 OAuth flow, Keychain 拿 access token
- [ ] **catfish whoami** 返当前用户 + 部门
- [ ] **catfish logout** 清 Keychain
- [ ] **Hermes 升级不动 SSO 模块** — auth 命令仍 work

## 11. brand assets (favicon / app icon / mascot)

- [ ] **应用 dock icon** 是鲶鱼, 不是 hermes 默认
- [ ] **macOS menubar 托盘** 鲶鱼 SVG
- [ ] **catfish-avatar.svg** 在 RelationCard 标题前显示
- [ ] **SSO login page** 显鲶鱼品牌 (不显 hermes 字样)

## 12. 性能 (0.12 lazy import 副作用)

- [ ] **catfish 冷启动** 不显著变慢 (基线 ~3-5s, 升级后允许 ±20%)
- [ ] **第一条 chat** 响应不显著变慢
- [ ] **gateway** 启动时间不显著变化

---

## Issue list (升级时遇到的没过关的项)

```
[ ] (待填)
```

---

## 升级时机 / 回滚

如多于 3 条 ❌ 或任何 P0 ❌ (1.x / 2.x / 3.x / 4.x):

```bash
git reset --hard pre-hermes-upgrade-0.10
```

回滚后排查问题, 修补丁, 重跑.

---

## 自动化脚本 (跑全套用)

(待写, 阶段 B.4)

```bash
# tests/hermes_upgrade_check.sh — 调用每条机器可验的项
./tests/hermes_upgrade_check.sh
```
