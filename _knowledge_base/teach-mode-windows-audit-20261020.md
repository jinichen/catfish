# 教学模式 Windows 专项审计 (只读, 2026-10-20)

范围: 「演示教学 → 凝固成脚本 → 再跑」整条链路在 Windows 上每一步依赖什么、哪些是 Mac 专有。
方法: 只读代码, 没跑 Windows 实机。每条判定附出处; 找不到的写「没找到」。
路径简写: `TB` = `edge/tool-bridge/src/catfish_tool_bridge/`, `RS` = `edge/companion-app/src-tauri/src/`。

**实测补充 (鸿波 10/20 口头)**: Windows 上 Chrome + 凝固 SKILL 已测过, 没有问题。下面第 1、2 步的「Windows 可用」以这条实测为准, 代码审读只补充「为什么能通 / 还有哪些边角没被这次实测覆盖」。其余步骤 (RecMode、再跑含下载文件、凭据、工作流) 没有实测记录。

## 已有的 parity 覆盖 (别重复查)

`edge/companion-app/scripts/check_windows_parity.py` (CI 在 `.github/workflows/ci.yml`) 当前通过 R1–R8:
Rust cfg 排除 Windows、裸 `Command::new`、Rust 拼 `.hermes`、只读 HOME、Python `Path.home()/".hermes"`、PowerShell 写文件缺 `-Encoding`、安装包资源清单、非 mac 分支写「未实现」。

它**没覆盖**的 (本次发现的问题都落在这里):
- 只扫 `edge/companion-app` 和 `delivery` 下的 `.ps1` (R6, 脚本 :160), `edge/browser-agent/scripts/*.ps1` 不在范围, 也不查 `.hermes` 路径。
- 不查 Python 里 `subprocess.run(["bash", ...])`、`"/" in path` 这类把 POSIX 路径当前提的写法。
- 不查 Python 子进程 `text=True` 的解码 (Windows 默认 GBK)。

## 路径与环境总览

| 东西 | 位置 | Windows 判定 | 出处 |
|---|---|---|---|
| hermes 数据目录 | `%LOCALAPPDATA%\hermes` (HERMES_HOME 优先) | 可用 | `TB/hermes_paths.py:19-24`; `RS/services/catfish_paths.rs:375-387` |
| hermes venv python | `<hermes_home>\hermes-agent\venv\Scripts\python.exe` | 可用 | `RS/services/catfish_paths.rs:197-208` |
| `~/.catfish/*` (traces、skills、recordings、downloads、oauth) | `%USERPROFILE%\.catfish\...` (`Path.home()`), 不是 `%LOCALAPPDATA%` | 可用但与 hermes 不在同一棵树 (见 P3) | `TB/trace_recorder.py:89`; `TB/skill_register.py:53`; `TB/catfish_tools_skill_ops.py:79-81`; `RS/commands/recordings.rs:47-55` (`home_env` 兼容 USERPROFILE, `RS/util/paths.rs:55-57`) |
| Companion 状态 / Chrome profile | `%USERPROFILE%\.catfish\companion-state\chrome-profile` (无源码树时) | 可用 | `RS/services/catfish_paths.rs:66-72, 260-262` |
| tool-bridge endpoint | `~/.catfish/tool-bridge.sock`; Windows 文件内容是 TCP 端口号 | 可用 (设计上已处理) | `RS/services/catfish_paths.rs:165-174` |
| Playwright Python 包 | 随 hermes venv 装, 钉 1.64.0 | 可用 | `edge/companion-app/src-tauri/hermes-extra-packages.txt:14`; `RS/commands/hermes_install_artifacts.rs:261-266` |
| Chromium | 离线包 `chromium-embed.tar.gz` 解到 `%LOCALAPPDATA%\ms-playwright` | 可用 | `RS/commands/hermes_install_native.rs:200-226`; `resources/windows/README.md:12` |

## 第 1 步 「我教你」: hermes 指挥 → catfish_browser_* → trace_recorder 写 jsonl

**判定: Windows 可用 (实测通过)**

- CDP 连接方式: tool-bridge 用 Playwright `connect_over_cdp("http://127.0.0.1:9222")` 复用 Companion 起的 Chrome, 不自己拉浏览器 —— `TB/catfish_tools_browser.py:53-55, 104-133`。127.0.0.1 写死, 避开了 `localhost` 在 Windows 解析成 ::1 的问题。
- Chrome 由 Companion 起, 不是 `catfish-browser-attach.*` 脚本: `RS/commands/chrome.rs:18-27` → `RS/services/chrome_runtime.rs:200-277`。
  - 浏览器候选顺序: 自带 Chromium → Chrome → Edge, 逐个试到能起为止 (`chrome_runtime.rs:232-277`; 候选表 `RS/services/browser_paths.rs:10-58`, Windows 分支用 `LOCALAPPDATA/ProgramFiles/ProgramFiles(x86)` 环境变量)。
  - 独立 profile `--user-data-dir`, 不碰员工日常 Chrome (`chrome_runtime.rs:7-12, 126-134`)。
  - Windows 上找 profile 占用者走 PowerShell `Get-CimInstance Win32_Process`, 并显式把输出设成 UTF-8 (`chrome_runtime.rs:65-70`)。
  - 同步 `browser.cdp_url` 到 hermes `config.yaml`: 用 `catfish_paths::hermes_config_path()` (走 `%LOCALAPPDATA%\hermes`), Rust 写 UTF-8 (`RS/commands/chrome.rs:31-67`)。
- trace 落盘: `~/.catfish/traces/active.jsonl`, `_state.json`, `_last_completed.json`, 全部显式 `encoding="utf-8"` —— `TB/trace_recorder.py:89-92, 164-216, 302, 370, 392`。没找到 POSIX 专有调用 (grep `fcntl|chmod|symlink|signal|/tmp|osascript|screencapture` 在该文件无命中)。
- 录制白名单 `RECORDED_TOOLS` 全是 catfish_browser_* / captcha / locate / run_skill / download 和工作流积木 (`trace_recorder.py:103-123`)。
- `catfish_browser_screenshot` 走 CDP 截图, 不需要 macOS 截屏权限 (`catfish_tools_browser.py:437-440`)。**没有任何 OS 级截屏依赖**。`catfish_tools_browser.py:437` 注释里提的「catfish_screenshot (mac screencapture)」是另一个工具, 不在教学录制白名单里。
- `catfish_recognize_captcha`: base64 图 → `http://127.0.0.1:8999/v1/chat/completions` (中央网关 loopback), 带 `~/.catfish/oauth/id_token`。纯 HTTP, 无平台分支 (`TB/recognize_captcha.py:45-54, 345-390`)。`browser_locate.py:44-94` 同样。
- 教学凭据 (`catfish_browser_fill(secret_ref=...)`):
  - macOS 走 Companion socket 问密码 (`TB/companion_secrets.py` 文件头; `RS/services/catfish_paths.rs:176-187` 的 `companion_secrets_socket` 带 `#[cfg(target_os = "macos")]`)。这是**Mac 专有**, 且文件头写明 Windows 不需要 (凭据管理器没有按程序的 ACL)。
  - Windows 走 `wincred://` → `keyring.get_password("catfish", name)` (`TB/secret_resolver.py:209-245`); Companion 存的时候写同一个 service/user (`RS/commands/teaching_credentials/index.rs:175-190`)。
  - **风险**: Python `keyring` 声明在 `edge/tool-bridge/pyproject.toml:20` (`sys_platform == 'win32'`), 但 hermes 额外包清单 (`hermes-extra-packages.txt`) 里没有 `keyring`; 我也没在 `scripts/`、`.github/` 里找到把它装进 hermes venv 的地方。若 venv 里没有, 会落到 `secret_resolver.py:249-290` 的 PowerShell `Get-Secret` 兜底, 那条要 SecretManagement 模块, 读不到 Companion 存的凭据。→ 原判「不确定」; **鸿波 10/20 补充: Windows 上带密码的再跑凝固已实测通过, 此疑点撤销** (待修 P2 已作废)。

### 1 的附属: `catfish-browser-attach.ps1` vs `.sh`

**判定: ps1 有 Windows 路径 bug, 但不在教学主链路上 (Companion 已取代它)**

- `edge/browser-agent/scripts/catfish-browser-attach.ps1:17` `$HermesConfig = Join-Path $env:USERPROFILE ".hermes\config.yaml"` —— Windows 上 hermes 在 `%LOCALAPPDATA%\hermes`, 这个脚本会把 `cdp_url` 写到一个没人读的文件 (9/23 R3 同类问题, 但 R3/R5 不扫这个 ps1)。
- :23 profile 用 `~\.catfish\chrome-profile`, Companion 用 `~\.catfish\companion-state\chrome-profile` (`catfish_paths.rs:260-262`), 两边不是同一个 profile: 手动跑过脚本的机器会有两份登录态。
- 没有 `catfish-browser-detach.ps1` (只有 `.sh`), ps1 尾部提示还是 `bash catfish-browser-detach.sh` (:163)。
- 文件带 BOM (首字节 EF BB BF), 编码没问题。README 仍把它当主路径 (`edge/browser-agent/scripts/README.md:55-62`), 与现状 (Companion 自动同步 `chrome.rs:9-11`) 不一致。

## 第 2 步 「凝固」: skill_freeze → script.py / SKILL.md → install

**判定: Windows 可用 (实测通过); `.sh` 路径是 Mac 专有, 但默认不走**

- 默认 `target='local'`, `run_install=False`: 产物落 `~/.catfish/skills/<ns>/<name>/`, 靠 hermes `skills.external_dirs` 看到, **不 spawn 任何脚本** —— `TB/skill_freeze.py:187, 195 (参数默认), 414-445`; 工具 schema 同样标 `run_install` 默认 false 且仅 workspace 生效 (`TB/catfish_tool_schemas_skill.py:649-698`)。
- 只有 `target='workspace' && run_install=True` 才执行 `subprocess.run(["bash", install_sh])` (`skill_freeze.py:419-427`)。
  - `skills/install_to_hermes.sh` 存在, **没有 .ps1 等价物** (`ls skills/` 只有 README.md、creative、department、install_to_hermes.sh)。
  - Windows 上 `bash` 不在 PATH 时抛 FileNotFoundError, 被 `except Exception` 吞成 `{"ran": True, "error": repr(e)}` (:433-434), 不崩, 但 summary 会写「未跑或失败」。
  - 且 `workspace` 根要求有仓库 `skills/` (`_resolve_workspace_skills_root` :76-96), 客户机没有仓库, 会直接报错并建议改 local (:317-321)。结论: 客户机 Windows 上这条路本来就进不去; 对国央企员工机无影响。
  - 此处 `text=True` 没指定 encoding, 若将来有人在 Windows 上装 Git Bash 触发, 输出会按 GBK 解码 —— 现状不可达, 记一笔。
- 注册到 hermes: `skill_register.py:53, 56` `LOCAL_SKILLS_ROOT=~/.catfish/skills`, `HERMES_CONFIG_PATH=hermes_home()/config.yaml`; 原子写 `tempfile.mkstemp` + `os.replace`, UTF-8 (:65-81, 126, 178)。`os.replace` 在 Windows 目标被占用时会失败, 代码只 log.warning 不崩 (:29-33 注释)。
- script.py / SKILL.md 全部显式 `encoding="utf-8"` 写 (`skill_freeze.py` 凝固段 `write_text(..., encoding="utf-8")`); 生成脚本里 `import` 的只有 logging/time/typing, 工具走 `dispatch_native` (`TB/skill_freeze_template.py:598-617`), 没有平台分支。
- 覆盖旧版用 `Path.rename` 备份到 `.bak-<ts>/` (`skill_freeze.py` 凝固段 backup): 同目录内 rename, Windows OK; 若 script.py 被另一进程打开会失败 (边角, 未实测)。
- verify gate 在凝固时做 AST + 白名单 (`TB/skill_verify.py`), 纯 Python, 无平台依赖。

## 第 3 步 RecMode (录屏教学)

**判定: Windows 可用 (代码层面), 不确定需实测**

- 「录屏」不是 OS 屏幕录制: `cdp_listener.py` 连 Chrome page-level CDP, 监听事件 + `Page.captureScreenshot` 关键帧, 落 `~/.catfish/recordings/<sid>/` (`TB/recmode/cdp_listener.py:1-36, 396, 454-470`)。**没有 ffmpeg / screencapture / 屏幕录制权限依赖**。
- CDP 连接: `GET http://localhost:9222/json` (httpx, `trust_env=False`) 再连 page ws (`cdp_listener.py:66, 112-150, 424-450`)。
  - 默认值写的是 `localhost` 而不是 `127.0.0.1`。Companion 自己注释过 Windows 上 `localhost` 的 IPv4/IPv6 解析会让 TCP 探测误判 (`RS/commands/chrome.rs:77-79`)。而 Chrome 调试端口只听 IPv4。httpx/websockets 一般会逐个地址重试, 但 `/json` 返回的 `webSocketDebuggerUrl` 本身也带 `localhost`。→ **不确定需实测**, 稳妥做法是默认改 127.0.0.1 (和 `catfish_tools_browser.py:55` 一致)。
- 代理处理已考虑 (临时清 `*_PROXY` env, `cdp_listener.py:421-451`), 国企内网常见, 这点比 Mac 路径更重要。
- websockets 包依赖: `cdp_listener.py:52-58` 缺包时友好报错; **没找到** 它在 `hermes-extra-packages.txt` 或 Windows wheelhouse 的明确声明 (hermes 依赖可能间接带了, 未验证) → 实测确认 `import websockets` 与 `import httpx`。
- 综合 / 修选择器 (aggregator、selector_repair): 调中央网关 `http://localhost:8999`, token 由 Companion 经 RPC 转发 (`TB/recmode/aggregator.py:350-355` 要求 token; `RS/commands/recmode.rs:66` 转发 `auth_token`)。非平台相关。
- 落档 + 同步: `aggregator.py:553` `Path.home()/".catfish"/"skills"`; `skill_sync.py:29-34` 同步到 `hermes_home()/skills/<slug>/`, 用 `shutil.copytree`/`rmtree` (故意不用 symlink, `skill_sync.py:20`, 对 Windows 反而是好事)。`rmtree` 在 Windows 遇到只读 / 被占用文件会失败, 无 `onerror` 处理 → 重录同名 skill 时可能失败 (边角, 未实测)。
- 清理: `cleanup.py:76-80` CATFISH_HOME 优先, 否则 `~/.catfish/recordings`; 与 Rust 侧 `recordings.rs:47-55` 同规则。
- Companion 前端 `src/tabs/Chat/RecMode/*`、`store/recmode.ts` 没有平台分支 (grep `platform|isMac|darwin|navigator` 无命中), 只有 macOS 风格的配色/注释。
- 「在文件夹中显示」: `recordings.rs:229-244` 已走 `file::reveal_in_finder`, Windows 用 `explorer /select,` (10/2 改过)。
- 录音 (旁白 / 语音输入, 10/1 改版): 进程内 cpal, macOS CoreAudio / Windows WASAPI (`RS/services/meeting_recorder.rs:1-5, 23, 83, 159-166`); `RS/commands/speech.rs:26, 150-190` `cfg(any(macos, windows))`。
  - 转写需要「会议组件包 (FunASR)」, 解码用包里的 PyAV (`RS/services/meeting_asr.rs:183-185, 257-285`)。**组件包要单独装/下载** (`scripts/fetch-meeting-asr-release.sh`, `scripts/build-meeting-asr-pack.sh`), 没装时 `need_asr()` 报错 (`speech.rs:45`)。能不能在国企内网机器上拿到这个包 → 没找到交付说明, 实测/交付确认。
  - 麦克风权限: Windows 隐私设置里「允许桌面应用访问麦克风」被组策略关掉时 cpal 开流会失败, 代码里没找到针对性提示 (`meeting_recorder.rs:166-173` 只返回通用错误)。→ 不确定。
  - 录音 id 用 `std::env::temp_dir()` 放临时 wav (`speech.rs:122`), Windows 有效。

## 第 4 步 「再跑」: catfish_run_skill → script.py

**判定: Windows 可用; 有一处文件路径识别 bug**

- 加载: `importlib.util.spec_from_file_location`, 唯一模块名, 进程内执行 (`TB/catfish_tools_skill_ops.py:180-210`), **不起子进程, 不用 venv 之外的解释器**。
- skill 搜索根: 仓库/`CATFISH_SKILLS_DIR` → `~/.catfish/skills` → 专家包 (`catfish_tools_skill_ops.py:79-100`)。客户机找 `~/.catfish/skills` 即可。
- script.py 里的浏览器动作都是 `dispatch_native(catfish_browser_*)` → 仍是 `connect_over_cdp(127.0.0.1:9222)`, 所以「Playwright 浏览器从哪来」= Companion 起的 Chrome/Chromium/Edge; Python 侧只需要 playwright **包** (hermes venv 内, 钉 1.64.0), 不需要 `playwright install`。离线包 chromium-embed 是给 Companion 起浏览器用的 (`RS/services/browser_paths.rs:86-98`)。缺包提示写的是 `~/.hermes/hermes-agent/venv/bin/pip install ...` (`TB/catfish_tools_browser.py:144`), Windows 上路径/命令都不对 —— 只是错误文案, 但员工照做会失败。
- **BUG: 返回值里的文件路径只认含 `/` 的字符串**: `_extract_file_paths` (`catfish_tools_skill_ops.py:248-270`) 对 `"/" in result` 才收。Windows 的 `C:\Users\...\x.docx` 没有 `/`, 全部被丢弃 → `run_skill` 返回的 `files` 为空。下游:
  - `skill_workflow.py:230-232` `_files_of` 同函数; `skill_workflow.py:287` 的 `files_min` 校验会因 `files` 为空而判失败。
  - 影响所有「产出文件」的 skill 和工作流 (下载、生成 docx/xlsx)。纯浏览器点击类 skill 不受影响。→ **Windows 实际故障, 高优先级**。
- 网页下载积木落 `<CATFISH_HOME or ~/.catfish>/downloads/<日期>/` (`TB/catfish_tools_browser_download.py:20-23`; `wiki_files._catfish_home` `wiki_files.py:107-112`), 路径同样是反斜杠。

## 第 5 步 工作流 (skill_workflow / workflow_store / workflow_tool)

**判定: Windows 可用, 受第 4 步 `files` bug 影响**

- 路径: 写 `~/.catfish/skills/<ns>/<name>/workflow.yaml + SKILL.md` (`TB/workflow_store.py:223-230`); 其它状态也在 `~/.catfish/`。全部 `encoding="utf-8"`; yaml 用 `safe_load/safe_dump` (`allow_unicode=True`)。
- 子进程: `skill_workflow.py` / `workflow_store.py` / `workflow_tool.py` / `workflow_freeze.py` / `workflow_catalog.py` 里没找到 `subprocess`/`Popen`/`bash` 调用 (grep 无命中); 积木是进程内函数调用 (`skill_workflow.py:244, 294`; `workflow_catalog.py:32`)。
- `workflow_store.py:35-38` 先写 `.tmp` 再 `tmp.replace(path)` (内部就是 `os.replace`), Windows 目标被占用时会失败 (边角, 未实测)。
- `workflow_freeze.py:101` `target.split("/")[-1]` 处理的是 skill_path (逻辑路径, 恒为 `/` 分隔), 不是磁盘路径, 没问题。
- 唯一实质问题是 `_files_of` / `files_min` (见第 4 步)。

## 第 6 步 小鲶提议技能 (catfish_tools_propose)

**判定: Windows 可用**

- 提议日志 `~/.catfish/skill_proposals.jsonl`、`skill_revisions.jsonl`, append 模式, UTF-8 (`TB/catfish_tools_propose.py:46, 69, 86, 447, 458, 475`)。
- 接受提议 → 写 `~/.catfish/skills/<ns>/<name>/` (`catfish_tools_propose.py:338-363`), 同步 hermes 失败静默不阻塞 (:376)。
- 没找到 subprocess / bash / POSIX 路径调用。
- 安装类工具 (`catfish_tools_install.py`): 垃圾箱 `~/.catfish/skill-trash` 用 `shutil.move` (:523-527), 暂存 `~/.catfish/skill-staging` (:282), Windows OK; Hub 默认 `http://127.0.0.1:9001` (:381)。

## 汇总表

| # | 步骤 | 关键依赖 | 判定 | 备注 |
|---|---|---|---|---|
| 1 | 我教你 (browser_* + trace) | Companion 起 Chrome/Chromium/Edge + connect_over_cdp 127.0.0.1:9222; `~/.catfish/traces` | **Windows 可用 (实测)** | 无 OS 截屏依赖 |
| 1a | 教学凭据取密码 | macOS: Companion socket; Windows: `keyring`/wincred | macOS 通道 **Mac 专有**; Windows 通道 **可用 (实测)** | 带密码再跑已测过 |
| 1b | catfish-browser-attach.ps1 | `$env:USERPROFILE\.hermes` | **有 Windows 路径 bug** (非主链路) | 无 detach.ps1; profile 与 Companion 不同 |
| 2 | 凝固 (local, 默认) | 只写 `~/.catfish/skills` + 改 config.yaml | **Windows 可用 (实测)** | |
| 2a | 凝固 workspace + install_to_hermes.sh | `bash` + `skills/install_to_hermes.sh`, 无 .ps1 | **Mac 专有** | 客户机默认不可达 |
| 3 | RecMode 录制 (CDP) | websockets/httpx + `localhost:9222` | **不确定需实测** | localhost vs 127.0.0.1; websockets 是否在 venv |
| 3a | RecMode 录音/转写 | cpal WASAPI + FunASR 组件包 + PyAV | **Windows 可用 (代码), 依赖组件包交付** | 10/2 起 Windows 开了 |
| 3b | RecMode 落档/同步 hermes | copytree/rmtree, `hermes_home()` | Windows 可用 | rmtree 无 onerror |
| 4 | 再跑 run_skill | 进程内 importlib + connect_over_cdp | **Windows 可用**, 但 `files` 识别 **有 bug** | `"/" in path` |
| 5 | 工作流 | 进程内, 无子进程 | Windows 可用, 受 4 的 bug 影响 | `files_min` 校验 |
| 6 | 小鲶提议技能 | `~/.catfish/*.jsonl` | Windows 可用 | |

## 按风险排序的待修清单

> **10/20 处理结果 (鸿波拍板后同日改完, 未提交)**:
> - P1 已修: `_extract_file_paths` 两种分隔符都认 (`catfish_tools_skill_ops.py` `_has_sep`), 加了 Windows 路径回归测试。
> - P3 / P4 及第 3 节 RecMode 全部作废: 录屏学习 (RecMode) 和「让 AI 学」(hermes /learn, 插件 P29) 整体拿掉。
>   会议纪要借用的 `call_llm` 挪到 `gateway_llm.py`, 小鲶提议技能用的 `skill_format.py` / `skill_sync.py` 挪出 `recmode/`。
> - 顺带修: 审计导出「在文件夹中显示」调的 `open_in_finder` 命令不存在, 兜底按 `/` 切路径, Windows 打开的是文件本身; 改用 `reveal_in_finder`。

1. **P1 — `_extract_file_paths` 只认 `/`** (`TB/catfish_tools_skill_ops.py:248-270`)。Windows 上产出文件的 skill/工作流 `files` 恒空, `files_min` 校验必失败。修法: 识别 `os.sep`/盘符 (`re.match(r"^[A-Za-z]:[\\/]", s)` 或 `Path(s).suffix` 且 `Path(s).is_absolute()`); 加 Windows 路径用例的单测。这是教学链路里唯一一处已能从代码确认的 Windows 实际故障。
2. **P2 (已作废: 10/20 实测通过) — 教学凭据在 Windows 的 `keyring` 依赖** (`TB/secret_resolver.py:209-290`; `pyproject.toml:20`; `hermes-extra-packages.txt` 没有它)。需在 Windows 实机上验证 hermes venv `import keyring`; 没有就加进 `hermes-extra-packages.txt` 并进 `hermes_install_artifacts.rs:266` 的自检 import 串。验证用例: Companion 存一条教学凭据 → 教学里 `catfish_browser_fill(secret_ref=wincred://...)`。
3. **P3 — RecMode 默认 `localhost:9222`** (`TB/recmode/cdp_listener.py:66`)。改成 `127.0.0.1` 与 `catfish_tools_browser.py:55` 一致; 同时实测 `import websockets, httpx`。内网代理环境已处理, 不用动。
4. **P4 — 会议/ASR 组件包交付**: Windows 旁白录音要组件包 (`meeting_asr.rs`), 内网机怎么拿到包没找到文档。录屏教学的口头旁白 (RecordingOverlay 调 `speech_start_recording`, `SetupModal.tsx:60`) 在没装包时是否有降级提示需实测。
5. **P5 — 错误文案里的 Mac 路径**: `catfish_tools_browser.py:144` 的 pip 提示在 Windows 上无效; 改成基于 `hermes_paths.hermes_home()` + `Scripts\pip.exe` 生成, 或只说「请在 Companion 里修复 hermes 环境」。
6. **P6 — `catfish-browser-attach.ps1`**: 改用 `LOCALAPPDATA\hermes` 或直接删掉 ps1/改 README 指向 Companion; 补 parity 脚本对 `edge/browser-agent` 下 `.ps1` 的扫描。非主链路, 低风险。
7. **P7 — 边角 (未实测, 可合并成一次加固)**: `skill_sync.py` 的 `shutil.rmtree` 加 `onerror` 清只读位; `skill_freeze.py` / `workflow_store.py` / `skill_register.py` 的 rename/replace 在目标被占用时的重试; `install_to_hermes.sh` 补 `.ps1` 或在 Windows 明确返回「不支持」而非 `repr(FileNotFoundError)` (`skill_freeze.py:433-434`)。
8. **P8 — parity 脚本补规则**: 新增 R9 `subprocess` 调 `bash`/`sh`/`.sh` 须带 `windows-parity:` 理由; R10 Python 里 `"/" in <path>` 做路径判定; R11 `text=True` 无 `encoding=` 的 `subprocess.run` (Windows 默认 GBK)。

## 使用情况补充 (鸿波 10/20)

视频教学 (RecMode) 与文档自学在 macOS / Windows 都从未实际使用过; 文档自学不在本次审计范围, 未看代码。RecMode 的疑点 (P3/P4) 均为未实测, 不是已确认故障。是否保留由鸿波决定。

## 本次没覆盖 / 需要实机的

- 没在 Windows 实机上跑任何东西; 「Windows 可用」= 代码里有显式 Windows 分支或无平台依赖 + (第 1、2 步) 鸿波 10/20 口头实测。
- 没审 hermes 本体 (外部仓库) 对 `skills.external_dirs` 的 Windows 路径解析 (config.yaml 里写的是 `C:\Users\...` 绝对路径)。凝固实测通过说明至少该路径被识别。
- 没审 `catfish_tools_browser_actions.py` 中 click/fill 的具体 JS 注入, 它们走 Playwright, 预期无平台差异。
- 没查中央网关 (`:8999`) 在 Windows 员工机上的启动方式 (captcha / 综合 / selector_repair 依赖它), 属于另一份审计。
