# Companion 部署 — 网关地址配置 (BL-WIN9 / DEPLOY1, 5/8)

## 部署形态分两种 (按客户需求选)

### 形态 A: 纯聊天 — Windows 端只装 Companion .exe

中央服务器:
- catfish-gateway (Python 8999)
- catfish-identity (Python 8998, SSO)

员工 Windows:
- 只装 Companion .exe (含 yaml 配置)
- **能聊天 / 看仪表盘 / 用 SSO 登录**
- ❌ **浏览器自动化 / skill 跑不了** (tool-bridge 不在本机)

适合:第一波给客户演聊天能力, 不演自动化.

### 形态 B: 完整 — Windows 端 Companion + 本机 tool-bridge + Chrome

中央服务器: 同上.
员工 Windows:
- Companion .exe
- Python 3.10+ + tool-bridge (本机 daemon)
- Chrome (浏览器自动化用)

适合:演 EIS 登录 / 验证码识别 / catfish_browser_* 这种真自动化场景.

---

## 场景: 网关装到中央服务器, 员工 mac/Windows 都连这一台

```
                     [员工 mac/Win Companion .app/.exe]
                              ↓ HTTPS / HTTP
              ┌──────────────────────────────┐
              │  中央服务器 (e.g. 10.10.40.50)│
              │  - catfish-gateway:8999       │  ← 网关
              │  - catfish-identity:8998      │  ← SSO (可选)
              └──────────────────────────────┘
                              ↓
                  [Qwen 主力 / DeepSeek / OpenAI]
```

员工电脑只跑:
- `Companion .app` / `.exe` (UI + 浏览器自动化 + skill 跑)
- `tool-bridge` (Python, 在员工本机, 通过 TCP/socket 跟 Companion 通信)

不再跑:
- gateway (在中央服务器)
- identity (在中央服务器)

---

## 配置方法 (mac + Windows 同款)

员工电脑装好 Companion 之后, **第一次启动会自动生成**
- mac: `~/.catfish/companion.yaml`
- Windows: `%USERPROFILE%\.catfish\companion.yaml`
  (实际地址例: `C:\Users\chenhongbo\.catfish\companion.yaml`)

内容形如:

```yaml
oidc:
  issuer: http://127.0.0.1:8998
  ...

# endpoints:
#   gateway_url: http://10.10.40.50:8999
```

把 `endpoints` 段**取消注释**, 改成你的中央服务器地址:

```yaml
oidc:
  issuer: http://10.10.40.50:8998        # SSO 也指中央
  client_id: catfish-companion
  audience: catfish-companion
  scope: openid email profile

endpoints:
  gateway_url: http://10.10.40.50:8999   # 网关
  chrome_debug_url: http://127.0.0.1:9222 # Chrome 还在本机
```

**重启 Companion 即生效** — 不需要重新打包 .app/.exe.

### Windows 改 yaml 的具体步骤

1. **打开文件管理器** → 地址栏粘贴 `%USERPROFILE%\.catfish` 回车
   - (员工看不到 .catfish 文件夹的话: View → Show → Hidden items 打开)
2. **双击 `companion.yaml`** — 选 "记事本" / VS Code / Notepad++ 都行
3. **修改后 Ctrl+S 保存**
4. **完全退出 Companion** (右下角系统托盘 → 鲶鱼图标 → 退出, 或 Task Manager kill `catfish-companion-app.exe`)
5. **重新双击 .exe** — 启动后控制台 (View → Developer → Inspect Element → Console) 能看到:
   ```
   [BL-WIN9] gatewayUrl: http://127.0.0.1:8999 → http://10.10.40.50:8999 (来自 ~/.catfish/companion.yaml)
   ```

如果改完没生效:
- 缩进检查: yaml 必须 2 空格, 不是 tab
- `endpoints:` 这行**最左对齐**, 没缩进
- `gateway_url:` 缩进 2 空格 (在 endpoints 里)
- 没多空行 / 没全角空格

---

## 验证

启动 Companion, 浏览器 dev tools (View → Developer → Inspect Element)
里能看到 console.info:

```
[BL-WIN9] gatewayUrl: http://127.0.0.1:8999 → http://10.10.40.50:8999 (来自 ~/.catfish/companion.yaml)
```

仪表盘 → 控制台 看到 "上游 LLM 可达性自检" 也是从中央服务器视角.

---

## 常见错

- **改 yaml 后没生效**: 必须重启 Companion (整个 quit + 再开). 不是 cmd-R 刷新.
- **yaml 解析失败**: console 会 warn `BL-WIN9 get_runtime_endpoints 失败`, fallback build-time 默认 (127.0.0.1:8999). yaml 缩进检查一下.
- **网络连不上**: gateway_url 后头加 `/v1/catalog` 在浏览器手动访问, 看返不返 JSON. 不返就是中央服务器 / 防火墙问题, 跟 catfish 代码无关.

---

## 优先级 (yaml > env > default)

```
1. ~/.catfish/companion.yaml endpoints.gateway_url    ← 客户改这个
2. ~/.catfish/companion.yaml endpoints.gateway_host + port
3. CATFISH_GATEWAY_HOST / CATFISH_GATEWAY_PORT env    ← dev / 测试
4. http://127.0.0.1:8999 (default)                    ← 单机部署 fallback
```

---

## 跟 5/8 之前不一样的地方

之前: 前端 `VITE_CATFISH_GATEWAY_URL` build-time 替换, 一旦打包 .app/.exe 就锁死. 客户要改地址必须重新打包发版本.

现在 (BL-WIN9): 启动时 invoke('get_runtime_endpoints'), Rust 读 yaml + env 返动态值, 前端写回 `config.gatewayUrl`. 客户改 yaml 重启即生效.
