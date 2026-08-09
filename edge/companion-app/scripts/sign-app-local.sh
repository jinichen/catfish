#!/usr/bin/env bash
# 给日常出包的 .app 签上 Developer ID —— 只签, 不公证 (8/9).
#
# ── 为什么需要这一步 ────────────────────────────────────────────────────
#
# macOS 的 TCC 授权 (完全磁盘访问 / 通讯录 / 麦克风…) 认的是应用的
# **Designated Requirement**:
#
#   ad-hoc 签名     → DR 只能退化成 cdhash → 每次重新构建都变 → 权限必掉
#   Developer ID    → DR 稳定不变          → 重建也认, 授权一次就够
#
# 8/9 之前有两条出包路径, 而快的那条不签名:
#
#   npm run tauri:build:arm64          → ad-hoc → 装上权限就掉
#   scripts/build-sign-notarize-arm64  → Developer ID + 公证 → 权限保住
#
# 日常迭代跑的都是快路径, 于是"每次重装都要重授一次完全磁盘访问"成了常态,
# 而且**没有任何提示** —— 员工只会发现邮件账号数又少了。这跟今天修的那批问题
# 是同一个形状: 有两条路, 走错的那条静默降级。
#
# 所以把签名焊进快路径。公证 (notarize + staple) 仍然只在
# build-sign-notarize-arm64.sh 里做 —— 它要上传 612MB 给 Apple 等好几分钟,
# 不该进日常循环。**签名保权限, 公证保分发**, 两件事分开。
#
# ── 找身份的顺序 ────────────────────────────────────────────────────────
#
#   1. $APPLE_SIGNING_IDENTITY / $SIGNING_IDENTITY (显式指定, 最高)
#   2. 钥匙串里**唯一**的 Developer ID Application (自动认, 免得每次记 env)
#   3. 找不到 → 大声警告后放行 (exit 0)
#
# 第 3 条为什么不 fail: 没证书的机器 (CI / 别的同事) 本来就只能出 ad-hoc 包,
# 让整个构建挂掉没有意义。但**签名失败**要 fail (exit 1) —— 那是"本来能签却
# 没签成", 静默产出 ad-hoc 包正是这个脚本要消灭的东西。
set -euo pipefail

APP_PATH="${1:-}"
if [[ -z "$APP_PATH" ]]; then
  echo "用法: bash scripts/sign-app-local.sh '<path/to/Xxx.app>'" >&2
  exit 2
fi
if [[ ! -d "$APP_PATH" ]]; then
  echo "❌ .app 不存在: $APP_PATH" >&2
  exit 2
fi

warn_unsigned() {
  cat >&2 <<EOF

──────────────────────────────────────────────────────────────────────
⚠️  这个包**没有** Developer ID 签名 (ad-hoc)
    装上去之后:
      · 完全磁盘访问等系统权限**每次重装都要重授**
      · 分发给别人时会被 Gatekeeper 拦 (要右键→打开)
    $1
──────────────────────────────────────────────────────────────────────

EOF
}

# ── 1) 显式指定 ──
IDENTITY="${APPLE_SIGNING_IDENTITY:-${SIGNING_IDENTITY:-}}"

# ── 2) 钥匙串里唯一的一个 ──
#
# ⚠ 不用 mapfile / readarray —— 那是 bash 4+ 的, 而 **macOS 自带的是 bash 3.2**
# (`/bin/bash --version` → 3.2.57)。`#!/usr/bin/env bash` 在装了 homebrew bash
# 的机器上是 5.x, 没装的就是 3.2 —— 用了就是"在我机器上好好的"。
# 改成 3.2 也认的 while-read 累加。
if [[ -z "$IDENTITY" ]]; then
  # `security find-identity` 每行形如:
  #   1) ABCD1234 "Developer ID Application: 某某 (TEAMID)"
  FOUND_LIST=""
  FOUND_N=0
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    FOUND_LIST="${FOUND_LIST}${line}
"
    FOUND_N=$((FOUND_N + 1))
  done < <(
    security find-identity -v -p codesigning 2>/dev/null \
      | grep "Developer ID Application" \
      | sed -E 's/.*"(Developer ID Application[^"]*)".*/\1/' \
      | sort -u
  )

  if [[ "$FOUND_N" -eq 1 ]]; then
    IDENTITY="$(printf '%s' "$FOUND_LIST" | head -1)"
    echo "→ 自动认到签名身份: $IDENTITY"
  elif [[ "$FOUND_N" -gt 1 ]]; then
    # 多个身份时**不猜** —— 签错身份 = DR 变了 = 权限照样掉, 而且更难查
    echo "⚠️  钥匙串里有 $FOUND_N 个 Developer ID Application, 不替你挑:" >&2
    printf '%s' "$FOUND_LIST" | sed 's/^/     /' >&2
    warn_unsigned "要签名请显式指定: APPLE_SIGNING_IDENTITY=\"<上面某一条>\" 再跑"
    exit 0
  fi
fi

if [[ -z "$IDENTITY" ]]; then
  warn_unsigned "本机钥匙串里没有 Developer ID Application 证书 —— 这台机器只能出 ad-hoc 包。"
  exit 0
fi

# ── 3) 真签 ──
#
# --deep: 连 Resources 里的 uv / catfish-calendar 一起签 (它们是 Mach-O,
#         不签的话 bundle 签名不完整, codesign --verify --deep 会红)。
# --options runtime: hardened runtime, 公证的硬性要求; 平时签上也无害,
#         省得两条路径签出来的东西不一样。
# --timestamp: 可信时间戳, 要联网。断网时降级成 --timestamp=none 并提示 ——
#         本地装用不上时间戳, 但公证需要, 所以要说清楚这个包不能拿去公证。
echo "=== 签名 .app (Developer ID, 不公证) ==="
if codesign --deep --force --options runtime --timestamp \
    --sign "$IDENTITY" "$APP_PATH" 2>/dev/null; then
  :
else
  echo "⚠️  带时间戳签名失败 (多半是断网), 降级成无时间戳重签" >&2
  echo "    → 这个包**不能拿去公证**; 要公证请联网后跑 build-sign-notarize-arm64.sh" >&2
  codesign --deep --force --options runtime --timestamp=none \
    --sign "$IDENTITY" "$APP_PATH"
fi

codesign --verify --deep --strict --verbose=2 "$APP_PATH"

# 这一条才是我们真正要的东西 —— DR 稳定, TCC 授权才能跨重建保留
if codesign --display --requirements - "$APP_PATH" 2>&1 | grep -q "Developer ID"; then
  echo "✅ 已签 Developer ID · Designated Requirement 稳定"
  echo "   → 完全磁盘访问等授权**跨重建保留**, 不用每次重授"
  echo "   (本次从 ad-hoc 切过来时仍需重授一次, 之后不用)"
else
  echo "❌ 签完了但 DR 里没有 Developer ID —— 权限还是会掉, 不能当成功" >&2
  exit 1
fi
