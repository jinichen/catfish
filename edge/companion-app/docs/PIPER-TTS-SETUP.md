# Piper TTS 部署 (BL-VOICE2, 5/10)

> 让鲶鱼说话 — 本地 CPU 跑, 数据 100% 不出员工电脑. 跟 whisper.cpp STT 对称.

## 🚀 一键装 (推荐, 鸿波 5/10 验证可用)

```bash
cd edge/companion-app
bash scripts/install-piper-tts.sh
```

脚本会:
1. 在 `~/.catfish/piper-venv/` 建 Python venv (绕开 PEP 668 + 不需要 brew install 任何东西)
2. `pip install piper-tts` 到 venv
3. 下默认中文女声 voice (`zh_CN-huayan-medium`, ~30MB) 到 `~/.catfish/piper-voices/`
4. 试合成一句话 + afplay 播放确认

Companion 自动探测 `~/.catfish/piper-venv/bin/piper`, 不需要改 yaml. 重启 Companion → AI 回答右下角 🔊 → 鲶鱼说话.

跳过 voice 下载 (要自己挑别的): `bash scripts/install-piper-tts.sh --no-voice`

## ⚠️ 装坑历史 (5/10 鸿波踩三连)

| 尝试 | 结果 | 真因 |
|---|---|---|
| `brew install piper-tts` | ❌ formula 不存在 | homebrew 主仓没收 piper |
| `brew install python3` (修上一条) | ❌ 7890 代理连不上 | 鸿波 mac 走本地 Clash/V2ray, 代理没起 |
| `pip3 install piper-tts` (用已有 py3.14) | ❌ PEP 668 拦 | Python 3.12+ + homebrew 禁直装系统 Python |

**venv 路子全绕开**: 不用 brew 装新东西, 不动 homebrew 系统 Python, 不依赖代理.

另外, rhasspy/piper 项目 2025-10 已 archive 迁到 `OHF-Voice/piper1-gpl`, 但 voice 模型仓库 (huggingface `rhasspy/piper-voices`) 仍可用, 模型格式不变. 如果 `pip install piper-tts` 失败, 脚本自动 fallback 试 `pip install piper1-gpl`.

## 方案对比

| 方案 | 数据归属 | 中文 | 包大小 | 央企友好度 |
|---|---|---|---|---|
| **Piper (我们选的)** | 本地 | zh_CN-huayan-medium | ~30MB | ✅ 高 |
| Edge TTS (Hermes 默认) | 微软 Azure 云 | XiaoxiaoNeural | 0 | ❌ 数据上云 |
| OpenAI TTS | OpenAI 云 + 收费 | gpt-4o-mini-tts | 0 | ❌ 数据上云 + $$$ |
| ElevenLabs | 云 + 收费 | Multilingual v2 | 0 | ❌ 同上 |

## 部署 (员工首次)

### macOS (5/10 P0 范围)

#### 1. 装 homebrew Python (如果还没装)

```bash
brew install python3
```

确认走的是 brew 的 python3:

```bash
which python3
# 应返 /opt/homebrew/bin/python3 (Apple Silicon) 或 /usr/local/bin/python3 (Intel)
```

如果返的是 `/usr/bin/python3` (系统自带), 在 `~/.zshrc` 顶上加:

```bash
export PATH="/opt/homebrew/bin:$PATH"   # Apple Silicon
# 或 export PATH="/usr/local/bin:$PATH" # Intel
```

`source ~/.zshrc` 重新载入.

#### 2. 装 piper-tts

**方案 A — 直接 pip (最快, 推荐)**:

```bash
pip3 install piper-tts
```

装完后 `piper` 命令应该在 `/opt/homebrew/bin/piper` (Apple Silicon).

**方案 B — pipx (Python deps 隔离, 不污染系统 Python)**:

```bash
brew install pipx
pipx ensurepath
pipx install piper-tts
```

pipx 装到 `~/.local/bin/piper` — Companion 探测路径里有, 可以认.

#### 3. 验证 piper

```bash
which piper
piper --help | head -5
```

预期看到 piper 的 usage 行 (`-m MODEL`, `-f OUTPUT_FILE` 等).

#### 4. 下载中文 voice 模型

```bash
mkdir -p ~/.catfish/piper-voices
cd ~/.catfish/piper-voices

# 默认: zh_CN-huayan-medium (女声, ~30MB)
curl -L -O \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx
curl -L -O \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx.json

# 可选: zh_CN-bizhao-medium (男声)
# curl -L -O https://huggingface.co/rhasspy/piper-voices/resolve/main/zh/zh_CN/bizhao/medium/zh_CN-bizhao-medium.onnx
# curl -L -O https://huggingface.co/rhasspy/piper-voices/resolve/main/zh/zh_CN/bizhao/medium/zh_CN-bizhao-medium.onnx.json
```

#### 5. 验证合成

```bash
echo "你好，我是鲶鱼，很高兴见到你。" | piper \
  -m ~/.catfish/piper-voices/zh_CN-huayan-medium.onnx \
  -f /tmp/test.wav
afplay /tmp/test.wav
```

听到声音就 OK.

#### 6. 重启 Companion

启动后, 任意 AI 回答右下角应该出现 🔊 喇叭按钮. 点击 → 鲶鱼说话.

### Windows / Linux (Phase 2)

未支持. 跟 whisper.cpp 同节奏: mac 先打通, Windows 后续 BL-VOICE2-WIN 把 piper 内置到 .exe bundle.

## 配置

`~/.catfish/companion.yaml`:

```yaml
tts:
  enabled: true
  voice: zh_CN-huayan-medium  # 文件名 (不含 .onnx 后缀)
  volume: 1.0                 # 0.0-1.0 (留扩展, 当前不生效)
```

或 env override (测试 / dev):

```bash
export CATFISH_PIPER=/usr/local/bin/piper
export CATFISH_PIPER_VOICE=zh_CN-bizhao-medium
export CATFISH_PIPER_MODEL=/path/to/some.onnx  # 完整路径 override voice 段
```

## Companion 探测路径

Rust `find_piper_executable()` 按顺序探:

1. `CATFISH_PIPER` env (override 全部)
2. `/opt/homebrew/bin/piper` (Apple Silicon brew Python pip)
3. `/usr/local/bin/piper` (Intel Mac brew Python pip)
4. `/opt/local/bin/piper` (MacPorts)
5. `~/.local/bin/piper` (pipx / pip --user)
6. `$PATH` 兜底 (GUI app 启动时一般 PATH 不全, 这条多半失效)

如果你装在别处, 设 `CATFISH_PIPER=/path/to/piper` 重启 Companion.

## Voice 选项

完整目录: <https://huggingface.co/rhasspy/piper-voices/tree/main/zh/zh_CN>

| Voice | 性别 | 音色 | 推荐场景 |
|---|---|---|---|
| `zh_CN-huayan-medium` | 女 | 标准普通话女声 | 默认 (温和友好) |
| `zh_CN-huayan-x_low` | 女 | 同上, 极低质量 | 嵌入式 / 实时性优先 |
| `zh_CN-bizhao-medium` | 男 | 标准普通话男声 | 老李等男性人设 |

⚠️ **中文 voice 当前 quality 上限 = medium** (5/10 鸿波踩坑后确认):
- HuggingFace `rhasspy/piper-voices/zh/zh_CN/` 下所有中文 voice 只有 `x_low` / `low` / `medium` 三档, **没有 high**
- 试图下 `zh_CN-huayan-high.onnx` 会拿到 15 字节 LFS pointer 假文件 → piper 启动报 `JSONDecodeError`
- 想要 high 听感得换英文 voice (en_US-lessac-high 等), 或等 OHF-Voice 训新中文 voice

英文 voice 有完整四档 (`x_low` / `low` / `medium` / `high`), 模型大小依次 ~5MB / ~15MB / ~30MB / ~80MB.

## 故障排查

### `pip install piper-tts` 失败

- **症状**: `Could not find a version that satisfies the requirement piper-phonemize`
- **真因**: piper-phonemize 1.1.0 没 macOS wheel, 旧版 pip 找不到
- **解法**:
  1. 升级 pip: `pip3 install --upgrade pip`
  2. 用最新 piper-tts (>= 1.2.0): `pip3 install --upgrade piper-tts`
  3. 还不行 → 换 OHF-Voice/piper1-gpl 仓库 fork: `pip3 install piper1-gpl`

### "piper 二进制找不到"

- 装了 `pip install piper-tts` 但 Companion 仍报错 → macOS GUI app 启动 PATH 不含 brew, 跟 ffmpeg / whisper-cli 同款问题.
- 解法 1: Companion 自动探测 6 种路径 (见上表). 95% case 自动认.
- 解法 2: 设 `CATFISH_PIPER=$(which piper)` 在 `~/.catfish/companion.yaml` 或 env, 重启.

### "piper voice 模型未下载"

- 检查 `~/.catfish/piper-voices/zh_CN-huayan-medium.onnx` + `.onnx.json` 都在 (两个文件缺一不可).
- 文件下到一半失败 → `ls -la` 看 size, 重新 curl. medium 大约 30MB, .json 大约 5KB.

### "audio 播放失败 (CSP / asset protocol scope?)"

- Tauri 有 CSP + assetProtocol scope 双重保护, scope 列表见 `src-tauri/tauri.conf.json` `assetProtocol.scope`.
- 如果改了 wav 临时文件路径模板 (Rust `commands/tts.rs` `wav_path`), 要同步更新 scope.

### 合成出来声音"模糊 / 卡顿"

- 用 `medium` 而不是 `x_low`. `x_low` 是超低质量给嵌入式用的.
- 或上 `high` (~80MB), CPU 负担略高但 M 系列 mac 不影响.

## 集成点 (代码索引)

| 文件 | 作用 |
|---|---|
| `src-tauri/src/commands/tts.rs` | piper subprocess 调用 + 模型探测 |
| `src/lib/tts.ts` | 前端 invoke + audio 播放 helper |
| `src/components/TTSButton.tsx` | 🔊 按钮组件 |
| `src/tabs/Chat/ChatMessage.tsx` | 在 AI 消息底部插 TTSButton |
| `src-tauri/tauri.conf.json` | CSP `media-src` + assetProtocol scope |

## Roadmap

| 任务 | 状态 |
|---|---|
| BL-VOICE2 P0 — mac subprocess + ChatMessage 喇叭 | ✅ 5/10 |
| BL-VOICE2 fix1 — pip install 真路子 + ~/.local/bin 探测 | ✅ 5/10 (本文档) |
| BL-VOICE2-WIN — Windows 打包 piper.exe + bundle resource | ⬜ |
| BL-VOICE2-PET — 桌宠嘴巴动画跟音频同步 | ⬜ |
| BL-VOICE2-PRO — Proactive 闲聊触发 → 自动播 (员工 opt-in) | ⬜ |
| BL-VOICE2-AGENT — AgentPrefsCard 加 voice 选择器 + 试听 | ⬜ |
