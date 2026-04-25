# 鲶鱼 Catfish · 新员工一键装

把这个仓库给到新员工（或者 IT 批量推），员工跑一条命令就能装好本地搜索 + Hermes 集成，开始用。

## 装了什么

- **`catfish-search`** CLI —— 员工电脑上的文件全文搜索（Office/PDF/代码/笔记 40+ 格式）
- **`catfish-search daemon`** —— 后台 watcher，文件变动自动增量索引
- **Hermes MCP server** —— 让 Hermes 的小鲶能自动调本地搜索
- **Hermes skill 文档** —— 给模型看的使用手册（跟 MCP 互补）

**不装**：gateway。gateway 是公司集中部署的，员工只要在 Hermes 里填企业 gateway 的 URL 和 token 就行。

## 怎么装

### macOS / Linux

```bash
# 把项目 clone 到员工机器
git clone <内部仓库 URL> ~/catfish
cd ~/catfish

# 一条命令装完
bash onboarding/install-catfish.sh
```

交互式跑，会在两个地方问"要不要现在做"：首次全量索引、后台 watcher。`-y` 跳过所有确认（IT 批量部署）：

```bash
bash onboarding/install-catfish.sh --yes
```

更细粒度的控制：

```bash
bash onboarding/install-catfish.sh --skip-index     # 不触发首次索引
bash onboarding/install-catfish.sh --skip-daemon    # 不装后台 watcher
```

### Windows

```powershell
# PowerShell，不需要管理员
cd C:\path\to\catfish
.\onboarding\install-catfish.ps1
```

支持同样的 `-Yes` / `-SkipIndex` / `-SkipDaemon` 参数。

## 前置条件

| 项 | 最低版本 | 安装方式 |
|------|---------|---------|
| Python | 3.10（推荐 3.12） | `brew install python@3.12` / `apt install python3.12` / python.org installer |
| Hermes Agent | 0.10+ | 按 Hermes 官方文档装；**没装也能跑 catfish-search CLI**，只是跳过 MCP 注册 |
| Git | 任意 | 系统自带通常够 |
| 网络 | 能到 PyPI（或公司内网 mirror） | 不需要外网，可配 `pip.conf` 指向内网 index |

## 装完之后

员工日常使用：

```bash
# 搜文件（CLI）
catfish-search query "合同"
catfish-search query --json -n 20 "鲶鱼 设计文档"

# 索引范围改一下（首次默认只包含 ~/Documents ~/Desktop ~/Downloads）
catfish-search config

# 看状态
catfish-search status
catfish-search daemon status
```

在 Hermes 里：

```
> 帮我找上周那份客户合同
```

小鲶会自动调 `mcp_catfish_local_search_local_search`，100ms 内返回结果。不会再跑 60 秒的全盘 grep。

## 日常维护

### 员工自己

无需维护。watchdog daemon 在后台跑着，文件改动自动同步到索引。

如果突然感觉搜索不准：

```bash
catfish-search clean     # 清理已失效条目
catfish-search index     # 全量重建
```

### 平台团队 / IT

升级这个员工的版本：

```bash
cd ~/catfish
git pull
bash onboarding/install-catfish.sh --yes   # 幂等，重跑即可
```

## 卸载

```bash
# 完整卸载（保留索引数据和用户配置）
bash ~/catfish/edge/local-search/hermes-skill/uninstall.sh
catfish-search daemon uninstall

# 连索引库和配置都清掉
rm -rf ~/.catfish/

# 连员工 venv 也删
rm -rf ~/.catfish/venv/    # 已包含在上一条的 rm -rf ~/.catfish/ 里
```

Windows 版卸载类似：运行 `.\onboarding\uninstall-catfish.ps1`（如果准备了）或手工按上面步骤做。

## 排错

**`catfish-search: command not found`**
→ `~/.local/bin` 没在 PATH 里。加一行到 `~/.zshrc` 或 `~/.bashrc`：
```bash
export PATH="$HOME/.local/bin:$PATH"
```

**Hermes 启动 banner 没看到 MCP Servers 段**
→ `hermes mcp list` / `hermes mcp test catfish-local-search` 排障。常见原因：
- `catfish-search-mcp` 不在 PATH（用绝对路径）
- `mcp` 包没装到员工 venv
- Hermes 版本太老（需要 0.10+）

**索引很慢 / pdfminer 报一堆 WARNING**
→ `catfish-search index` 对大量 PDF 会慢，首次正常。WARNING 已经在 extractor.py 里压住，如果还刷屏，升级到最新代码。

**公司内网 PyPI mirror**
→ 在 `~/.pip/pip.conf` 配：
```ini
[global]
index-url = http://pypi.intra.company.com/simple/
trusted-host = pypi.intra.company.com
```
然后重跑 install-catfish 即可。

## 架构说明（给好奇的员工）

鲶鱼 Catfish 的边缘能力都装在员工自己的 Mac / PC 上，不往公司服务器发数据。具体看 `catfish-design.md` 第 4 章"边缘主权"。
