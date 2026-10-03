# 小鲶会议纪要 (macOS) — 实施方案

> 10/1 定稿。鸿波拍板: Chromium 保持内嵌; 旧录音并入会议功能、删除 whisper;
> 参会人数必填; 音频默认留 7 天; P1 不做实时字幕; 先只支持 Apple Silicon。

## 1. 现状 (代码核实过的)

| 事实 | 位置 |
|---|---|
| 录音最长 5 分钟 (`-t 300`) | `companion-app/src-tauri/src/commands/speech.rs:129` |
| 录音设备写死 avfoundation 序号 `:0`, 注释说"默认设备"是错的 —— 开发机上 0 是 iPhone 连续互通麦克风, 内置麦克风是 1 | `speech.rs:125` |
| 转写 = whisper-cli + ggml-small, 纯文本, 无时间戳 / 说话人 / 切块 | `speech.rs:212-258` |
| ffmpeg / whisper-cli / 模型都**不在安装包里**, 要员工 brew 装 → 客户机器上 🎤 和音频转写基本用不了 | `tauri.*.conf.json`, `build-mac-resources.sh` 无任何 ffmpeg/whisper |
| Windows 录音是 stub, 直接报"仅 macOS" | `speech.rs:425-461` |
| 录屏学习模式 (RecMode) 的旁白复用同一个 5 分钟录音 | `RecordingOverlay.tsx:71-82` |
| 中央分发服务只是占位 | `central/distribution/README.md` |

使用情况 (开发机日志, 8/8 起): 聊天 🎤 3 次; RecMode 6 次, 最后一次 6/23。

## 2. 选型依据 (本机实测, M4 / 16G, CPU)

FunASR: FSMN-VAD + SeACo-Paraformer (支持热词) + CT-Transformer 标点 + CAM++ 说话人, 全部 Apache-2.0。

| 项 | 结果 |
|---|---|
| 2 分钟 4 人样本 | 15s 转完 (~8× 实时), 分出 4 人, 文本/标点质量好 |
| 30 分钟 | 3.5 分钟; 加载 24s; 峰值内存 3.7GB |
| 说话人, 不给人数 | **30 分钟时分出 42 人** (实际 4 人) |
| 说话人, `preset_spk_num=4` | 4 人; 同一人跨时段标签一致 ~92% |
| 体积 | torch 等 ~0.65GB + 模型 2.1GB (识别 954M / 标点 1.1G / 说话人 28M / VAD 4M) |

→ 参会人数必填; 组件包 ~2.7GB 不进安装包 (现在安装包 ~600MB)。

文档对比 (阿里官方图, 读图近似): WenetSpeech 会议集字错率 whisper-small ~24.8% vs Paraformer-zh ~6.9%。

## 3. 架构

```
录音 (Rust, cpal) ──► ~/.catfish/meetings/<id>/audio/*.wav (5 分钟一片)
        │
        ▼ 会后
转写 (独立 Python 环境 ~/.catfish/meeting-asr/venv, 按需起子进程)
        │  输入: 切片 + 参会人数 + 热词 (wiki 人物/项目标题+别名 + 手填)
        ▼  输出: transcript.json (段: spk / start / end / text)
说话人改名 (界面)
        │
        ▼
纪要 (tool-bridge `meeting/*` RPC → 中央大模型, 员工当前选的模型)
        │  输出: 摘要 / 决议 / 待办(负责人·事项·截止) / 待定问题 → minutes.md
        ▼
写回 (用户确认): 待办 → 任务库 (source=meeting, 幂等); 存知识库 → wiki_ingest
```

要点:

- **录音用 Rust (cpal) 直接录**, 不再依赖 brew 的 ffmpeg; 设备按名字选 (序号会变)。
  聊天 🎤、RecMode 旁白、会议共用这一个录音模块。
- **转写环境独立**, 不进 `hermes-extra-packages.txt` —— 那份清单是全有或全无,
  且每次启动都 import 检查, 加 torch 会拖慢所有人的启动。
- **纪要走 RPC 白名单** (照 `recmode_rpc` 的 `recmode/` 前缀写法), 不进小鲶的工具列表。
- **数据**: 音频 / 转写 / 纪要只在 `~/.catfish/meetings/`; 只有转写文字经网关去大模型 (网关不落盘,
  符合 CENTRAL-EDGE-DATA-BOUNDARY)。隐私卡片加这一项。
- **删除 whisper**: `speech.rs` 的 whisper 链路、`parse_file_audio.py`、聊天上传音频
  (`transcribe_audio_from_b64`) 全部改走会议转写; 组件包没装时 🎤 提示去装。
  上传的音频文件 = "导入一场会议"。

## 4. 组件分发 (P0)

内嵌 vs 服务器按"启动必需 vs 功能可选"切:

- 留在安装包: Python / uv / hermes / hermes 依赖 / node / Chromium (启动必需或鸿波定为必须)。
- 放中央: 会议组件包, 以及以后的 bge-m3 向量模型、语音包等 (现在都要员工手动 curl)。

中央: catfish-web 的 nginx 加 `location /components/` 静态托管, 目录挂载到宿主机;
`manifest.json` 记每个组件的 name / version / platform / file / size / sha256。

客户端: 读 manifest → 断点续传下载到 `~/.catfish/runtime/` → 校验 sha256 → 原子改名。
断网机器沿用"IT 手动把包放进 `~/.catfish/runtime/`"(现有解析逻辑已支持该目录)。

## 5. 分期

| 期 | 内容 |
|---|---|
| P0 | 组件分发: 中央 `/components/` + manifest 生成脚本; 客户端下载器 (续传 / 校验 / 进度) |
| P1 | 录音模块 (cpal, 设备选择, 分片, 4 小时上限) + 修 🎤 设备问题; 会议组件包构建与安装; 转写脚本; 说话人改名; 纪要; 写回; 「会议」页; 删除 whisper 链路 |
| P2 | 体积: 小标点模型 / ONNX 量化 (去 torch) 评估 |
| P3 | 实时字幕 (流式 Paraformer)、线上会议录系统声音 (Windows 会后转写 10/2 已提前做完) |

每期带单测; P1 最后用一场真实会议做端到端验收。

## 6. 已知风险

- 峰值内存 3.7GB / 30 分钟: 16G 机器开很多应用时可能吃紧 → 转写在会后跑, 进程降优先级, 界面提示。
- 说话人编号是"本场录音内匿名", 不是人物识别; 抢话片段会错, 由人改名 + 大模型按上下文纠正。
- 1–4 的速度数据来自 M4; 更老的 M1 需要实测。
- manifest 的 sha256 只防损坏不防篡改 (同一通道下发); 内网 HTTPS + 中央证书信任先顶着, 签名 (minisign) 后续加。

## 7. 进度 (10/1)

| 提交 | 内容 |
|---|---|
| 3c09db7 / 02321ae | P0 组件分发 (中央 /components/ + 开发机 vite 同路径 + 客户端下载器) |
| f370c5b | 进程内录音 (cpal, 选设备, 分片防崩, 4h 上限) + 签名加 audio-input |
| 3efc6fb | 会议页: 组件包构建 / 安装、本机转写、说话人改名、纪要、写回任务库 / 知识库 |
| 99cdfa8 | 聊天 🎤 / 上传音频 / 录屏旁白 / 文件解析改走新录音 + 会议组件包; 删 whisper / ffmpeg 依赖 |
| (10/2) | Windows: 组件包 (CI 打 + 自检)、PyAV 解码、🎤 / 上传音频 / 文件解析开放 |
| (10/3) | 纪要自定义模版: 员工存 Markdown 模版 (~/.catfish/meeting-templates/), 生成时可选; 缺省仍是原格式。结构化抽取照旧 (待办加任务库不受影响), 多一次大模型调用按模版写 minutes.md |
| (10/3) | 单位固定的 Word / Excel 纪要表原样上传: 认空 (标签格旁空格 / 表头下空行 / 「xx：」/ {{占位符}}) → 大模型给每个空出值 → 代码填进原文件另存, 格式不动; 待办表直接用结构化待办填 (meeting_file_template.py) |

已知后续:
- 🎤 冷启动 ~18 秒 (加载模型 ~17 秒, 跟原来 whisper medium 16 秒相当)。常驻转写进程可以降到 1 秒内,
  代价是常驻 ~2GB 内存 —— 要不要做、空闲多久释放, 待定。
- ogg vorbis / wma 这类 afconvert 解不了的格式会提示换格式。
- Windows (10/2 补上): 录音 (cpal) + 转写 (同一份 meeting_asr.py) + 解码 (组件包里的 PyAV,
  `meeting_asr.py --decode`; mac 继续用系统 afconvert)。Windows 版组件包由
  `.github/workflows/build-meeting-asr-pack.yml` 在 windows-latest 上打, 自检含"离线装 → 分说话人转写
  → m4a 解码后不分说话人转写, 文字跟原 wav 一致"。聊天 🎤 / 上传音频 / 文件解析里的音频跟着可用。
