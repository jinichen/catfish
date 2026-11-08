# 安装说明

> 版本 20260728 · 全流程已在 Ubuntu 24.04 实机验证

---

## 一、交付物

| 文件 | 给谁 | 说明 |
|---|---|---|
| `catfish-poc-FULL-<arch>-20260728.tar.gz` | 服务器 | 一体包,含全部镜像 + 安装脚本,不依赖外网 |
| `Catfish-Companion-0.20.0-aarch64.dmg` | 员工机器 | 桌面端,**当前仅 Apple Silicon**;Intel Mac 需另 build x64 dmg |

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

### Linux / macOS

```bash
tar xzf catfish-poc-FULL-amd64-20260728.tar.gz
cd delivery/catfish-poc/

SERVER_IP=<服务器内网IP> ENABLE_HTTPS=1 bash setup.sh
```

### Windows（不需要装 Docker，推荐）

```powershell
tar xzf catfish-poc-FULL-amd64-<日期>.tar.gz      # Windows 10 1803+ 自带 tar
cd delivery\catfish-poc\

powershell -ExecutionPolicy Bypass -File .\setup.ps1 -ServerIp <服务器内网IP>
```

`-ExecutionPolicy Bypass` 只对这一次调用生效，不改系统设置。

机器上**没有 Docker** 时，`setup.ps1` 自动改走 Windows 自带的 WSL2（`windows\setup-wsl.ps1`）：
包里带了一个装好 Docker 引擎的 Linux 和 WSL 离线安装包，全程不联网，也**不需要 Docker Desktop**
（Docker Desktop 对员工超过 250 人或年收入超过 1000 万美元的公司要收费）。

| 要求 | 说明 |
|---|---|
| 系统 | Windows 10 21H2 及以上 / Windows 11 / **Windows Server 2022** 及以上（Server 2019 及更早不支持 WSL2） |
| 管理员 | 脚本会自己弹「是否允许」提权 |
| CPU 虚拟化 | BIOS 里开 Intel VT-x / AMD-V；本身是虚拟机的话，宿主机要开「嵌套虚拟化」 |
| 重启一次 | 首次要开 Windows 功能，重启**登录后自动接着装** |
| 账户密码 | 设开机自启时要输一次当前账户的 Windows 密码（服务器重启后不登录也能自动起来）；只用 PIN 的账户先设个密码 |
| 磁盘 | 默认装到 `C:\CatfishCentral`，至少 20 GB；换盘加 `-InstallDir D:\CatfishCentral` |

它做的事：开 WSL2 → 装 WSL（离线 MSI）→ 导入 Linux → 在里面跑**跟 Linux 服务器同一个 `setup.sh`**
→ 设开机自启（计划任务 `CatfishCentral`）→ 把 443 / 5173 / 8998 / 8999 从本机 IP 转进去并放行防火墙。

装好以后：

- 数据（数据库、上传文件）都在 `C:\CatfishCentral\wsl\ext4.vhdx`，备份这个目录
- 日志：`C:\CatfishCentral\logs\boot.log`；服务日志 `wsl -d CatfishCentral -u root -- docker logs catfish-gateway`
- 停：`Stop-ScheduledTask CatfishCentral; wsl --terminate CatfishCentral`；起：`Start-ScheduledTask CatfishCentral`
- 443 被 IIS 占着时脚本会停下并说明，先停掉 IIS（`Stop-Service W3SVC`）

### Windows（已经装了 Docker Desktop）

机器上有 Docker Desktop 时 `setup.ps1` 照旧用它，命令同上。装机前确认 **Docker Desktop 已经启动**
（托盘图标不再转）——装了不等于起了，这是这条路上最常见的一种失败，表现为一堆看不懂的 docker 报错。

> ⚠ 不要用 `central\deploy.ps1`。那个脚本会跑 `docker compose build`，而交付包里
> 只有镜像没有源码，必然失败；它是给我们自己有源码的机器用的。客户机器上用的是
> 这个目录下的 `setup.ps1`。

两个平台做的事完全一样：密钥生成、证书签发、配置写入这些判断都在 `tools/` 下的
共享脚本里，跑在 `catfish-identity` 镜像里执行，所以宿主机不需要装 openssl 或
Python——有 Docker 就够了。

`SERVER_IP` 必须填**服务器真实内网 IP**(如 `10.10.40.50`)。

> ⚠ 不能填 `127.0.0.1` 或 `localhost` —— 员工浏览器会解析到自己的电脑,整个系统都用不了。

脚本会自动完成:生成配置 → 建内部 CA 和服务器证书 → 导入 7 个镜像 → 启动 → 自检。首次约 5–15 分钟(导入镜像最慢)。

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

### 直接敲 IP 自动跳到 HTTPS (80 端口)

HTTPS 模式下, 员工在浏览器里直接敲 `http://<IP>` (走 80 端口) 会自动跳到 `https://<IP>`。
80 被别的程序 (nginx / apache / IIS) 占着时, 装机脚本不开这个跳转、打印一句说明, 不影响门户本身 —— 员工直接用 `https://<IP>`。
要换跳转口: `.env` 里 `CATFISH_HTTP_REDIRECT_PORT=8080`; 不要跳转: `CATFISH_HTTP_REDIRECT_PORT=0`。
(员工接入链接 `http://<IP>:5173/connect.html` 不受影响, 照常不跳。)

### 换端口(443 被占且不能停)

```bash
SERVER_IP=<IP> ENABLE_HTTPS=1 CATFISH_HTTPS_PORT=8443 bash setup.sh
```

员工则访问 `https://<IP>:8443`。脚本会把端口同步进所有相关配置。

### 用域名 / 正式证书(IP 和域名同时能用)

默认只有内网 IP:员工用 `https://<IP>`,证书是装机时生成的内部证书。加 `PUBLIC_HOST` 后,**IP 照旧能用,域名也能用**,两边都能登录(每个地址各有一个登录地址 / OIDC issuer,identity、网关、门户都认)。

三种装法(Windows 对应 `-PublicHost` / `-TlsCert` / `-TlsKey`):

```bash
# 1. 只有 IP (跟以前一样, 什么都不用加)
SERVER_IP=192.168.31.199 bash setup.sh

# 2. IP + 域名, 都用内部证书 (内部证书的 SAN 自动加上域名)
SERVER_IP=192.168.31.199 PUBLIC_HOST=catfish.example.com bash setup.sh

# 3. IP + 域名, 域名用正式证书 (公网 CA 签的; 浏览器 / 手机什么都不用装)
SERVER_IP=192.168.31.199 PUBLIC_HOST=catfish.example.com \
  TLS_CERT=/root/ssl/fullchain.pem TLS_KEY=/root/ssl/privkey.pem bash setup.sh
```

- `PUBLIC_HOST` 可以写多个,逗号分隔:`PUBLIC_HOST=catfish.example.com,m.example.com`。必须走 HTTPS(默认就是)。
- `TLS_CERT` 是**完整链**(服务器证书 + 中间证书,证书供应商一般叫 fullchain / 「Nginx 格式」),`TLS_KEY` 是不带密码的私钥。装机脚本先检查:私钥跟证书是不是一对、证书包不包含每个域名(支持 `*.example.com`)、有没有过期;不合格直接停下并说明原因,什么都不改。只有服务器证书没有中间证书、30 天内到期会提醒。合格的拷到 `certs/official/`。
- 正式证书只给域名用(公网 CA 不给 IP 签证书)。IP 访问仍用内部证书,员工照旧用接入码。
- 选择会记进 `.env`(`CATFISH_PUBLIC_HOSTS`),以后重跑 / `UPGRADE=1` 不写也沿用,`certs/official/` 里的正式证书也沿用。不要域名了:`PUBLIC_HOST=none bash setup.sh`。

**DNS**:内网 DNS 把域名解析到服务器内网 IP(内网员工用域名时直连);外网要用的话,公网 DNS 指到公司出口,出口把 443 映射到服务器的 443。装机脚本在服务器上模拟外面的请求验证(不依赖 DNS),并打印要找网管做的事。

**员工怎么连**:

| 员工在哪 | 门户地址 | Companion 接入 |
|---|---|---|
| 公司内网 | `https://<IP>` | 跟以前一样:接入链接 `http://<IP>:5173/connect.html?…` + 接入码 |
| 用域名 / 在公司外面 | `https://<域名>` | 装机脚本另打一段域名的接入链接(`https://<域名>/connect.html?…`),网关和登录都走这个 https 地址(外面只开了这一个端口)。正式证书时不用接入码也能连;带着也无害 |

门户「后台 → 用户」页顶部会把每个地址的接入链接都列出来,一键复制。

**证书续期**:内部证书照旧每天自动检查、到期前自动重签(域名也在里面)。**正式证书不会自动续**——门户「服务状态」会显示它的到期日,30 天内标黄。拿到新证书后:

```bash
UPGRADE=1 TLS_CERT=/root/ssl/new-fullchain.pem TLS_KEY=/root/ssl/new-privkey.pem bash setup.sh
```

### 内网部署 + 公网映射(NAT)

服务器只有内网地址(例 `192.168.31.199`),外面通过公司路由器 / 防火墙的端口映射进来,而且对外端口可能不一样(例 公网 `203.0.113.7:8443` → 内网 `192.168.31.199:443`)。`PUBLIC_HOST` 里写**外面实际打开的地址和端口**:

```bash
SERVER_IP=192.168.31.199 PUBLIC_HOST=203.0.113.7:8443 bash setup.sh
# 有域名的话一起写:
SERVER_IP=192.168.31.199 PUBLIC_HOST=203.0.113.7:8443,catfish.example.com bash setup.sh
```

外面的人用 `https://203.0.113.7:8443` 登录,登录地址(issuer)就是它;内网员工照旧 `https://192.168.31.199`。端口不写就等于服务器自己的 HTTPS 端口(默认 443)。

交给网管的映射表:

| 公网(外面打开的) | 映射到(服务器) | 说明 |
|---|---|---|
| `203.0.113.7:8443` TCP | `192.168.31.199:443` | 门户 + 登录 + 网关 + 手机,全走这一个端口 |
| `catfish.example.com` 的 443(有域名时) | `192.168.31.199:443` | 公网 DNS 指到公司出口 |

8998 / 8999 / 5173 **不用**映射出去。映射好以后从外网打开 `https://203.0.113.7:8443/.well-known/openid-configuration`,应该看到 `"issuer":"https://203.0.113.7:8443"`(装机脚本结尾也会打印这句)。

公网 IP 拿不到正式证书,外面的人第一次用浏览器打开会提示证书不受信;Companion 用接入码装一次内部 CA 即可。要免警告就用域名 + 正式证书。

> ⚠ 安全:这样做是**把整个门户(含登录页)开到公网**。优先用域名 + 正式证书;防火墙上能限来源 IP 就限;管理员账号改强密码。如果只是想让员工手机在外面用,更稳的是只开手机中转站(见下面「中转站单独放 DMZ」,`docker-compose.relay.yml`),中央本体不出内网。

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

### 2. 装证书(必做,用接入码,不用传文件)

装机脚本最后会打印一行**接入码**(也写在服务器 `certs/pairing-code.txt`,门户「后台 → 用户」页顶部也能看到并一键复制):

```
门户地址  https://<IP>
接入码    3F9A-1C2B-77D0-9E41
```

脚本同时打印一条**接入链接**(`http://<IP>:5173/connect.html?web=…&code=…`),门户「用户」页的「复制接入链接发给员工」复制的也是这一段。把它发到员工群里:

1. 员工装好 Companion,点接入链接,浏览器打开一个说明页,点「打开鲶鱼 Companion」;
2. Companion 弹出确认框,门户 / 网关 / 认证地址和接入码都已填好,员工确认是公司 IT 发的,点「连接」;
3. Companion 自己去门户取公司证书,**指纹跟接入码对上才信任**,然后保存配置、重新登录。

点了没反应(比如没装好 Companion),说明页上可以「复制接入链接」(`catfish://connect?…`),粘到 Companion「服务器配置」的「接入链接」框,几项会自动填好。也可以手填门户地址和接入码。接入码对不上不导入,会提示「可能连错了服务器」。

链接里不带网关 / 认证地址时,Companion 按门户主机 + `8999` / `8998` 端口推出来(跟下面「配服务器地址」那张表一致)。

接入码是内部 CA 的指纹:CA 十年不变,接入码也不变;服务器证书每年重签不影响它。

老办法仍可用(中央还没升级到带接入码的版本时):把服务器上 `delivery/catfish-poc/certs/ca.pem` 发给员工,在仪表盘「中央门户 → 查看检测详情」里选择文件导入,或直接放到 `~/.catfish/server-ca.pem`。**是 `ca.pem`,不是 `cert.pem`。**

不装的后果:Companion 仪表盘「中央门户」一直显示连不上,而**同一个地址浏览器能打开** —— 这个不对称极容易被误判成网络问题。原因是桌面端用 Rust 发请求,不认浏览器点过的「继续前往」。

规模化下发时,把 `ca.pem` 同时推进系统信任库(域控组策略 / Jamf / Intune),浏览器就不用每台每个浏览器手点一次了。

### 3. 配服务器地址

打开 Companion → 仪表盘 → 服务器配置 → 改,填三项:

| 字段 | 值 | 备注 |
|---|---|---|
| Gateway URL | `http://<IP>:8999` | **必须带端口**,必须是 `http://` |
| Identity URL | `http://<IP>:8998` | 同上 |
| 门户 URL | `https://<IP>` | 这项独立,改上面两个不会自动带上它 |
| 接入码 | 装机脚本打印的那串 | 填了 https 门户 URL 才出现;保存时自动取证书并核对,已装过证书可不填 |

内网用 IP 时前两项走 http 直连这两个端口。**用域名 / 在公司外面**(见第三节「用域名 / 正式证书」)时三项都填 `https://<域名>`(公网映射填 `https://<公网IP>:<对外端口>`):外面只开了门户这一个端口,网关和登录都走门户的 https 反代。接入链接会自动这样填。

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

### 4. 手机访问(可选)

员工可以在手机上跟**自己电脑上的小鲶**对话、批准小鲶要做的事(只能单次批准)、看早安简报。
内容在手机和员工电脑之间端到端加密,中央只转发、不保存、看不到内容。员工电脑关机或休眠时手机连不上。

**默认就装好了**:中转站在中央网关里(`/relay/ws`),手机网页在门户的 `/m`,不多装服务。

- 员工电脑:Companion → 设置 → 隐私 / 本机数据 → 📱 手机 → 打开「允许手机连这台电脑」(默认关)。
- 手机:浏览器打开 `https://<服务器IP或域名>/m`,单位账号登录,选电脑 → 配对 → 电脑上弹出 6 位验证码,跟手机上一样就点允许。
- 每人最多配几台手机:`.env` 里 `CATFISH_MOBILE_MAX_DEVICES`(默认 3;填 0 = 不开放手机端),改完 `docker compose up -d gateway`。
- 必须是 https:手机浏览器只在安全连接下提供加密功能,http 打开会直接提示。

**手机在公司外面怎么连**,由 IT 二选一:

1. 员工手机连 VPN,跟在公司里一样用。
2. 把门户的 `/m`、登录页和 `/relay/` 经反向代理开放到公网,其余路径不开放(`relay-dmz/nginx.conf` 就是这样一份白名单,可以照着改)。

> 外网登录:手机在外网要能打开登录页,所以中央装机时要带上外网能访问的地址 `PUBLIC_HOST=<域名>`(见第三节「用域名 / 正式证书」)。11/08 起 IP 和域名可以同时登录:员工电脑在内网照旧用 IP,手机在外网用域名,各自的登录地址都认。只在内网 Wi-Fi / VPN 下用手机就不用配。

#### 中转站单独放 DMZ(客户不愿意把中央任何路径开到公网时)

同一个网关镜像的「只中转」模式(`CATFISH_ROLE=relay`):只有 `/relay/ws` 和 `/healthz`,不带模型、配额、审计等接口,不碰用户数据。

1. DMZ 机器上导入跟中央同一个镜像包,解开本目录。
2. `cp relay-dmz/relay.env.example relay-dmz/relay.env`,填中央数据库地址、登录服务地址(中央 `.env` 里 `CATFISH_OIDC_ISSUERS` 那一整串;中央装机时要带 `PUBLIC_HOST=<对外域名>`)、手机上限。
3. 证书放到 `relay-dmz/certs/server.crt` 和 `server.key`(对外域名的证书)。
4. 改 `relay-dmz/nginx.conf` 里的 `<中央内网地址>`。
5. `docker compose -f docker-compose.relay.yml up -d`,`curl -k https://<对外域名>/healthz` 回 `{"status":"ok","role":"relay"}` 就好了。

网络规则:DMZ → 中央 PostgreSQL 5432、中央门户 443、identity 8998;员工电脑 → DMZ 443。
员工电脑的「服务器配置」里网关地址仍填中央;手机通道会连**同一个网关地址**下的 `/relay/ws` ——
所以用 DMZ 方案时,中央网关的 `/relay/ws` 也保留(员工电脑在内网连它),两边靠同一个数据库互通。

---

## 六、日常维护

### 服务器证书续期(每年一次)

服务器证书 **397 天**到期。到期前在服务器上:

```bash
cd <装机目录>/delivery/catfish-poc/
SERVER_IP=<IP> ENABLE_HTTPS=1 bash setup.sh
```

脚本检测到 30 天内到期会自动重签。**CA 不变,员工机器上的 `ca.pem` 不用动。**

域名用的正式证书(`certs/official/`)不在自动续期范围里,换法见第三节「用域名 / 正式证书」。

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
cd <装机目录>/delivery/catfish-poc/
UPGRADE=1 SERVER_IP=<IP> ENABLE_HTTPS=1 bash setup.sh
```

Windows（WSL2 装法）：新包解开后在新目录里跑

```powershell
powershell -ExecutionPolicy Bypass -File .\windows\setup-wsl.ps1 -Upgrade 1
```

服务器 IP 沿用上次的（记在 `C:\CatfishCentral\catfish-wsl.json`）。

`UPGRADE=1` 不会用 `.env.example` 覆盖现场配置；数据库密码、JWT、加密主密钥、API key 和外置 Identity 地址都会保留，部署 IP/OIDC/HTTPS/worker 等派生参数会按本次参数更新。数据库卷、证书、用户都会保留。脚本会打印镜像 ID 变化,能看到到底换没换。

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
| 用域名打开报证书错误,IP 正常 | 正式证书过期 / 没覆盖这个域名 —— 门户「服务状态」看到期日;换证书见第三节「用域名 / 正式证书」 |
| 外网打开域名 / 公网 IP 登录报 issuer 不一致 | 外面打开的地址跟 `PUBLIC_HOST` 不完全一样(端口也算)。按外面实际的 `主机:端口` 重跑 `PUBLIC_HOST=… bash setup.sh` |

日志位置:

```bash
docker compose logs identity     # 登录相关
docker compose logs gateway      # 模型调用相关
docker compose logs web          # 页面 / 反代
```

Companion 日志:`~/Library/Logs/com.catfish.companion/`(macOS)。
