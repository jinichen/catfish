# Companion 部署 — 网关地址配置 (BL-WIN9 / DEPLOY1, 5/8)

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

员工电脑装好 Companion 之后, 第一次启动会自动生成
`~/.catfish/companion.yaml` (mac) 或 `%USERPROFILE%\.catfish\companion.yaml` (Windows),
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
