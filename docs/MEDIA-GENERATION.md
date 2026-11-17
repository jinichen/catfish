# 小鲶出图 / 出视频 (2026-11-17, Companion 1.2.202)

> 文生图、图生图 (改图)、文生视频、图生视频。跟对话模型一样是中央「模型」页上的配置, 代码里不写死型号。

## 为什么要做

之前小鲶基本不会出图: Hermes 自带的 `image_generate` 走 FAL.ai, 要员工自己的海外 key, 员工机上没有, 工具从没真正
调用过; 视频完全没有。员工让它「做张图」, 它只能用 Python 画图表或拼 HTML 海报。

## 怎么接的

```
小鲶 (Hermes 自带 image_generate / video_generate)
  └─ 供应商 catfish  (插件 catfish-xcatfish-user/media_gen.py)
       └─ 中央网关  POST /v1/images/generations   出图 (带原图 = 改图), 同步
                    POST /v1/videos → GET /v1/videos/{id} → GET /v1/videos/{id}/content   出视频, 异步
            └─ 按用途挑模型 → 上游 (百炼原生接口 / 其它厂商走 LiteLLM)
```

- **不另开工具**: 用 Hermes 自带的两个工具, 只换背后的供应商。模型收到的工具从 44 个变 46 个 (视频工具集原来默认关),
  离 Qwen 50+ 撞空 400 的线还有余量。工具说明由 Hermes 按供应商报的能力动态生成 (能不能改图、时长范围、清晰度、配音)。
- **Companion 规定配置** (hermes_policy_config.rs, 只 v0.23+): `image_gen.provider` / `video_gen.provider` = `catfish`;
  `platform_toolsets` 的 api_server / weixin / cli / cron 加 `video_gen` (没配过的写成「平台默认 + video_gen」, v0.23.2 实测只多出
  video_gen, 别的一个不少)。
- **结果**存 `~/.catfish/outputs/小鲶出图-*.png` / `小鲶改图-*.png` / `小鲶视频-*.mp4`, Companion 右栏自动打开 (这次给右栏加了视频)。

## 中央怎么配

「模型」页的「用途」多了四种: 文生图、图生图 (改图)、文生视频、图生视频。每种用途挂「默认」的那个给员工用; 员工没权限
(部门的可用模型) 或 key 没配时, 落到同用途的下一个。

| 字段 | 说明 |
|---|---|
| `upstream.model` | `dashscope/<型号>` = 阿里百炼原生接口; 别家照对话模型的写法 (`openai/gpt-image-1` 等, 走 LiteLLM) |
| `upstream.provider` | 复用供应商的地址和 key。百炼供应商配的兼容地址 `…/compatible-mode/v1` 自动换成原生的 `…/api/v1` |
| `upstream.timeout` | 图片建议 280 秒 (改图实测 2–3 分钟; 中央 nginx 300 秒) |
| `price_per_unit` | 元 / 张 或 元 / 秒。不填审计页按 0 算并标「缺单价」 |
| `media.params` | 默认带给上游的参数 (如 `{"watermark": false}`), 员工请求里的同名参数优先; 强制的写 `upstream.param_overrides` |
| `media.max_input_images` | 改图最多几张原图 (百炼 Qwen-Image 1–3) |
| `media.default_duration` / `min_duration` / `max_duration` | 视频时长, 超出夹到范围内 |
| `media.resolutions` | 视频清晰度档位, 第一个是默认 |
| `media.size_param` | 视频画面大小怎么传: `size` (宽*高, 按档位 + 画幅查表, 万相 2.x 文生)、`resolution` (只给档位, 万相 2.x 图生)、`resolution_ratio` (档位 + 画幅比, 万相 3.0; 带首帧时 ratio=adaptive)。默认文生 size、图生 resolution |
| `media.image_input` | 首帧图怎么传: `img_url` (万相 2.x, 默认) 或 `first_frame` (放 `input.media`, 万相 3.0) |
| `media.audio` | 模型能不能出带声音的视频 (能的话员工端可以选) |
| `media.sizes` | 覆盖内置尺寸表: 图片 `{"landscape": "1664*928"}`, 视频 `{"720P/16:9": "1280*720"}` |

计费: 这类请求 `quota_events` 记 0 token, 张数 / 秒数记在新列 `media_units`, 审计页按单价算。视频成功那一次才记账
(media_jobs.metered, 员工端反复轮询只记一次)。员工当天 token 配额用完时也不能出图 / 出视频。

## 数据边界

- 提示词、图片、视频内容中央都不存 (跟对话审计同一条)。media_jobs 只有谁、哪个模型、上游任务号、状态、秒数, 30 天后删。
- 改图 / 图生视频要把本机原图发到中央、再到上游。这一跳在「数据外发记录」里标「出图 / 改图」「出视频」(带原图内容, 防篡改链照常)。
- 单张原图上限 10MB, 员工端和中央各卡一道。

## 实测 (11/17, 本机中央 + 百炼)

本机百炼供应商是 **Token Plan** 地址 (`token-plan.cn-beijing.maas.aliyuncs.com`), 它的 key 在标准地址上无效, 套餐也不含
qwen-image-2.0 / wan2.6 / wan2.7-t2v (`AccessDenied.Unpurchased` / `Model not exist`), 但含 **qwen-image-3.0-pro、
wan2.7-image-pro、wan3.0-video** —— 本机四种用途改配成 qwen-image-3.0-pro (文生图 + 改图) 和 wan3.0-video (文生 + 图生视频),
没改代码。出厂 `models.yaml.example` 仍写标准账号普遍可用的 qwen-image-2.0-pro / wan2.6, 客户按自己账号在「模型」页改。

| 用途 | 模型 | 结果 |
|---|---|---|
| 文生图 | qwen-image-3.0-pro | 横幅海报, 中文标题字清楚, 66 秒 |
| 改图 | qwen-image-3.0-pro | 背景换成夜景、主体不变, 146 秒 |
| 文生视频 | wan3.0-video | 5 秒 1280×720, 内容对得上 (小鲶鱼在水里游) |
| 图生视频 | wan3.0-video | 首帧是原图 (海报), 画幅跟原图 |

小鲶对话实测 (hermes chat, 没点名工具): 「帮我画一张图: 戴安全帽的小鲶鱼在水泥厂巡检, 方形」→ 自己调 `image_generate`, 54 秒;
「把这张图做成 3 秒小视频, 让小鲶鱼挥挥手」+ 本机图片路径 → 自己调 `video_generate` (图生视频), 73 秒, 首帧是原图、960×960。

**踩到的坑**: 万相 3.0 跟 2.x 请求格式不一样 —— 没有 `size` (传了被忽略, 按默认 1080P 带声音出, 更贵), 首帧要放
`input.media=[{type: first_frame, url}]` (放 `img_url` 被忽略, 出来的视频跟原图毫无关系)。上游都不报错。所以这两项做成了模型
配置 (`size_param: resolution_ratio`、`image_input: first_frame`), 不按型号名在代码里判断。

## 测试

- 网关 `tests/test_media_generation.py`: 配置 / 挑模型 / 百炼请求体 / 视频提交轮询只记一次账只有本人能取 / 计费聚合 / LiteLLM 路径
- 插件 `tests/test_media_gen.py` (真 Hermes 0.23.2 的供应商基类和工具入口, 假中央)
- Companion: `hermes_policy_config` (v0.23 打开、v0.22 不碰)、右栏视频、外发用途
- 门户: 按张 / 按秒成本、表单校验

**另一个坑 (顺手修了)**: 新版网关写进库的值 (`resolution_ratio`) 老进程不认, `_assemble_config` 里 `ModelConfig.model_validate`
没兜住 —— 整份重载失败, 冷启动就是整个网关起不来, 跟那段代码自己写的「不让单个模型掀翻整份配置」相反。现在读不懂的那一行跳过、
原因进模型页的 config_errors。
