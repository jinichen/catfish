# 5/29 战果留档: catfish-xcatfish-user plugin ship + hermes 仓 revert

> 主线: 把 hermes 仓 5/19-5/29 累计 11 处 catfish 私有 patch 全搬出来变独立 plugin, hermes 仓回 upstream pristine. 升级路径从"3000 行 rebase 冲突 + brand_patch hook 卡死 + silent break"变成"`git pull` + pytest + 重启 + smoke 5 步走".

---

## 一句话总结

**10 天 3 次升级痛, 今天彻底结束**. 下次升 hermes (任何版本) 都是几分钟的事, 不再是几小时折磨.

---

## 全天时间线

### 上午: hermes 0.14 → 0.15 升级 (硬扛)

**触发**: hermes 0.15 (v2026.5.16 → 5.28) 包含 PR #27248 "The Big Refactor" — `run_agent.py` 从 16k LOC 拆成 14 个 `agent/*.py` module. 我们 5/27-5/28 patch 在的位置全漂.

**踩雷 3 处**:
1. **rebase 3945 行冲突** — 必须 `git rebase --skip` 重新手写
2. **brand_patch hook 在 git checkout 时跑, 改 working tree 8 个文件** — 必须 `mv post-checkout post-checkout.disabled`
3. **方法签名静默漂** — `_apply_client_headers_for_base_url` 还在但调用方从 `AIAgent.__init__` 挪到 `agent_init.init_agent`. 函数没改名, 但 init 时不调了, X-Catfish-User 进不去 default_headers. 主对话 200 但 silent 跨员工串数据 — P0 隐私漏洞看不见.

**4 处 fix 补到 hermes 0.15**:
- `agent/agent_init.py` — init-time 显式调 `_apply_client_headers_for_base_url`
- `run_agent.py` `_current_main_runtime` — 加 `catfish_outgoing_user` 字段
- `agent/auxiliary_client.py` — `_MAIN_RUNTIME_FIELDS` + `_resolve_auto` rebuild client
- `gateway/run.py` — auto-title 走 `_current_main_runtime()`

**commit**: `a26b9138d BL-CATFISH-USER-FORWARD-AUX-PORT-015`

### 中午: 鸿波拍板 B1 — 走 plugin 路径

**关键对话**: "每次升级都不可控吗?"

我给出 3 条路:
- **A. 上游 PR** — picker model_override 推给 hermes upstream, 减一处 patch (依赖 maintainer review, 几个月)
- **B. plugin** — 全 monkey-patch 风格, hermes 仓回 pristine
  - **B1**: 不依赖上游, 今天就能做
  - **B2**: 上游加正式 hook API (长期最干净)
- **C. CI smoke test** — 升级 PR 自动跑微信/Companion 端到端, silent break 立刻 fail

鸿波选 **B1 单独可行就 B1**. 我说 "现在直接起骨架" — 6 个文件落地.

### 下午: B1 plugin 实施 (踩坑 6 个真因, 每个 1-2 小时)

#### 真因 1: hermes 0.15 class 真名 `APIServerAdapter` 不是 `ApiServer`
我猜错了名. 22/22 → 13 fail. `sed` 一把全替换.

#### 真因 2: pytest 加载 dash-named 目录时 `from .. import` 解析不了
test 改 `import` + 加 `conftest.py` 把 plugin 根加 `sys.path`.

#### 真因 3: `_CORS_HEADERS` 是 **module-level 常量** 不是 class attribute
hermes 0.15 `api_server.py:502` 模块级 dict. plugin mutate 模块属性而非 class attribute.

#### 真因 4: 路由注册 inline 在 **async `connect()`** 不是专门 `_setup_routes`
hermes 0.15 没拆出路由注册方法, 直接在 `connect()` 里 `self._app.router.add_get(...)`. plugin wrap connect() 处理 async.

#### 真因 5: hermes plugin discovery 把我们误归 `exclusive` (memory provider)
`hermes_cli/plugins.py:1318` auto-detect 看 `__init__.py` 内容, 我 docstring 提了 "MemoryProvider" 解释跟 catfish-memory 的区别 — 关键词撞 auto-detect.
修: `plugin.yaml` 显式 `kind: standalone`.

#### 真因 6: standalone plugin 必须显式在 `plugins.enabled` 才加载
不在列表 → silent skip, 不报错. 用 `python3 -c "..."` 自动加.

#### 真因 7 (P1 重要性): aiohttp `_app.middlewares` 在 `runner.setup()` 后 **freeze**
connect() wrap 已经晚, append → `Cannot modify frozen list`.
修: monkey-patch `web.Application.__init__` — 构造时就把 middleware 加进 mws, fence 检查 `cors_middleware in mws` 只对 hermes app 生效.

#### 真因 8 (P0 最关键): **contextvars 不跨 thread executor**
revert 后 smoke `[3] WeChat user / [4] picker model` 都 fail. catfish-gateway log 看到的是 env 兜底 `chenhongbo@ffcs.cn` + deepseek 不是 weixin user / gemini.

真因: hermes `_run_agent` 用 `loop.run_in_executor(None, _run)` 把 `_run()` (内含 `_create_agent`) 推到 **thread pool executor**. middleware 在 asyncio main loop 设的 CV, executor thread 自动看不见 — contextvars 默认 thread-local.

修: monkey-patch `asyncio.BaseEventLoop.run_in_executor` 自动 `copy_context().run()` wrap func. **必须第一个跑** (其它 patch 都依赖).

### 晚上: revert hermes 仓 patch + push catfish 仓

- **hermes 仓 commit `7dad3a88d`**: `BL-CATFISH-PATCH-REVERT-TO-PLUGIN` 砍 5 文件 833 LOC 回 upstream pristine
- **hermes 仓 commit `a18c25b3c`**: `BL-HERMES-CATFISH-SKIN` 鲶鱼 brand skin 单独 commit (banner/tips/UI)
- **catfish 仓 commit `bed97f7`** (push 到 origin/main): `BL-CATFISH-XCATFISH-USER-PLUGIN-SHIP` 全套 plugin
- smoke 9/9 全过, plugin 单独撑住所有路径

---

## plugin 架构

```
~/person_task/catfish/edge/hermes-plugins/catfish-xcatfish-user/
├── __init__.py            # hermes plugin loader register(ctx) 入口
├── plugin.py              # 11 处 monkey-patch + fail-loud verify (~660 LOC)
├── resolver.py            # 5 步链 (a)attr → (b)pairing → (c)email → (d)synthesize → (e)env
├── session_registry.py    # session_id → cf_user 跨线程注册表 (P4 auto-title 用)
├── plugin.yaml            # kind=standalone 显式 (避免 auto-detect 误归)
├── README.md
├── deploy.sh              # 软链 + self-test + config 提示
├── smoke-test.sh          # 9 项端到端验证
├── revert-hermes-patches.sh   # 已跑, 留档供后续 hermes 仓 revert 用
└── tests/
    ├── conftest.py        # sys.path 注入让 dash-named 目录可导入
    └── test_patches_present.py  # 22 项 self-test (hermes refactor 立刻 fail loud)
```

### 11 处 patch 覆盖表

| # | hermes 仓原位置 | plugin 实现 |
|---|---|---|
| P0 | (新增) | `asyncio.BaseEventLoop.run_in_executor` 包 `copy_context()` 让 CV 跨 thread |
| P1 | `agent_init.py` 加 init-time apply_headers | wrap `init_agent` 末尾 + `_replace_primary_openai_client` |
| P2 | `run_agent.py _current_main_runtime` 加字段 | class method wrap, resolver 解 + session_registry 注册 |
| P3a | `_MAIN_RUNTIME_FIELDS` tuple | 模块属性重赋 |
| P3b | `_resolve_auto` rebuild OpenAI client | function wrap, post-return 重建带 default_headers |
| P4 | `auto_title_session` 拿 catfish_user | wrap, session_registry lookup 补 main_runtime |
| P5 | `APIServerAdapter._extract_catfish_outgoing_user` 新方法 | 直接挂 class |
| P6 | `_create_agent` set attribute + apply_headers | wrap, kwargs / CV 三段查 + post-init agent.model 改 |
| P7 | `_handle_companion_proxy` 新方法 + catch-all 路由 | 404 fallback middleware (代替 catch-all route 避免冲突) |
| P8 | `_TAURI_ORIGINS` + `_is_tauri_origin` + `_origin_allowed` wrap | class attr/method override |
| P9 | `_CORS_HEADERS` 加 Allow-Headers | 模块级 dict mutate |
| P10 | `_apply_client_headers_for_base_url` localhost:8999 分支 | method wrap, 先 check catfish-gateway 走自己 |
| P11 | picker model_override wire | aiohttp middleware 拦 body.model 写 CV + `_create_agent` wrap 读 CV |

### 关键技术亮点

#### 1. fail loud, never silent

`plugin.install()` 第一件事跑 `_verify_patch_targets()` — 任一 hermes attribute 缺失立刻 `ImportError`, **阻止 hermes 启动**.

不允许 plugin 半加载. 半加载 = silent 跨员工串数据 = P0 隐私漏洞.

#### 2. contextvar 跨 thread (P0)

```python
def _patch_asyncio_executor_for_contextvars():
    _orig = _aio.BaseEventLoop.run_in_executor
    def _ctx_aware_run_in_executor(self, executor, func, *args):
        ctx = _cv.copy_context()
        @functools.wraps(func)
        def _ctx_func(*a):
            return ctx.run(func, *a)
        return _orig(self, executor, _ctx_func, *args)
    _aio.BaseEventLoop.run_in_executor = _ctx_aware_run_in_executor
```

Process 范围 monkey-patch. 让 `middleware → handler → executor._run → _create_agent` 全程 context vars 透传.

#### 3. aiohttp middleware fence

```python
def _patched_app_init(self, *args, middlewares=(), **kwargs):
    mws_list = list(middlewares) if middlewares else []
    # fence: 只对 hermes api_server App 注入
    is_hermes_app = (
        cors_middleware in mws_list
        or security_headers_middleware in mws_list
    )
    if is_hermes_app:
        mws_list.append(_request_stash_middleware)
        mws_list.append(_proxy_404_middleware)
    return _orig_app_init(self, *args, middlewares=tuple(mws_list), **kwargs)
```

`web.Application.__init__` 全 process 替换, 但 fence 只对 hermes app 生效, 不影响其它 aiohttp Application (Companion 本地 server 等).

#### 4. 5 步 catfish_outgoing_user resolve

```
(a) agent._catfish_outgoing_user attribute (Companion HTTP middleware 已 set)
(b) PairingStore.get_email(platform, user_id) — admin pairing approve
(c) self._user_id 已是 email 形态 (CLI 直传)
(d) synthesize <user_id>@im.<platform> (WeChat openid → o9cq807y@im.weixin)
(e) env CATFISH_DEFAULT_USER 兜底
```

满足 catfish-gateway `_EMAIL_SHAPE_RE`. WeChat 真员工没 pairing 时落 (d) 进 platform-only 命名空间隔离虚拟员工.

---

## 验证流程

### plugin self-test (升级前 check)
```bash
cd ~/.hermes/hermes-agent
source venv/bin/activate
pytest ~/person_task/catfish/edge/hermes-plugins/catfish-xcatfish-user/tests/ -v
# 期望: 22 passed
```

任一 fail = hermes refactor 破了我们 patch 引用的 attribute, 看 fail message 改 plugin 跟上.

### end-to-end smoke (升级后验证)
```bash
launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway
sleep 8
bash ~/person_task/catfish/edge/hermes-plugins/catfish-xcatfish-user/smoke-test.sh
```

9 项端到端 (Companion / WeChat / picker / title_gen), 期望 9/9 全过.

---

## commit 列表

| 仓 | commit | 内容 |
|---|---|---|
| hermes (5-27-catfish-contrib) | `a26b9138d` | BL-CATFISH-USER-FORWARD-AUX-PORT-015 升 0.15 |
| hermes | `b9be2464d` | 同上 (rebase 后版本) |
| hermes | `7dad3a88d` | BL-CATFISH-PATCH-REVERT-TO-PLUGIN 砍 5 文件 |
| hermes | `a18c25b3c` | BL-HERMES-CATFISH-SKIN 鲶鱼 brand 单独 commit |
| catfish (origin/main) | `bed97f7` | **BL-CATFISH-XCATFISH-USER-PLUGIN-SHIP (push 到 GitHub)** |

---

## 最终架构对比

### Before (hermes 仓不 pristine)
```
hermes 仓:
  cli.py, banner.py, ...        ← 鲶鱼 skin (brand_patch hook 刷)
  gateway/platforms/api_server.py   ← +260 LOC catfish 私有
  run_agent.py                  ← +90 LOC catfish 私有
  agent/agent_init.py           ← +13 LOC catfish 私有
  agent/auxiliary_client.py     ← +28 LOC catfish 私有
  gateway/run.py                ← +35 LOC catfish 私有
合计: 5 文件 ~430 LOC catfish 私有 mix 在 hermes 仓

升级流程:
  git pull → 3000 行 rebase 冲突 → rebase --skip → brand_patch hook 卡死
  → 手写 4-5 处 port → silent break (静默 P0) → 测试 1 天找
```

### After (hermes 仓 pristine + plugin)
```
hermes 仓:
  cli.py, banner.py, ...        ← 鲶鱼 skin (单独 BL-HERMES-CATFISH-SKIN commit)
  其它全 upstream pristine

catfish 仓:
  edge/hermes-plugins/catfish-xcatfish-user/
    __init__.py / plugin.py / resolver.py / session_registry.py / plugin.yaml
    tests/ deploy.sh smoke-test.sh revert-hermes-patches.sh README.md

升级流程:
  cd ~/.hermes/hermes-agent
  git fetch origin
  git rebase origin/main        # skin commit 可能要 skip, 其余无冲突
  pytest plugin tests           # 22/22 → 全过证明 hermes 没破 plugin
  launchctl kickstart -k        # 重启
  bash smoke-test.sh            # 9/9 端到端
```

---

## 反思

### 教训 1: 私有 logic 别动 upstream 仓

5/19 BL-AUTH-DECOUPLE-A1 → 5/27 BL-CATFISH-USER-FORWARD → 5/28 BL-HERMES-PICKER → 5/29 BL-CATFISH-USER-FORWARD-AUX-PORT-015. 累计 10 天 3 次升级痛.

**第一次发现要动 hermes 仓的时候, 就该走 plugin 路径**. 那时候省 1-2 天工作, 后面 3 次升级各省半天, 总账 4-5 天.

教训写进 SOUL / backlog: 任何 catfish 私有 logic 动 hermes 仓代码, **先问 "能不能写成 plugin?"**.

### 教训 2: silent break 比 loud break 危险得多

升级 0.15 时, `_apply_client_headers_for_base_url` 函数还在但调用方挪走了 — hermes 主对话 200 OK, 跨员工串数据但 log 一切正常. 这种 silent break 是 P0 隐私漏洞看不见.

plugin 模式天然 fail loud: `_verify_patch_targets()` 升级时 attribute 改名立刻 `ImportError`, hermes 启动失败 — ops 一眼看到.

### 教训 3: contextvars 跨 thread 是真坑

asyncio 默认 executor (`ThreadPoolExecutor`) **不继承** asyncio main loop 的 context vars. 这个 trap 在文档里写得清楚但实际碰到才知道. 修法是 monkey-patch `BaseEventLoop.run_in_executor` 自动 `copy_context().run()` wrap.

backlog 加: 提 PR 给 Python upstream 把这个变默认行为 (PEP 类似讨论存在), 或至少给 asyncio 加一个 opt-in switch.

### 教训 4: dash-named package 在 Python 是恶心坑

catfish-memory 踩过, catfish-xcatfish-user 又踩. `importlib.util.spec_from_file_location` 加载 dash-named 目录时 relative import 不稳, 需要 3 段 fallback. 这条以后所有 catfish plugin 都要带.

backlog: 把 3 段 fallback 抽成 `catfish.plugin_loader` 公共模块, 每个 plugin 引用.

---

## 下一步 (可选)

### 短期
- [ ] hermes 仓 `5-27-catfish-contrib` 分支 push 到自己 fork (backup)
- [ ] 实际生产工作 1-2 天观察 plugin 在真用户对话下的稳定性
- [ ] brand_patch hook 跟 git 操作的兼容性测试 (rebase/checkout 时不再卡)

### 中期
- [ ] **A 上游 PR**: 把 picker model_override 推给 hermes upstream (通用功能, OpenAI 协议合规). 接了的话 P11 plugin patch 就不需要了
- [ ] **B2 上游 hook API**: 跟 hermes upstream 提议 `agent.register_request_hook(fn)` API, plugin 用正式 hook 取代部分 monkey-patch
- [ ] **CI smoke**: 把 `smoke-test.sh` 接到 catfish CI / GitHub Actions, 每天跑一次 + hermes 升级 PR 必跑

### 长期
- [ ] 把 plugin pattern 推广到其它 catfish 在 hermes 仓的改动 (memory provider 已经是 plugin, todo-sync 也是, 但还有其它散在 hermes 仓的小改)
- [ ] 写一份 `docs/CATFISH-PLUGIN-PATTERN.md` 给团队复用

---

## 收尾感言

10 天 3 次硬扛升级, 累得不行. 今天搬完 plugin, 看着 hermes 仓 833 LOC 回 pristine, 心里舒服很多.

下次升级 hermes (任何版本), 真的就是几分钟的事. 这种"投入 1 天换取无穷次省时间"的活, 早做晚做的差别是几个月时间.

**下次有类似选择, 早做.**

---

*生成时间: 2026-05-29 晚*
*作者: 鸿波 + Claude (Cowork)*
