#!/usr/bin/env bash
# BL-FIX45 (5/11) — 错误自动恢复 UX (A: 401 auto re-auth, B: 500 auto fallback, C: skill session 重登).
#
# 鸿波 5/11 18:00 截图: 用 Gemini Flash-Lite 撞 500 红框, 还要手动换模型重试.
# 还有 OAuth token 过期 (401) 也是手动重启 Companion. 都是 UX 死角.
# 3 个场景一起救场, 不再让员工看红框愣神.

set -e
cd "$(dirname "$0")/.."

if [ -f .git/index.lock ]; then
    rm -f .git/index.lock
fi

git fetch origin main 2>&1 | tail -3 || echo "(fetch 失败, 继续)"

git add edge/companion-app/src/lib/chat.ts
git add docs/samples/eis-login-skill/SKILL.md
git add scripts/sync-bl-fix45.sh

git commit -m "BL-FIX45 (5/11): 错误自动恢复 UX (401 re-auth + 500 fallback + skill session-renewal)

鸿波 5/11 18:00 截图: Gemini Flash-Lite 撞 500 红框, 然后手动重发问题
才走通. UX 痛点 — 员工看红框不知道下一步, 还要自己想'我该换模型吗?'.
其它两种类似场景: OAuth token 过期 401, EIS session 跑到一半失效.

# A: 401 auto re-auth (chat.ts)

之前: gateway 返 401 → 红框 'HTTP 401: ...' → 员工得手动关 Companion / 重登.
现在: chat.ts 检测 401 → 调 tauri auth_login 命令 → 弹浏览器走 OAuth flow
→ 拿新 id_token → silent 重发原请求.

防死循环: _retryCounters.reauth 上限 1 次. 1 次后仍 401 报错 IdP 不可达.

UX 字符串: '⏳ catfish 登录已过期, 正在重新登录 (浏览器会弹一下)...'

# B: 500/502/503/504 auto fallback model (chat.ts)

之前: 红框 'HTTP 500: ...' → 员工手动换模型.
现在: chat.ts 检测 5xx → fetchCatalog → 找 catalog 下一个 mode='chat' +
reachable !== false + 不是当前模型 的候选 → silent 切 + 提示重试.

防死循环: _retryCounters.fallback 上限 2 次. 用完 retry 还 5xx 报 friendly error.

UX 字符串: '⚠️ 模型 \`Gemini-3.1\` 暂时不可达 (HTTP 500), 自动切到
            \`catfish-private-main\` 重试...'

# C: EIS session-renewal (eis-login SKILL.md)

之前: EIS session 中途失效 → skill 抓不到待办 → 整个 skill 报错.
现在: SKILL.md 加 'Session-renewal' 段, 描述:
  - 检测: get_url 看是否跳回 /login, 或 step 8 失败时 locate 找登录按钮
  - 恢复: 跑 step 2-7 子集 (screenshot → captcha → fill → click → 等跳)
  - 回到原步骤继续

防死循环: 单次 skill 执行 renewal 上限 2 次. 第 3 次报错 'session 续期失败'.

SOUL.md 同步: LLM 跑 skill 时遇到 session 失效, 直接 renewal 不问员工.

# 跟之前 fix 关系

  - FIX44 (浏览器自动化彻底修): 工具层
  - L8 (plan-only retry): LLM 行为层
  - FIX42 (历史截图折叠): prompt 层
  - **FIX45 (错误恢复 UX): 用户接口层** ← 把'红框看不懂'变成'silent 自动救场'

# 部署

仅改 Companion (chat.ts) + 文档 (SKILL.md). 不动 gateway / tool-bridge / 数据库.
Companion 重启后 chat.ts 重新打包加载 (vite dev) / 重新 build (production).

# 测试路径 (5/12 鸿波内网)

  1. 模拟 token 过期: localStorage.removeItem catfish_oauth_token / 清 keychain
     → 发消息 → 期望弹浏览器登录窗 (而不是红框 401)
  2. 模拟上游 500: 选 Gemini Flash-Lite (今天截图刚撞过) → 发消息
     → 期望自动切到 catfish-private-main 重试 (而不是红框 500)
  3. EIS session-renewal: 跑 skill, 中途手动从其它端踢登录 → 期望 skill 自动重登

# 已知边界

  - 网络断 (Tauri fetch 自己 abort): 当前还是 friendly error, 没自动重试. 网络层
    重试容易引入新坑 (不知道服务真挂没挂), 暂不在 FIX45 范围.
  - 401 reauth 后会弹浏览器, 鸿波可能正在打字被打扰. 长期看应 silent refresh
    (用 refresh_token 不弹浏览器), 但当前 OIDC PKCE 没接 refresh_token, 留 P2.
"

if [ -n "$(git status --porcelain)" ]; then
    echo "⚠ 还有 untracked / unstaged 改动."
fi

echo
git log origin/main..HEAD --oneline
echo
read -p "确认 push? [y/N] " yn
case $yn in
    [Yy]*)
        git push origin main && {
            echo "✅ done."
            echo
            echo "mac 接下来:"
            echo "  Companion 重启 (chat.ts 新 error handler 加载)"
            echo "  不需要 alembic / gateway 重启"
        }
        ;;
    *) echo "❎ 取消." ;;
esac
