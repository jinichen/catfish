# Phase 1 Windows 手测 checklist (W1 + W3 端到端 verify)

**目的**: 在真 Windows 上 verify Week 2 交付的
- W1: install.ps1 offline patch 3 分支
- W3: outlook_win.py COM adapter

**不 verify** (需 msi + Companion.exe, 留 Phase 2):
- W2 msi CustomAction / InstallScope=perUser / SetProperty INSTALLDIR

---

## 前置: 拷 5 个文件到 Windows

从 macOS 拷这 5 个文件到 Windows 桌面新建目录 `C:\catfish-phase1\`:

- `install.ps1` (~165KB, 已 patched)
- `uv.exe` (39MB)
- `cpython-3.11.15-embed.zip` (47MB)
- `hermes-agent-bundle.tar.gz` (70MB)
- `_phase1_win_install_hermes.ps1` (装 hermes 一键 script)
- `_phase1_win_test_outlook.py` (测 outlook_win adapter 一键 script)

**总 156MB**. USB / OneDrive / 网盘 都行. 若 Outlook / Exchange 附件上限, 走 USB.

**Windows 前置**:
- Windows 10/11 x64
- Outlook 桌面版装了 + 配了至少一个账号 (IMAP / Exchange 都行)
- 用户账号能装软件 (perUser 装到 %LOCALAPPDATA%\hermes, 不需 admin)

---

## Task 1: 装 hermes offline (~5 min, verify W1)

在 Windows 开 PowerShell (**不需要 admin**):

```powershell
cd C:\catfish-phase1
powershell -ExecutionPolicy Bypass -File _phase1_win_install_hermes.ps1
```

期望输出 (关键行):
```
[Phase 1] install.ps1 offline mode test
  - InstallDir: C:\Users\<you>\AppData\Local\hermes\hermes-agent
  - OfflineSourceDir: C:\catfish-phase1\hermes-agent-src (from tar.gz)
  - OfflineUvExe: C:\catfish-phase1\uv.exe
  - OfflinePythonZip: C:\catfish-phase1\cpython-3.11.15-embed.zip
[install.ps1] Catfish offline: copying uv.exe from ...
[install.ps1] Catfish offline: expanding python from ...
[install.ps1] Catfish offline: copying hermes-agent from ...
[install.ps1] Managed uv installed from offline bundle
[install.ps1] Python installed from offline bundle
[install.ps1] hermes-agent installed from offline bundle
[Phase 1] ✓ hermes 装完
[Phase 1] verify: hermes info
Hermes vX.Y.Z
```

**成功 = 出 "Hermes vX.Y.Z" 版本信息**, W1 verify 通过.

**若失败**:
- `-Offline*` 参数没进 params — install.ps1 patch 没生效 (打包时 patch 挂)
- "Catfish offline" 3 处日志少一处 — 该处 anchor 没命中
- 网络还是被拉 (从 astral.sh 拉 uv 或 github.com 拉 hermes) — offline 分支没走
  → 报错贴我看

---

## Task 2: 测 outlook_win adapter (~5 min, verify W3)

**前提**: Outlook 桌面版**打开**且**登录了账号** (窗口打开状态最稳, 后台可能挂).

```powershell
cd C:\catfish-phase1
# 装 pywin32 (走 hermes 里的 uv 或 pip)
%LOCALAPPDATA%\hermes\bin\uv.exe pip install pywin32
# 或者 python -m pip install pywin32 (若 hermes 装了自带的 python 3.11)

# 拉 catfish-email 源码 (从 git 或者本地 macOS 上 tar 一下)
# 若已拉:  cd C:\catfish-phase1\email-agent
# 若未拉, 从 GitHub 拉:
#   git clone https://github.com/jinichen/catfish
#   cd catfish\edge\email-agent
#   pip install -e ".[windows]"

python _phase1_win_test_outlook.py
```

期望输出:
```
[Phase 1] outlook_win adapter smoke test
[test 1] platform guard: sys.platform=win32 ✓
[test 2] _import_pywin32() ✓
[test 3] Outlook.Application COM Dispatch ✓
[test 4] list_accounts():
  - <你的邮箱>@... (default=True)
[test 5] list_messages(folder=Inbox, limit=3):
  - 邮件 1 主题: "..."
  - 邮件 2 主题: "..."
  - 邮件 3 主题: "..."
[test 6] read_message(id=<first msg id>):
  - subject: ...
  - sender: ...
  - date: ...
[test 7] search(query="test", limit=2): 找到 N 封
[Phase 1] ✓ outlook_win adapter smoke test 全过
```

**成功 = list_accounts 返你的邮箱 + list_messages 返 3 封真邮件**, W3 verify 通过.

**若失败**:
- `ClientNotRunningError: Outlook COM 初始化失败 (hresult=0x80080005)` — Outlook 没在跑, 手动开 Outlook 再试
- `DataNotFoundError: Outlook 里没配任何账号` — 打开 Outlook 加个 IMAP/Exchange 账号
- `pywintypes.com_error` 其他 hresult — 贴给我看

---

## Task 3: verify 邮件读取 (~3 min, 深度 verify W3)

若 Task 2 拿到邮件 id, 手动测 read_message + Message-ID:

```powershell
python -c "
from catfish_email.adapters.outlook_win import OutlookWinAdapter
from catfish_email.adapters.base import ListFilter
a = OutlookWinAdapter()
msgs = a.list_messages(ListFilter(folder='Inbox', limit=1))
if msgs:
    full = a.read_message(msgs[0].id)
    print('subject:', full.subject)
    print('sender:', full.sender)
    print('body_text (前 200 字):', full.body_text[:200])
    print('body_html len:', len(full.body_html))
    print('is_read:', full.is_read)
    print('has_attachments:', full.has_attachments)
    print('message_id (RFC 822):', full.message_id)
"
```

**期望**:
- Exchange 账户: `message_id (RFC 822): <xxx@domain.com>` — 有值
- IMAP 账户: `message_id (RFC 822): None` — 无值 (predictable fallback, isReplied 会退化 subject fuzzy)

---

## Phase 1 完成 checkpoints

- ✅ Task 1: hermes 装到 `%LOCALAPPDATA%\hermes\hermes-agent`, `hermes info` 返版本
- ✅ Task 2: list_accounts 返你邮箱, list_messages 返 3 封真邮件
- ✅ Task 3: read_message 返完整 subject/body/message_id

3 项全过 → **Phase 1 收工**, 报告贴回 chat.

## 若撞 blocker (贴给我看)

任何 3 项其中一处挂:
1. 完整 log 贴回来
2. 我 audit + 给 fix
3. 修 code 后重打 install.ps1 或 outlook_win.py, 你重跑

---

## Phase 2 决定 (Phase 1 完事后再决定)

Phase 1 若过, Week 2 W1 + W3 交付**验证完毕**. 剩下 W2 (msi + Companion.exe) 需要:
- macOS 上装 xwin + cross-compile Companion.exe (跳 ort-sys + esaxx-rs blocker)
- 或者 Windows 上 native cargo build msi (2-3h Win VM 或复用你物理机 dev toolchain)

留 Phase 1 报告回来讨论.
