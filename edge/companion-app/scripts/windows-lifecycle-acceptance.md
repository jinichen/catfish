# Windows 安装生命周期验收（2026-09-09）

新版 MSI 的安装/卸载均通过内嵌 GUI 维护程序清理；不依赖已安装 Python。
Companion 启动时同样迁移旧入口，并用当前用户文件锁防止多实例。

## 清理边界

- 只停止当前用户、可执行文件路径核实过的 Companion、受管搜索/工具服务及其控制台子进程。
- 只移除动作指向旧 `catfish-search-watcher.bat` 且用户身份匹配的 `CatfishSearchWatcher` 登录任务。
- 旧任务 XML、旧 BAT、移除的 Run 值有备份；不清空 Hermes home、邮件目录、知识库或任务库。
- 不猜测/删除任何 Windows 服务。未知所有者或非匹配动作的启动任务会跳过并记录。
- 日志与备份：`%LOCALAPPDATA%\CatfishMaintenance\`。
- 邮件安装日志：`<实际 HERMES_HOME>\logs\catfish-companion-bootstrap.log`。
- 邮件版本以离线归档和安装脚本的 SHA256 为依据；相同版本号但不同内容也更新。

## Windows 现场验收（本机 macOS 无法替代）

### 同版本覆盖回归（9/11 现场报告）

旧主程序日期仍为 9/2，邮件 CLI 不支持 discover；维护日志 install 成功不能作为升级成功的依据。
默认禁止降级的 WiX 分支必须启用 AllowSameVersionUpgrades，保持 afterInstallInitialize 移除旧产品。
这允许相同公开版本的包互相替换，也意味着不能靠该版本号区分同版本的新旧构建。

- 先安装旧 1.0.0，再安装本次 1.0.0 MSI，保留 msiexec `/l*v` 日志。
- 比较安装后的主 EXE SHA256 与本次构建 EXE SHA256，必须一致；不能只看界面的 1.0.0。
- 启动安装后的 EXE，确认 bootstrap 日志产生，邮件 CLI `discover --help` 返回 0。
- `discover --json` 必须返回 Foxmail 来源；再在应用里验证自动读取和后续刷新。
- 运行 scripts/diagnose-windows.ps1 捕获进程；记录可见黑框时间以对应进程链。
  深信服 Ingress/sfwget 的控制台与 Companion 分开核对，不修改企业软件。

1. 在测试用户下安装旧版，并保留旧 BAT 登录任务和已运行的循环，再安装新版。
   验证旧循环和子进程终止、任务删除、XML/BAT 备份存在、新版只运行一个实例。
2. 退出/重开、连点两次快捷方式，验证第二次激活原窗口，不启动第二套后台服务。
3. 保留健康旧邮件 wheel，安装带不同内容但仍为 0.1.0 的新包，验证自动替换与指纹更新。
   再启动一次：指纹未变且导入正常时不得重复安装。模拟安装失败时不得写成功指纹。
4. 在 Foxmail 数据迁移至 E 盘的测试机验证账号与邮件；Outlook 未装/COM 超时时，Foxmail 独立返回。
   发现规则读取 Foxmail 注册表、App Paths、附近配置文件和已知目录；不会扫描整盘、破解账号或绕过权限。
5. 卸载新版、重启登录：没有 Companion/旧 BAT 自动启动；邮件、知识、任务数据仍在。
6. 重装新版：自动准备组件，不要求填写 companion.yaml。检查维护日志与邮件日志无失败。

两条 Windows CI 都运行 `test-windows-maintenance.ps1` 的隔离 fixture 测试。
CI fixture 的进程、任务操作被 mock，不代替上面的真实安装与重启验收。
