#!/usr/bin/env bash
# BL-Q3-ARCHIVE fix1 — User.email → User.sub (5/11 mac 实测).
#
# User dataclass 字段是 sub (= email per 决策 3), 不是 email. 之前写错
# user.email 让 chat 全部 500.

set -e
cd "$(dirname "$0")/.."

if [ -f .git/index.lock ]; then
    rm -f .git/index.lock
fi

git fetch origin main 2>&1 | tail -3 || echo "(fetch 失败, 继续)"

git add central/llm-gateway/src/catfish_gateway/app.py
git add central/llm-gateway/src/catfish_gateway/tool_archive/router.py
git add scripts/sync-bl-q3-archive-fix1.sh

git commit -m "BL-Q3-ARCHIVE fix1 (5/11): User.email → User.sub

mac 实测 chat 500: 'User' object has no attribute 'email'.

User dataclass (auth/base.py) 字段是 sub (= email, AUTH 决策 3 决定 sub
就是 email, 跨 IdP 通用), 不是 email. 之前 BL-Q3-ARCHIVE 接入时写错.

3 处改:
  - app.py prepare_tool_messages(user_email=user.sub)
  - tool_archive/router.py _check_access 校验
  - tool_archive/router.py read_archive log

无单测改动: 测试本来传 user_email='t@x.com' 字符串, 不构造 User 对象.
"

if [ -n "$(git status --porcelain)" ]; then
    echo "⚠ 还有 untracked / unstaged 改动, 跳过."
fi

echo
git log origin/main..HEAD --oneline
echo
read -p "确认 push? [y/N] " yn
case $yn in
    [Yy]*) git push origin main && echo "✅ done — 重启 gateway 即生效" ;;
    *) echo "❎ 取消." ;;
esac
