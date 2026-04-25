# catfish-search 单文件打包

把 `catfish-search` 打成一个可执行文件，员工拿到后不需要装 Python、不需要 pip、不需要 venv，解压即用。

## 为什么要做这个

企业里常见摩擦：

1. 员工 Mac 上有个老 Python，pip install 各种报权限错误。
2. Windows 员工装 Python 被公司 EDR / 360 拦住。
3. Linux 员工的发行版自带 Python 版本太老（比如 RHEL 8 的 3.6）。
4. 内网代理复杂，PyPI 都连不上。

把二进制打出来放到共享盘或内部制品库，上面所有问题一次性消失。

## 谁来打包

**每个平台自己构建**。PyInstaller 没法跨平台交叉编译，Mac 上构建的产物只能在 Mac 上跑。

| 需要发布的平台 | 在什么机器上构建 |
|----------------|------------------|
| macOS (Intel/arm64) | 对应架构的 Mac |
| Windows x64 | 一台 Windows 10/11 |
| Linux x64 | 一台 Ubuntu 20.04+ / RHEL 9 |

推荐做法是开一个 GitHub Actions 矩阵，三台 runner 各打一份。个人/小团队直接找三台机子手跑也行。

## 本机构建

### macOS / Linux

```bash
cd edge/local-search
bash pyinstaller/build.sh
```

产物：`dist/catfish-search`。

### Windows

```cmd
cd edge\local-search
pyinstaller\build.bat
```

产物：`dist\catfish-search.exe`。

## 两种 Python 版本要求，容易混

这里区分两件事：

| 场景 | 看谁的 Python 版本 | 我们的要求 |
|------|---------------------|-----------|
| 开发者用 pip 安装 | **用户机器的 Python** | >= 3.9（考虑企业老环境） |
| 员工跑预打包二进制 | **打包机的 Python**（已塞进二进制） | **>= 3.10，推荐 3.12** |

所以 **打包一律用最新稳定版 Python**（脚本自动挑 3.12 > 3.11 > 3.10）：

- 启动更快（3.11 有显著性能优化）
- markitdown 永远能装（它自己要求 3.10+）
- 安全补丁最新
- 员工机器有没有 Python 跟这无关，反正他们跑的是内嵌版本

构建脚本会按顺序尝试 `python3.12` → `python3.11` → `python3.10`，都找不到才报错。

## 产物大小预期

| 配置 | 体积 |
|------|------|
| 仅 watchdog（无 markitdown） | 约 15~20 MB |
| 含 markitdown 全量文档支持（默认） | 约 70~120 MB |

## 验证

```bash
# Unix
./dist/catfish-search --help
./dist/catfish-search status

# Windows
dist\catfish-search.exe --help
dist\catfish-search.exe status
```

## 分发

把 `dist/catfish-search(.exe)` 放到：

- 内部 NAS / 共享盘：`\\fileserver\apps\catfish-search\`
- Confluence 附件页
- 内网 HTTP 站点：`https://intra.company.com/downloads/catfish-search/`

员工下载后放到 `~/bin/` 或 `C:\Tools\`，加 PATH 即可。

## 已知坑

### macOS Gatekeeper 拦截

首次双击或 `./catfish-search` 会被拦：

> "catfish-search" 无法打开，因为 Apple 无法检查其是否包含恶意软件。

解决：

```bash
xattr -d com.apple.quarantine ./catfish-search
```

如果要大规模分发，走 Apple Developer ID 签名 + notarize 流程，成本较高，内部工具一般用 xattr 就够。

### Windows Defender / SmartScreen 警告

首次运行可能提示"Windows 已保护你的电脑"。点"更多信息" -> "仍要运行"。

规避办法：

1. 申请代码签名证书（EV 证书更干净，一次几千块）。
2. 让公司 IT 把 SHA256 加到白名单。
3. 初期小范围分发，忍一下。

### 企业 EDR（比如 Crowdstrike / 360 安全卫士）

不同 EDR 对 PyInstaller 打出的 UPX 压缩包识别各异。我们的 spec 已经 **关闭了 UPX**，降低误判概率。如果仍被拦，需要联系 IT 走白名单。

### 单文件启动慢

PyInstaller 单文件模式首次启动会把自己解压到临时目录，约 1~3 秒。如果嫌慢，改用 "onedir" 模式（去掉 spec 里的 onefile 行为），体积更大但启动瞬时。

## 配合 daemon 用

打好的二进制配合 `daemon install` 子命令，自动注册到当前平台的服务管理器（launchd / Task Scheduler / systemd --user），开机自启 + 崩溃重启：

```bash
# Mac
./catfish-search daemon install

# Windows
catfish-search.exe daemon install

# Linux
./catfish-search daemon install
```

注意：`daemon install` 会把 `sys.executable`（也就是打出来的二进制本身路径）写进服务描述，所以**别在装了 daemon 之后挪走二进制**，要挪就先 uninstall 再装到新位置再 install。
