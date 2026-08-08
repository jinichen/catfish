# 达华 POC · 3 台 mac 分发 SOP

**规模**: 2 台 Apple Silicon + 1 台 Intel
**版本**: v0.20.0 (2026-08-08 · 跟 hermes v2026.8.3 对齐; 服务端 HTTPS / CA 信任 / 绕系统代理那几条仍按 7-28 那版)

> **0.20.0 这一版带的东西** (0.19.0 → 0.20.0)
>
> - hermes 升到 **v0.20.0 (v2026.8.3)** —— Companion 版本号是跟 hermes 钉死的
>   (`scripts/check_version_sync.sh` 在 CI 强制), 所以这个号跳不是我们自己攒够了功能。
> - **内嵌 Python 换了 SQLite 没洞的构建**。原来打进包的 CPython 3.11.15 链的是
>   SQLite 3.50.4, 在 WAL-reset 损坏漏洞范围内; hermes 自带的缓解只拒绝给**新库**
>   开 WAL, 对现场那些跑了几个月、早就是 WAL 的 `state.db` 一律不管。现在包里是
>   3.53.1。**这条是数据安全, 不是优化** —— 老包装的机器建议都升。
> - 修 advisor(早安页)在 hermes v0.20 上整个不可用: 上游改了协议选择逻辑,
>   会把打到我们网关的请求发成 `/v1/responses`, 而网关只有 `/v1/chat/completions`,
>   表现是早安页一直「综合判断暂不可用」。
> - 微信审批提示补齐中文: v0.20 的审批文案有三种成品, 之前只翻了一种,
>   另两种整段英文会直接发到员工微信。

**目的**: 让 3 台 mac 员工装 Companion + verify chat 通

> ⚠ **7/28 现状 · 分发前必读**
> 1. **x64 (Intel) dmg 当前没有** —— `target/x86_64-apple-darwin/` 目录不存在。
>    Intel 那台要么今晚补 build (`npm run tauri build -- --target x86_64-apple-darwin`,
>    需先 `rustup target add x86_64-apple-darwin`),要么首日只上 2 台 Apple Silicon。
> 2. 0.20.0 aarch64 dmg ≈ 613 MB (内嵌 hermes 离线包,比 0.18 的 128 MB 大是正常的)。
> 3. 服务端已启 HTTPS:员工机**多一步装 ca.pem**,见下文装机步骤 3.5。
> 4. 聊天要通,**服务器 `.env` 必须已填 `DASHSCOPE_API_KEY`** —— 门户能登录
>    不代表 key 已配;没配的话 chat 第一条消息报「上游 LLM Provider 鉴权挂了」。
>
> **员工机的聊天链路与记忆** (7/29 版起)
>
> 聊天走哪条路由 `~/.hermes/.env` 的 `API_SERVER_KEY` 决定:有 key 就是
> Companion → hermes → gateway,没有就是 Companion 直连 gateway。
>
> 这件事关系到**鲶鱼记不记得员工**:记忆是 hermes 侧每轮对话后写的,
> 直连 gateway 那条路上没有写入方(网关侧的写入能力已按"中央/边缘分离"移除)。
> 所以不经 hermes 的机器,能读旧记忆但永远不产生新记忆 —— 表现是"用起来
> 一切正常,但用多久都不会更懂你",现场看不出哪里坏了。
>
> 7/29 起 Companion 启动时会自动配好这个 key(已有则保留不换),所以员工装完
> dmg 就是走 hermes 的完整链路,记忆正常积累,**不需要额外跑任何脚本**。
>
> **唯一要注意**:key 是 Companion 首次启动时写的,而 hermes 读它是在自己启动时。
> 所以**装机当天要让 hermes 重启一次**:
>
> ```bash
> pkill -f hermes      # launchd 会自动拉起,别手动 start
> ```
>
> 不做这一步不影响使用(聊天照常直连 gateway),只是记忆当天不积累,hermes
> 下次自然重启后自愈。
>
> 另:走 hermes 的机器,在面板改服务器地址后同样要 `pkill -f hermes`——
> Companion 侧当场生效,但 hermes 是独立进程,要重启才读到新地址。

---

## 7/18 关键 code fix (已 build 进最新 dmg)

| Task | Fix | 员工场景影响 |
|------|-----|-----|
| #60 | chat 走 gateway 直连还是经 hermes, 取决于本机有没有 `API_SERVER_KEY` | Onboarding 填达华 IP 即通 · 无需额外 config |
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

> **⚠ 8/8 修正 · dmg 的位置和文件名都跟这份文档以前写的不一样。**
>
> 7/24 起 dmg 不再由 tauri 自己的 bundler 出 (`BL-TAURI-DMG-WORKAROUND`:
> 官方 `bundle_dmg.sh` 在 macOS Sequoia + Tauri 2 上反复挂), 改成
> `scripts/make-dmg.sh` 手工 `hdiutil create`。输出位置和命名跟着变了:
>
> | | 以前 (本文档一直写的) | 现在 (make-dmg.sh 实际产出) |
> |---|---|---|
> | 目录 | `src-tauri/target/release/bundle/dmg/` | `~/Downloads/` |
> | 文件名 | `Catfish Companion_0.19.0_aarch64.dmg` | `Catfish-Companion-0.20.0-aarch64.dmg` |
> | 分隔符 | 空格 + 下划线 | 全连字符 |
>
> 也就是说照旧文档走, 第 1 步 `ls` 是空的、第 2 步 `cp` 报 No such file。
> 这跟 8/1 修过的那次「SOP 写 0.18.0 而实际是 0.19.0」是同一类病, 只是这次
> 变的是**路径**不是版本号, 所以上次没被发现。

```bash
ls -la ~/Downloads/Catfish-Companion-*-aarch64.dmg
ls -la ~/Downloads/Catfish-Companion-*-x64.dmg
```

期望 (8/8 实测):
- `Catfish-Companion-0.20.0-aarch64.dmg` ≈ 613 MB (内嵌 hermes 离线包)
- x64 dmg **当前没有** · Intel 机需先补 build (见文件头 ⚠)

### 2. Copy 到分发目录 · 上传 Nextcloud

```bash
mkdir -p ~/Downloads/catfish-达华POC-0715/
cp ~/Downloads/Catfish-Companion-0.20.0-aarch64.dmg ~/Downloads/catfish-达华POC-0715/
```

上传 Nextcloud `paixiao2.duckdns.org:9997/catfish-达华POC/`.

### 3. 你 mac 上先 verify aarch64 dmg (10 min)

```bash
# 装
open ~/Downloads/catfish-达华POC-0715/Catfish-Companion-0.20.0-aarch64.dmg
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

## ⚠ 先分清: 这台机器是**全新装**还是**升级**

下面「3 台员工分发」整章写的是**全新装机**。机器上已经有 Companion 时,
照着走会卡在第 4 步等一个永远不出现的 Onboarding 界面 —— 服务器地址存在
`~/.catfish/`, 升级不会清掉, 所以那个界面不再弹。

**升级走下面这一节**, 步骤完全不同。

---

## 从旧版升级到 0.20.0 (8/8 实测)

装过 Companion 的机器用这一节。整个过程 10 分钟, 其中 3-5 分钟是等
`install.sh` 解 hermes 归档。

### 为什么不能只是"拖进去覆盖"

`install.sh` 是 **Companion 启动时**跑的, 而且只在 `~/.hermes/hermes-agent`
**不存在**时才装。所以直接覆盖 app 的结果是: 前端换成新的了, 而 hermes 运行时
还是旧版本 —— 而且没有任何提示, `hermes version` 照样报旧号。

8/1 实测撞到的具体表现: 覆盖安装后 `ls ~/.hermes/hermes-agent` 目录时间戳
纹丝不动, bootstrap 锁文件也没被碰过, 界面却已经是新的了。

### 步骤

```bash
# 1. 完全退出 Companion (⌘Q 或这条, 关窗口不算)
osascript -e 'quit app "Catfish Companion"'

# 2. 停掉旧 hermes —— 不停的话它一直占着 8642, 新的起不来
pkill -f hermes-agent

# 3. 把旧的 hermes 运行时**移开**(不是删! 出问题要靠它退回去)
mv ~/.hermes/hermes-agent ~/.hermes/hermes-agent.bak
```

4. dmg 里把 `Catfish Companion.app` 拖进「应用程序」, 选**替换**
5. 打开它。这一步才触发 `install.sh`, **等 3-5 分钟**别急着操作
6. 验:

```bash
ls ~/.hermes/hermes-agent && hermes version
curl -s localhost:8642/health
```

两处都要报 **0.20.0**。`hermes version` 还会打出
`Install directory: /Users/<你>/.hermes/hermes-agent`, 确认它指的是这个目录。

7. 聊一条 "hi" 通了, 再删备份:

```bash
rm -rf ~/.hermes/hermes-agent.bak
```

### 不通就退回去 (5 秒)

```bash
rm -rf ~/.hermes/hermes-agent
mv ~/.hermes/hermes-agent.bak ~/.hermes/hermes-agent
pkill -f hermes-agent          # launchd 会自动拉起, 别手动 start
```

### 几个会让人误判的点

- **会话和记忆不在 `hermes-agent` 里**, 在 `~/.hermes/state.db` (可能几百 MB)。
  上面第 3 步只动 `hermes-agent` 子目录, 聊天记录一条都不会丢。
- **第 2 步之后 `curl :8642/health` 可能还在报旧版本号。** 不是没停干净 ——
  Unix 上进程打开的文件被 `mv` 走之后进程照常活着, 旧 gateway 还能继续服务。
  以第 6 步 `install.sh` 跑完之后的结果为准。
- **升级不会重弹 Onboarding**, 服务器地址沿用旧的。要改地址走
  仪表盘 → 服务器配置 (见文末「面板改 IP 后 · 必做 2 步」)。
- Gatekeeper 那个「已损坏」的拦截**升级时同样会有** —— 新 dmg 是新文件,
  经网络传过去照样带隔离属性。处理方法见下面全新装机的第 3 步。

---

## 3 台员工分发 · 每台 15 min（**全新装机**）

### 员工 1/2/3 装机步骤

**给 2 台 Apple Silicon 员工**: `Catfish-Companion-0.20.0-aarch64.dmg`
**给 1 台 Intel 员工**: `Catfish-Companion-0.20.0-x64.dmg`（文件头 ⚠ 说了这个还没 build）

**装机步骤** (每台员工机):

1. 从 Nextcloud 下 dmg (对应架构)
2. 双击 dmg · 拖 `Catfish Companion.app` 到 `Applications`
3. **首次打开** · macOS 会拦。**员工看到的原话是「"Catfish Companion" 已损坏,
   无法打开。你应该推出磁盘映像」** —— 文件其实没坏,这是 Gatekeeper 对
   未签名 app 的标准反应(我们还没有 Apple 开发者证书)。

   经**网络**传过去的文件(微信 / 浏览器 / AirDrop)会被打上隔离属性,
   未签名 app 一旦带这个属性就报"已损坏"。用 **U 盘或内网共享**拷贝则不会
   有这个标记,双击直接能开 —— 三台机器建议直接用 U 盘,省掉下面这步。

   已经用微信传了的,按**这个顺序**处理(顺序不能反):

   ```bash
   # ① 先把 app 从 dmg 拖进「应用程序」—— 不要在 dmg 里直接双击
   # ② 去掉隔离属性
   sudo xattr -rd com.apple.quarantine "/Applications/Catfish Companion.app"
   # ③ 再打开
   open "/Applications/Catfish Companion.app"
   ```

   在 dmg 里改没用: dmg 是只读的, 而且弹窗上那个「推出磁盘映像」会把改动一起丢掉。

   > 拿到 Apple 开发者证书并完成签名 + 公证后, 这一步整个消失, 员工双击即用。
3.5. **装公司证书 (7/28 新增 · HTTPS 后必做)**:
   把服务器 `delivery/dahua-poc/certs/ca.pem` 拷到员工机:
   ```bash
   mkdir -p ~/.catfish && cp <拿到的>/ca.pem ~/.catfish/server-ca.pem
   ```
   不装的话:聊天/登录能用,但仪表盘「中央门户」一直显示连不上
   (桌面端走 Rust 请求,不认浏览器点过的「继续前往」)。
4. Companion 打开 · Onboarding 填服务器地址 (跟你 verify 时同款):
   - gateway_url: `http://<服务器IP>:8999`  (必须带端口 · http)
   - identity_url: `http://<服务器IP>:8998`
   - 门户 URL: `https://<服务器IP>`  (7/28 新字段 · 在仪表盘服务器配置里)
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
