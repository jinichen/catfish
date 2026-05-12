#!/usr/bin/env bash
# BL-MM9-FREEZE (5/12 鸿波拍板): 教学→凝固→复用闭环.
#
# # 鸿波 5/12 反思
#
# 上午 EIS 教学撞死循环, 鸿波看了一眼: "你这样改代码的方式，是不是就算这个过了，
# 其他的也不能达到我们预设的目标？" 之后又说 "不要管 5/14 的事，你只要达到我们
# 的目标，这几天如果没用的代码就要删掉，不要干扰我们的目标".
#
# 目标 = catfish 真正的卖点 = BL-MM9 skill learning = **员工跑通一遍, 鲶鱼凝固
# 成 skill, 下次秒开**.
#
# 之前我做的 SKILL.md (skills/department/eis-login/) 是手工凝固 — 我代替了
# 鲶鱼应该自动做的事. 这次回滚, 真把"自动凝固"管道做出来.
#
# # 改了什么
#
# ## 删除 (反目标 — 会让鲶鱼跳过"教学"直接调凝固版, 自动凝固永远跑不起来)
#
#   - skills/department/eis-login/SKILL.md (我手工凝固的, 5/12 上午写)
#   - skills/personal/eis-auto-login/ + eis-auto-login-and-todo/ (LLM 失控
#     创建在错路径的旧 stub)
#   - docs/samples/eis-login-skill/SKILL.md (5/11 草稿)
#   - scripts/sync-bl-fix47-procedural-skill.sh (procedural skill 思路废弃)
#
# ## 回滚 (BL-FIX47 procedural 思路 — 让 LLM 看 SKILL.md 走流程 也是反目标)
#
#   - edge/tool-bridge/.../catfish_tools.py: run_skill 4344 段 procedural 分支
#     回滚, 改回严格要求 script.py 存在. 没 script.py 就提示"先走教学+凝固".
#   - 同时 catfish_run_skill tool description 移除 BL-FIX47 procedural 返回说明.
#
# ## 减法 — CATFISH_LEAN_INJECT 总开关
#
# 教学场景下, 鲶鱼应该专注学一个系统, 不需要看员工画像 / 7 天历史 / 反馈 /
# stats_guard / retry-hint. 这些 inject 互相打架, prompt 30K+ 字符 LLM 不可
# 预测. 加 env 开关 CATFISH_LEAN_INJECT=1, 关掉一群:
#
#   - inject_session_facts
#   - inject_stats_guard
#   - inject_skill_guard
#   - inject_session_history
#   - inject_employee_journal
#   - inject_feedback
#   - tool_retry_hint (BL-A1.2)
#   - self_critique (BL-A1.3)
#   - duplicate_tool_call_guard (BL-FIX24)
#   - BL-FIX23 L8 plan-only retry
#
# 保留 (核心): inject_identity / inject_skills_catalog / inject_session_goal
# (/goal Ralph loop) / session_meta / prompt_security / BL-Q3-ARCHIVE.
#
# 默认 LEAN=0 = 老行为, 不破坏现有部署. 教学路径起 gateway 时 export
# CATFISH_LEAN_INJECT=1.
#
# ## 新建 — 教学→凝固管道
#
#   1. edge/tool-bridge/.../trace_recorder.py (新, ~210 行)
#      - 拦截白名单工具 (catfish_browser_* / recognize_captcha / browser_locate)
#      - 写 ~/.catfish/traces/active.jsonl, 一行一个 tool 调用
#      - 大字段截断 (8K 上限), 失败永不抛
#      - 提供 read_traces / session_summary / rotate API
#
#   2. edge/tool-bridge/.../catfish_tools.py dispatch_native 加 wrapper
#      - is_recorded(name) → 包 trace 记录 → 调 _dispatch_native_inner
#      - 其它 tool 原路径
#
#   3. edge/tool-bridge/.../skill_freeze.py (新, ~470 行) — 凝固引擎
#      - freeze_skill(name, namespace, description, ...) 读 trace 模板化生成
#      - 模板化逻辑:
#         * goto / fill / click → page.<op> 经 dispatch_native 路由
#         * fill secret_ref → 透传给 catfish_browser_fill 内部 resolve
#         * fill 明文密码 (#pwd selector + 像密码) → 拒绝凝固 (安全)
#         * recognize_captcha → 自动包 retry loop (识别失败时刷图重识)
#         * fill captcha 字段 (text 跟上一步 recognize result 一致) → 用变量
#         * snapshot / find_by_text / locate → skip (LLM-only)
#      - 参数推断: username (从第一个非密码 fill) / password_ref (从
#        secret_ref) / max_captcha_retry (固定 3)
#      - 落地 catfish/skills/<ns>/<name>/{script.py, SKILL.md}
#      - 自动 spawn install_to_hermes.sh 同步到 ~/.hermes/skills/productivity/
#
#   4. 新 3 个 tool (catfish_tools.py CATFISH_NATIVE_TOOLS + dispatch):
#      - catfish_freeze_inspect — 看 trace 状态
#      - catfish_freeze_skill — 凝固成 skill
#      - catfish_freeze_rotate — 凝固完归档 active trace
#
# # 端到端验证 (沙箱已通过)
#
# 模拟 8 步 EIS 教学 trace → freeze_skill → 产出 script.py 通过 ast.parse
# syntax 检查 + SKILL.md 正确. 参数推断对:
#   username: str = 'chenhb'  (来自 #name fill text)
#   password_ref: str = 'keychain://eis_password'  (来自 #pwd secret_ref)
#   max_captcha_retry: int = 3
#
# # 闭环演示
#
# 步骤 1 (教学): 员工说"我教你 EIS 登录" → LLM agent 调 catfish_browser_*
#                一步步 → trace_recorder 自动记
# 步骤 2 (凝固): 员工说"凝固成 eis-login skill" → LLM 调:
#                catfish_freeze_skill(name='eis-login', namespace='department',
#                                     description='EIS 登录看待办')
#                → 模板化生成 script.py + SKILL.md → install_to_hermes
# 步骤 3 (复用): 员工说"上 EIS 看待办" → LLM 调:
#                catfish_run_skill('department/eis-login', {'username': 'chenhb'})
#                → script.py 跑全套 → 返 {ok, todo_count, ...}
#
# # 部署
#
# gateway 重启 + tool-bridge 重启读新代码. 不动 PG.
# 教学路径起 gateway 时 export CATFISH_LEAN_INJECT=1.

set -e
cd "$(dirname "$0")/.."

if [ -f .git/index.lock ]; then
    rm -f .git/index.lock
fi

git fetch origin main 2>&1 | tail -3 || echo "(fetch 失败, 继续)"

# 1) 删除反目标文件 (git rm 触发本地删)
echo "─── git rm 反目标文件 ───"
git rm -rf skills/department/eis-login 2>&1 | tail -3 || echo "  (skill 不存在, skip)"
git rm -rf skills/department/eis-login-test 2>&1 | tail -3 || echo "  (test1 不存在, skip)"
git rm -rf skills/department/eis-login-v2test 2>&1 | tail -3 || echo "  (test2 不存在, skip)"
git rm -rf skills/personal 2>&1 | tail -3 || echo "  (personal 不存在, skip)"
git rm -rf docs/samples/eis-login-skill 2>&1 | tail -3 || echo "  (sample 不存在, skip)"
git rm -f scripts/sync-bl-fix47-procedural-skill.sh 2>&1 | tail -3 || echo "  (sync 不存在, skip)"

# 同步清 hermes 端可能残留的同名 skill
HERMES_SKILL=~/.hermes/skills/productivity/catfish-eis-login
if [ -d "$HERMES_SKILL" ]; then
    rm -rf "$HERMES_SKILL"
    echo "  cleaned $HERMES_SKILL"
fi

# 2) 改动的现有文件
echo "─── git add 改动的文件 ───"
git add central/llm-gateway/src/catfish_gateway/app.py
git add edge/tool-bridge/src/catfish_tool_bridge/catfish_tools.py

# 3) 新文件
echo "─── git add 新文件 ───"
git add edge/tool-bridge/src/catfish_tool_bridge/trace_recorder.py
git add edge/tool-bridge/src/catfish_tool_bridge/skill_freeze.py
git add scripts/sync-bl-mm9-freeze.sh

# 4) 文档 (如果存在改动)
[ -f CHANGELOG.md ] && git add CHANGELOG.md
[ -f docs/FEATURE-TRACKS.md ] && git add docs/FEATURE-TRACKS.md

git commit -m "BL-MM9-FREEZE (5/12 鸿波拍板): 教学→凝固→复用闭环 + 删反目标文件

# 鸿波 5/12 反思 (引发本 commit)

上午 EIS 教学 5+ 轮死循环, 鸿波: '你这样改代码的方式, 是不是就算这个过
了, 其他的也不能达到我们预设的目标?' 之后: '不要管 5/14 的事, 你只要达
到我们的目标, 这几天如果没用的代码就要删掉.'

目标 = BL-MM9 skill learning = **员工跑通一遍, 鲶鱼自动凝固成 skill,
下次秒开**. 之前我手写 skills/department/eis-login/SKILL.md = 我代笔
凝固, 不是鲶鱼自己干. 这次真把'自动凝固管道'做出来.

# 删 (反目标)

  - skills/department/eis-login/ — 我 5/12 上午手工凝固的, 让鲶鱼一看
    hermes 里已经有 catfish-eis-login → 不触发'我要学一遍' → 自动凝固
    永远跑不起来
  - skills/personal/eis-auto-login*/ — 5/12 上午 LLM 失控在错路径创建
  - docs/samples/eis-login-skill/ — 5/11 草稿, 已被取代
  - scripts/sync-bl-fix47-procedural-skill.sh — procedural skill 思路废弃

# 回滚 BL-FIX47

procedural skill 思路 (让 LLM 看 SKILL.md 走流程) 也反目标 — 鼓励
ad-hoc 不凝固. catfish_tools.py run_skill 4344 段恢复成严格要求 script.py
存在. 没 script.py 提示员工走教学+凝固路径.

# 减法 — CATFISH_LEAN_INJECT 总开关

教学场景关掉一群 inject (打架 + prompt 爆 30K):
  inject_session_facts / stats_guard / skill_guard / session_history /
  employee_journal / feedback / tool_retry_hint / self_critique /
  duplicate_tool_call_guard / BL-FIX23 L8 retry

保留核心: identity / skills_catalog / session_goal / session_meta /
prompt_security / BL-Q3-ARCHIVE.

默认 LEAN=0 不破坏老行为. 教学起 gateway 时 export CATFISH_LEAN_INJECT=1.

# 新建 — 教学→凝固管道

## 1) trace_recorder.py (新, ~210 行)

拦截白名单工具 (catfish_browser_* / recognize_captcha / browser_locate),
顺序写 ~/.catfish/traces/active.jsonl. 大字段 8K 截断, 失败永不抛.
read_traces / session_summary / rotate API.

## 2) catfish_tools.py dispatch_native trace wrapper

is_recorded(name) → 包 trace → 调 _dispatch_native_inner (原 dispatch
body 改名). 其它 tool 原路径.

## 3) skill_freeze.py (新, ~470 行) 凝固引擎

freeze_skill(name, ns, description, ...) 读 trace 模板化生成:
  - goto/fill/click → 经 dispatch_native 路由 (不自己开 Playwright, 复用
    catfish_browser_* 内部 CDP connect / secret_ref resolve / SSRF deny)
  - fill secret_ref → 透传, 明文不进 script.py
  - fill 明文密码 (#pwd selector + 像密码) → 拒绝凝固 (安全)
  - recognize_captcha → 自动包 retry loop
  - fill captcha (text 跟上一步 recognize result 一致) → 用 captcha_text 变量
  - snapshot/find_by_text/locate → skip (LLM-only, script 不需要)

参数推断: username (第一个非密码 fill) / password_ref (secret_ref) /
max_captcha_retry (固定 3).

落地 catfish/skills/<ns>/<name>/{script.py, SKILL.md} + spawn
install_to_hermes.sh 自动同步 hermes 端.

## 4) 3 个新 tool

  - catfish_freeze_inspect — 查 trace 状态, 凝固前 sanity check
  - catfish_freeze_skill — 凝固成 script.py + SKILL.md
  - catfish_freeze_rotate — 凝固完归档 active trace, 防下次教学撞

# 端到端验证

沙箱模拟 8 步 EIS 教学 trace → freeze_skill → 产出 script.py 通过
ast.parse syntax 检查. 参数推断对:
  username: str = 'chenhb'
  password_ref: str = 'keychain://eis_password'
  max_captcha_retry: int = 3
captcha retry loop / secret_ref 透传 / skip LLM-only 全对.

# 闭环演示

1. (教学) 员工: '我教你 EIS 登录' → LLM agent 调 catfish_browser_* →
   trace_recorder 自动记
2. (凝固) 员工: '凝固成 skill' → LLM 调 catfish_freeze_skill(
   name='eis-login', namespace='department', description='EIS 登录看待办')
   → script.py + SKILL.md 落地 + install_to_hermes
3. (复用) 员工: '上 EIS 看待办' → LLM 调 catfish_run_skill(
   'department/eis-login', {'username': 'chenhb'}) → script.py 跑全套 →
   返 {ok, ...} → LLM 报告

# 部署

  - gateway 重启 (改了 app.py)
  - tool-bridge 重启 (改了 catfish_tools.py + 新 trace_recorder /
    skill_freeze)
  - 教学路径起 gateway 时 export CATFISH_LEAN_INJECT=1
  - 不动 PG

# 后续 (5/13 之后)

  - 真测一遍: 鸿波内网起 LEAN mode → 教 EIS 登录 → freeze →
    run_skill 跑通
  - 失败 trace 处理 (当前只过滤 ok=true 步骤, 但教学失败序列要不要
    保留供分析)
  - 多 LLM agent session 并发录 trace 隔离 (当前是单文件 append)
  - SKILL.md 后续 inline 修改 → 触发 trace replay 重测 (BL-MM10 范围)
"

if [ -n "$(git status --porcelain)" ]; then
    echo "⚠ 还有 untracked / unstaged 改动 — 检查一下:"
    git status --porcelain
fi

echo
git log origin/main..HEAD --oneline
echo
read -p "确认 push? [y/N] " yn
case $yn in
    [Yy]*) git push origin main && {
        echo "✅ push done."
        echo
        echo "── 下一步 (本地起服务) ──"
        echo
        echo "  1) gateway: export CATFISH_LEAN_INJECT=1; python -m catfish_gateway.app"
        echo "  2) tool-bridge: pkill -9 -f catfish_tool_bridge && sleep 8"
        echo "     (Companion 会自动拉起 tool-bridge)"
        echo
        echo "── 验证闭环 (新会话) ──"
        echo
        echo "  教学: '我教你登 EIS, 第一步 catfish_browser_goto(url=...)' ..."
        echo "  查 trace: '查 trace 状态' → LLM 调 catfish_freeze_inspect"
        echo "  凝固: '凝固成 eis-login skill, 描述是 EIS 登录看待办'"
        echo "       → LLM 调 catfish_freeze_skill(name='eis-login', ...)"
        echo "       → script.py + SKILL.md 落 catfish/skills/department/eis-login/"
        echo "       → hermes 同步 ~/.hermes/skills/productivity/catfish-eis-login/"
        echo "  复用: 新会话 '上 EIS 看待办' → LLM 调 catfish_run_skill"
    } ;;
    *) echo "❎ 取消." ;;
esac
