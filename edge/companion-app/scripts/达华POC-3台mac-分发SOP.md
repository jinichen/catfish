# 达华 POC · 3 台 mac 分发 SOP

**规模**: 2 台 Apple Silicon + 1 台 Intel
**版本**: v0.18.1 (2026-07-18 · 7/18 code fix 全 mirror)
**目的**: 让 3 台 mac 员工装 Companion + verify chat 通

---

## 7/18 关键 code fix (已 build 进最新 dmg)

| Task | Fix | 员工场景影响 |
|------|-----|-----|
| #60 | Companion `hermes_api.enabled=false` 默认 · chat 直连 gateway | Onboarding 填达华 IP 即通 · 无需额外 config |
| #63/#64 | CSP `connect-src` 严格 + Rust reqwest 代理 (`http_proxy`) | 员工输**任意远端 IP** · WebView 不再拦 (老版本 dmg 会挂 `TypeError: Load failed`) |
| #66 | gateway 支持双 audience `catfish-companion,catfish-gateway` | Companion Rust 返 id_token · aud=companion · gateway 认 (老版本单值 aud=gateway 会 401) |
| #68 | gateway image `0.1.1` 加 orjson | litellm mcp code path 不再 502 |
| #1 | useChat 分支补 persist user msg (gateway 直连场景) | 切走 session 再切回 · **user 气泡保留** (老版本丢) |
| #3 | WeChat QR/poll 强走 hermes 8642 无视 `hermes_api.enabled` | WeChat 绑定不再 404 |
| #5 | WeChat approve code case-insensitive | approve 不再 "code 找不到" |
| #72 | 面板改 IP 同步写 `~/.hermes/.env` `CATFISH_GATEWAY_URL` | catfish plugin 用新 URL · memory/role 正确 |
| #8 | **⚠ email-agent CLI 未 bundle · 员工场景 blocker** | 员工装完 dmg 后 · 邮件 tab 挂 · **需 IT 手工装** (见下) |

---

## 分发前 · 你 (陈鸿波) mac 上准备

### 1. 确认 dmg 已 build 好

```bash
ls -la ~/person_task/catfish/edge/companion-app/src-tauri/target/release/bundle/dmg/*.dmg
ls -la ~/person_task/catfish/edge/companion-app/src-tauri/target/x86_64-apple-darwin/release/bundle/dmg/*.dmg
```

期望:
- `Catfish Companion_0.18.0_aarch64.dmg` 128 MB
- `Catfish Companion_0.18.0_x64.dmg` 122 MB

### 2. Copy 到 Downloads · 上传 Nextcloud

```bash
mkdir -p ~/Downloads/catfish-达华POC-0717/
cp ~/person_task/catfish/edge/companion-app/src-tauri/target/release/bundle/dmg/Catfish\ Companion_0.18.0_aarch64.dmg ~/Downloads/catfish-达华POC-0717/
cp ~/person_task/catfish/edge/companion-app/src-tauri/target/x86_64-apple-darwin/release/bundle/dmg/Catfish\ Companion_0.18.0_x64.dmg ~/Downloads/catfish-达华POC-0717/
```

上传 Nextcloud `paixiao2.duckdns.org:9997/catfish-达华POC/`.

### 3. 你 mac 上先 verify aarch64 dmg (10 min)

```bash
# 装
open ~/Downloads/catfish-达华POC-0717/Catfish\ Companion_0.18.0_aarch64.dmg
# 拖到 Applications

# 打开
open -a "Catfish Companion"
```

**Onboarding 填**:
- gateway_url: `http://<达华内网服务器 IP>:8999`
- identity_url: `http://<达华内网服务器 IP>:8998`

**SSO 登录 · 试 chat "hi"**:
- ✅ 通 → POC dmg 就绪 · 分发
- ❌ 401 → gateway 侧配置问题 · 不是 dmg 问题
- ❌ 其他错误 → 看应用 log: `~/Library/Logs/com.catfish.companion/*.log`

---

## 3 台员工分发 · 每台 15 min

### 员工 1/2/3 装机步骤

**给 2 台 Apple Silicon 员工**: `Catfish Companion_0.18.0_aarch64.dmg`
**给 1 台 Intel 员工**: `Catfish Companion_0.18.0_x64.dmg`

**装机步骤** (每台员工机):

1. 从 Nextcloud 下 dmg (对应架构)
2. 双击 dmg · 拖 `Catfish Companion.app` 到 `Applications`
3. **首次打开** · 若 macOS 拦"来源未验证":
   - 系统设置 → 隐私与安全性 → "仍要打开"
   - 或 Terminal: `sudo xattr -rd com.apple.quarantine /Applications/Catfish\ Companion.app`
4. Companion 打开 · Onboarding 填服务器地址 (跟你 verify 时同款)
5. SSO 登录 → 试 chat "hi" → 收到回复即 POC 通

---

## ⚠ email-agent CLI 装机 (Task #8 · 员工场景临时手工)

Companion 邮件 tab 挂 · 提示 "catfish-email CLI 没装" · **每台 mac 员工机手工一次** (装完永久 · 5 min):

**选项 A · IT 装 (推荐)**:
IT 员工 · 从 `~/person_task/catfish/edge/email-agent/` (若有源码) 或**员工 mac 上**装:

```bash
# 员工机上 (IT 远程 SSH 或亲装):
# 1. 装 catfish-email CLI · 用 hermes venv 里的 python
export PATH=~/.hermes/hermes-agent/venv/bin:$PATH
python -m pip install -U catfish-email
# 或若无 catfish-email pypi 包 · IT 从 hermes 里带的 CLI 复制:
mkdir -p ~/.local/bin
ln -sf ~/.hermes/hermes-agent/venv/bin/catfish-email ~/.local/bin/catfish-email

# 2. verify
which catfish-email
catfish-email --version
```

**选项 B · Companion 邮件功能不用 (POC 期跳过)**:
邮件 tab 关掉不看 · POC chat 主要功能不受影响.

**长期 fix · 待做** (Task #8): dmg build 时 bundle email-agent CLI + install.sh · Companion Rust 首启 hooks 自动装. 员工机零手工. rebuild dmg 后新版本 auto install.

---

## 面板改 IP 后 · 必做 2 步 (7/18 Task #72)

若客户 IT 后期改中央服务器 IP · 或员工机换网络:
1. Companion → 仪表盘 → **服务器配置 → 改** · 填新 IP · 保存
2. 提示后**必须做**:
   - 终端: `hermes gateway stop && hermes gateway start` (让 catfish plugin 用新 `~/.hermes/.env` `CATFISH_GATEWAY_URL`)
   - Companion: quit 重开 (前端 yaml 只读一次)
3. 若 identity 也变 · `rm -rf ~/.catfish/oauth` · 重开 Companion · 弹浏览器 SSO 拿新 JWT


**装机过程会自动跑 install.sh** (装 hermes-agent 到 ~/.hermes/):
- 从 dmg 内嵌 copy uv · Python 3.11 · hermes-agent 源码 · **0 网络**
- **若员工机装了 Node.js** → 会尝试 npm install (需公网 · 达华可能挂 · 但只 `log_warn` 不 throw · installer 依然成功)
- **若员工机没装 Node.js** → 自动 skip npm + Playwright chromium · installer 成功

3-5 min 装完.

### 若 chat 不通 · debug

```bash
# 应用 log
tail -100 ~/Library/Logs/com.catfish.companion/*.log

# hermes 装到哪
ls ~/.hermes/

# hermes 起了没
ps aux | grep hermes
```

Log 若有 `401 Unauthorized` → 服务端 gateway JWT 校验问题 · 找陈鸿波看 gateway log.

---

## 若需要 browser_* tools · 手动装 chromium (可选 · 每台员工 5 min)

**只需要浏览器自动化的员工**跑:

```bash
# 前提: 员工机装了 Node.js. 若没装:
brew install node@22   # Apple Silicon or Intel

# 装 Playwright chromium (350 MB 下载 · 需公网)
cd ~/.hermes/hermes-agent
npx playwright install chromium
```

装完后 browser_* tools 立即可用. 需**员工机公网 or VPN**.

---

## 验收清单 (你 + 3 台员工)

- [ ] 陈鸿波 mac 装 aarch64 dmg · Onboarding 填服务器 · SSO 登录 · chat "hi" 通
- [ ] 员工 A (Apple Silicon) · 装 aarch64 dmg · chat 通
- [ ] 员工 B (Apple Silicon) · 装 aarch64 dmg · chat 通
- [ ] 员工 C (Intel) · 装 x64 dmg · chat 通
- [ ] (可选) 3 员工中要 browser_* tools 的手动装 chromium

**全绿** = POC 通过 · 达华可考虑规模化 (500 员工 Windows msi · 之前做的代码到时候用).

---

## 你之前做的 Windows msi 工作 · 不废

**保留代码**:
- `edge/companion-app/src-tauri/wix/catfish-postinstall.wxs`
- `edge/hermes-fork/patch_install_ps1_offline.py` (7 处 offline patch)
- `.circleci/config.yml` (Windows msi CI)
- `edge/companion-app/scripts/build-msi-local.ps1` (本地 build 一键脚本)
- `edge/companion-app/scripts/build-windows-msi-local.md` (SOP)

**POC 通过后**·**达华 500 员工规模化时启用** · 30 min 打新 msi 就绪.

---

## 支持

- 邮件: catfish-support@ai-catfish.com
- 陈鸿波 · 手机: xxx
