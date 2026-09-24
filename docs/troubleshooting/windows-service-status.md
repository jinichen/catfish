# Windows：浏览器能打开，Companion 却显示未运行

## 区分三件事

1. TCP/HTTP 可达，不等于客户端信任 HTTPS 证书。现场的
   `SEC_E_UNTRUSTED_ROOT (0x80090325)` 明确是证书信任失败，不是服务停机。
2. Hermes CLI 可运行，不等于 Hermes HTTP API 已监听；普通浏览器能打开网页，
   也不等于 Catfish 的独立 Chrome CDP 已就绪。
3. 邮件附加组件的安装失败不能作为 Hermes API 停机的证据。

## 新版行为

- 门户保留浏览器打开入口。证书错误、超时、HTTP 错误保留原始详情，不显示“未运行”。
- “查看检测详情 / 证书配置”支持导入 IT 提供的 PEM 公共证书。
  默认保存到 Windows 用户目录下 `.catfish/server-ca.pem`（若设置 HOME 则以 HOME 为准）。
  下一轮检测重新加载，不需要重启；不会自动下载并信任未知服务器证书。
- 证书仍必须有效且匹配访问地址。访问 `https://127.0.0.1` 时，证书 SAN
  需要包含该 IP；信任证书不会绕过域名校验。不要全局关闭 TLS 校验。
- Hermes 直接检查配置地址的 `/health`；Chrome 检查 `/json/version` 的
  CDP WebSocket 地址；Tool Bridge 直接调用 health RPC。
  Windows 的 Tool Bridge endpoint 文件存 TCP 端口，不是 Unix socket。
- 取消前置 800ms TCP 判定。HTTP 检测使用 5 秒预算与客户端地址回退。
  没有页面不代表 Chrome 不健康；CDP 可用也不承诺每个页面的 JS 都正常。
- UI 检测失败显示“状态未知”及实际错误，不再连续三次失败就杀进程。
  轮询不重叠，卸载后不继续更新状态。后台已有的进程退出恢复机制保持不变。
- Windows PID 检查识别 tasklist CSV 中的精确 PID，不再因为中文编码失败丢弃
  存活进程；命令行检查使用 PowerShell CIM，取消对 WMIC 的依赖。

## 发布门槛

MSI workflow 在封装前执行四组 Windows 原生回归：

- localhost IPv4 服务、HTTP 401、有效/无效 CDP 响应；
- 证书导入、热加载、非法输入不覆盖现有信任；
- 中文编码 PID 解析，以及识别 Windows 测试进程；
- Windows TCP endpoint 文件 RPC 与错误详情。

Mac 的测试和 Windows 交叉类型检查不能代替 Windows 真机运行。发布后应核对
构建 commit，并在现场确认配置地址与服务实际监听地址一致；不能仅凭三个图标
全部变绿就判定业务功能全部正常。
