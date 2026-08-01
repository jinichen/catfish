# 安装说明

> 版本 20260728 · 全流程已在 Ubuntu 24.04 实机验证

---

## 一、交付物

| 文件 | 给谁 | 说明 |
|---|---|---|
| `dahua-poc-FULL-<arch>-20260728.tar.gz` | 服务器 | 一体包,含全部镜像 + 安装脚本,不依赖外网 |
| `Catfish Companion_0.19.0_aarch64.dmg` | 员工机器 | 桌面端,**当前仅 Apple Silicon**;Intel Mac 需另 build x64 dmg |

架构选择:x86_64 服务器用 `amd64`,ARM 服务器(鲲鹏 / Graviton / Apple Silicon)用 `arm64`。选错装不起来。

**还有一个文件在装机后才会有**:服务器上生成的 `certs/ca.pem`,要发到每台员工机器。见第四节。

---

## 二、服务器要求

- Ubuntu 22.04+ / CentOS 8+
- Docker 24+ 且带 `docker compose` 插件(`docker compose version` 能出版本号)
- 4 vCPU / 8 GB 内存 / 100 GB 磁盘
- **不需要外网**,但员工机器要能访问服务器的 443 / 8998 / 8999 三个端口
- 装机账号在 `docker` 组里(`id -nG | grep docker`),否则每条命令都要 `sudo`

装机前确认 443 没被占用:

```bash
sudo ss -tlnp | grep ':443'
```

有输出的话先停掉(常见是 nginx / apache2),或者装机时用 `CATFISH_HTTPS_PORT=8443` 换端口。

---

## 三、服务端安装

```bash
tar xzf dahua-poc-FULL-amd64-20260728.tar.gz
cd delivery/dahua-poc/

SERVER_IP=<服务器内网IP> ENABLE_HTTPS=1 bash setup.sh
```

`SERVER_IP` 必须填**服务器真实内网 IP**(如 `10.10.40.50`)。

> ⚠ 不能填 `127.0.0.1` 或 `localhost` —— 员工浏览器会解析到自己的电脑,整个系统都用不了。

脚本会自动完成:生成配置 → 建内部 CA 和服务器证书 → 导入 8 个镜像 → 启动 → 自检。首次约 5–15 分钟(导入镜像最慢)。

**装完记下屏幕上这三项**,只显示这一次:

```
→ CATFISH_SECRET_KEY 已生成: xxxxxxxx        ← ★ 最重要,见下面
→ PG_PASSWORD 空 · 已生成随机: xxxxxxxx      ← 数据库密码
→ users.yaml 生成 · sysadmin: admin@catfish.com / catfish_2026
```

> ### ⚠⚠ `CATFISH_SECRET_KEY` 必须立刻存进公司密码管理器
>
> 它是**供应商 API key 的加密主密钥**。之后在「控制台 → 接入 → 供应商」
> 界面上填的每一个 API key,都是用它加密后存进数据库的。
>
> **这个值丢了,那些 key 全部解不开** —— 只能逐个去阿里云百炼、DeepSeek、
> Google 后台重新申请,再在界面上重填一遍。没有任何办法找回,程序也兜不住。
>
> 它在服务器的 `.env` 里也有一份,但**别只依赖那一份**:重装系统、换服务器、
> 误删目录都会一起没。
>
> 重跑 `setup.sh` 不会覆盖已有的值(那等于把所有 key 报废),所以升级和排障
> 时随便重跑。

结尾出现「装机完毕」和访问地址即成功。

### 聊天要能用,必须填模型 API key

不填的话**门户、登录一切正常**,唯独员工发第一条聊天消息时报「上游 LLM Provider 鉴权挂了」——极易误判成聊天功能坏了。

**推荐:在界面上填**(8/1 起)。登录 → 控制台 → 接入 → 供应商 → 选一家 →
填 API key → 保存。**立即生效,不用重启、不用改文件**。key 是加密存库的
(用上面那个 `CATFISH_SECRET_KEY`)。

也可以照老办法写 `.env`,但那样每次加供应商都要改文件 + 重建容器:

```bash
# 编辑 .env,填 DASHSCOPE_API_KEY=sk-...(阿里云百炼的 key)
docker compose up -d --force-recreate gateway
```

setup.sh 结尾会检查这项,空的会显眼提示。

### 换端口(443 被占且不能停)

```bash
SERVER_IP=<IP> ENABLE_HTTPS=1 CATFISH_HTTPS_PORT=8443 bash setup.sh
```

员工则访问 `https://<IP>:8443`。脚本会把端口同步进所有相关配置。

---

## 四、装机后验证

```bash
bash verify-login.sh
```

六步全绿即服务端就绪。任一步失败,脚本会直接说明断在哪、为什么、怎么修。

然后浏览器打开 `https://<IP>`,首次会提示证书不受信,点「高级 → 继续前往」,用 `admin@catfish.com / catfish_2026` 登录。**登录后立即改密。**

---

## 五、员工机器

### 1. 装 Companion

双击 dmg,拖进「应用程序」。

### 2. 装证书(必做)

把服务器上 `delivery/dahua-poc/certs/ca.pem` 发给员工,放到:

```
~/.catfish/server-ca.pem
```

> **是 `ca.pem`,不是 `cert.pem`。** `ca.pem` 十年有效只装一次;`cert.pem` 是服务器证书,每年会换。

不装的后果:Companion 仪表盘「中央门户」一直显示连不上,而**同一个地址浏览器能打开** —— 这个不对称极容易被误判成网络问题。原因是桌面端用 Rust 发请求,不认浏览器点过的「继续前往」。

规模化下发时,把 `ca.pem` 同时推进系统信任库(域控组策略 / Jamf / Intune),浏览器就不用每台每个浏览器手点一次了。

### 3. 配服务器地址

打开 Companion → 仪表盘 → 服务器配置 → 改,填三项:

| 字段 | 值 | 备注 |
|---|---|---|
| Gateway URL | `http://<IP>:8999` | **必须带端口**,必须是 `http://` |
| Identity URL | `http://<IP>:8998` | 同上 |
| 门户 URL | `https://<IP>` | 这项独立,改上面两个不会自动带上它 |

前两项走 http 是当前实现的限制(桌面端直连这两个端口,不经 HTTPS 反代)。

**填完必须退出 Companion 重新打开**才生效。

批量部署可以直接推配置文件 `~/.catfish/companion.yaml`:

```yaml
endpoints:
  gateway_url: http://10.10.40.50:8999
  web_url: https://10.10.40.50

oidc:
  issuer: http://10.10.40.50:8998
  client_id: catfish-companion
  audience: catfish-companion
  scope: openid email profile
```

---

## 六、日常维护

### 服务器证书续期(每年一次)

服务器证书 **397 天**到期。到期前在服务器上:

```bash
cd <装机目录>/delivery/dahua-poc/
SERVER_IP=<IP> ENABLE_HTTPS=1 bash setup.sh
```

脚本检测到 30 天内到期会自动重签。**CA 不变,员工机器上的 `ca.pem` 不用动。**

> 为什么不签十年:macOS / iOS 对 TLS 服务器证书有 398 天上限,超了直接拒绝连接,且跟证书受不受信任无关。

### 换服务器 IP

服务器端:

```bash
SERVER_IP=<新IP> ENABLE_HTTPS=1 bash setup.sh
```

服务器证书会自动重签,CA 不变。

**每台员工机器**(含你自己用来演示的那台)必须跟着改:仪表盘 →
服务器配置 → 改 → 填新的三个地址 → 保存 → 退出 Companion 重开。

保存这一下会把本机所有相关配置一次写全(含 hermes 那三个文件里的地址),
并作废绑定旧服务器的模型选择。**不要手工去改配置文件** —— 地址在本机有
多份副本,手工改必漏,漏了的表现是"登录成功但聊天没反应"这类相互矛盾的现象。

### 升级(换镜像)

```bash
# 传新的 FULL 包，解开覆盖
cd <装机目录>/delivery/dahua-poc/
SERVER_IP=<IP> ENABLE_HTTPS=1 bash setup.sh
```

`.env`、数据库、证书、用户都会保留。脚本会打印镜像 ID 变化,能看到到底换没换。

---

## 七、卡住时

| 现象 | 原因 / 处理 |
|---|---|
| 浏览器卡在「加载中…」不报错 | 用了 HTTP 裸 IP。前端登录需要安全上下文,必须 `ENABLE_HTTPS=1` |
| 报 `email 或密码错误` | 重装时 `users.yaml` 已存在会跳过,密码不会再显示。找装机时记的那个,或让已有管理员改密 |
| 四个服务都连不上数据库 | `.env` 里的 `PG_PASSWORD` 跟数据库里的对不上。数据库密码只在首次建库时写入,后来改 `.env` 无效。从 `.env.bak.*` 找回旧密码 |
| Companion 说门户连不上、浏览器却能开 | 没装 `ca.pem`,见第五节 |
| 聊天报「上游 LLM Provider 鉴权挂了」 | 服务器 `.env` 的 `DASHSCOPE_API_KEY` 空或过期,见第三节。门户能登录不代表 key 已配 |
| 员工机器装了 Clash / VPN | 内网地址被代理劫走。Companion 已默认绕过代理,浏览器需在代理软件里把服务器 IP 加进直连规则 |
| 装机脚本报 443 被占 | 按提示停掉占用方,或用 `CATFISH_HTTPS_PORT` 换端口 |

日志位置:

```bash
docker compose logs identity     # 登录相关
docker compose logs gateway      # 模型调用相关
docker compose logs web          # 页面 / 反代
```

Companion 日志:`~/Library/Logs/com.catfish.companion/`(macOS)。
