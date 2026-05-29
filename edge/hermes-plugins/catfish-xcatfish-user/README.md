# catfish-xcatfish-user-plugin (B1 骨架)

把 hermes 仓里 5/27-5/29 共 11 处 catfish 私有 patch 搬出来, 写成独立 plugin
(monkey-patch 风格). hermes 仓回归 pristine, 升级路径 `git pull` 干净.

## 文件

| 文件 | 角色 | LOC |
|---|---|---|
| `plugin.py` | 主入口 + 11 处 monkey-patch + import-time verify | ~330 |
| `resolver.py` | 5 步 catfish_outgoing_user resolve 链, 复用 | ~50 |
| `session_registry.py` | session_id → user 跨线程注册表 (P4 用) | ~60 |
| `tests/test_patches_present.py` | 启动 self-test: 11 处 patch 引用的 hermes 函数 / 方法都在 | ~200 |

## 覆盖的 11 处 patch

| # | hermes 路径 | 风险 |
|---|---|---|
| P1 | `agent/agent_init.py` init-time apply_headers | wrap init_agent + post replace_primary_openai_client |
| P2 | `run_agent.py AIAgent._current_main_runtime` | class method wrap |
| P3a | `agent/auxiliary_client._MAIN_RUNTIME_FIELDS` | module attribute 重赋 tuple |
| P3b | `agent/auxiliary_client._resolve_auto` | wrap, post-return rebuild client |
| P4 | `agent/title_generator.auto_title_session` | wrap, lookup session_registry 补 main_runtime |
| P5 | `gateway/platforms/api_server.ApiServer._extract_catfish_outgoing_user` | 新方法挂 class |
| P6 | `ApiServer._create_agent` | wrap, post-init set attribute + apply_headers + picker model override |
| P7 | `ApiServer._handle_companion_proxy` 新方法 + catch-all 路由 | wrap _setup_routes, 末尾追加路由 |
| P8 | `ApiServer._is_tauri_origin` + CORS origin check | wrap origin check 函数 |
| P9 | `ApiServer._CORS_HEADERS["Access-Control-Allow-Headers"]` | dict update |
| P10 | `AIAgent._apply_client_headers_for_base_url` localhost:8999 分支 | method wrap, catfish-gateway 先 check |
| P11 | picker model_override (aiohttp middleware + post-init agent.model) | middleware 拦截 body.model + _create_agent wrap 覆盖 |

## 跟现在直接改 hermes 仓的差别

|  | 现在 (5 文件改 hermes 仓) | B1 plugin |
|---|---|---|
| hermes 仓 git status | dirty | clean |
| 升级 hermes | rebase --skip / 3945 行冲突 / brand_patch hook 卡死 | `git pull` 干净 |
| Big Refactor 破坏 patch | silent 400 / silent 跨员工串 | ImportError fail loud, hermes 启动失败 |
| 调试位置 | hermes 仓 git blame 混 catfish + upstream | catfish 仓单独 git blame 清晰 |
| catfish patch 总 LOC | ~600 散在 hermes 5 个文件 | ~640 集中 plugin |

## 关键设计: fail loud, never silent

`plugin.install()` 先跑 `_verify_patch_targets()` — 任一引用的 hermes attribute /
方法名缺失立刻 raise ImportError, 阻止 hermes 启动. 不允许 plugin 半推半就加载
(会导致 silent 跨员工串数据 = P0 隐私漏洞).

例: hermes refactor 把 `_current_main_runtime` 改名 `_get_runtime_state`,
plugin import 时立刻报:

```
ImportError: catfish-xcatfish-user plugin: hermes refactor 破坏了 patch targets.
缺失 attributes: run_agent.AIAgent._current_main_runtime.
不允许半加载 (会导致跨员工串数据). 升级 plugin 或回滚 hermes.
```

ops 看到: hermes 起不来, 知道 plugin 跟 hermes 版本不兼容, 立刻知道要修 plugin
或回滚 hermes — 不会 silent 出大问题.

## 待办 (这是骨架, 落地还需要)

1. **接 hermes plugin discovery** — 看 catfish-memory `__init__.py` 怎么写的, 模仿
   暴露 `install()` entry point (具体跟 catfish-memory 模式一致)
2. **把当前直接改 hermes 仓的 11 处改回滚** — revert 5/27-5/29 在 hermes 仓的
   commit, 让 hermes 回到 pristine upstream
3. **smoke test** — Companion 切 Gemini / 微信 ClawBot 发消息 / 后台 title_gen,
   3 条路径全过, 端到端验证 X-Catfish-User 真传过去 + 真切 model
4. **catfish CI** 加 `pytest tests/test_patches_present.py`, 每天跑一次 + hermes
   升级 PR 必跑
5. **P7 catch-all proxy 路由实现要完善** — 当前是占位, 真实现要 stream
   request/response body + preserve trailers + 处理 chunked + 不破 WebSocket

## 工作量估算 (更新 — 大部分已完)

| 阶段 | 状态 |
|---|---|
| ~~接 hermes plugin discovery~~ | ✓ `__init__.py` register(ctx) + 3 段 fallback (跟 catfish-memory 同 pattern) |
| ~~deploy 脚本~~ | ✓ `deploy.sh` (软链 + self-test + config 提示) |
| ~~smoke test 3 路径~~ | ✓ `smoke-test.sh` (Companion / WeChat / picker / title_gen) |
| ~~revert 脚本~~ | ✓ `revert-hermes-patches.sh` (smoke 全过后跑) |
| P7 catch-all proxy 完善 | ⚠ 占位实现, 真接 Companion 各种长尾 endpoint 时可能需要补 streaming / WebSocket / trailer |
| CI 接 test_patches_present | TODO (照搬 GitHub Actions yaml, ~0.5h) |

剩下要你做的:
1. **搬 plugin 源到 catfish 仓**: `mv outputs/catfish-xcatfish-user-plugin ~/person_task/catfish/edge/hermes-plugins/catfish-xcatfish-user`
2. **跑 deploy**: `bash ~/person_task/catfish/edge/hermes-plugins/catfish-xcatfish-user/deploy.sh`
3. **改 ~/.hermes/config.yaml**: plugins 列表加 `catfish-xcatfish-user`
4. **重启 + smoke**: `launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway && bash ~/person_task/catfish/edge/hermes-plugins/catfish-xcatfish-user/smoke-test.sh`
5. **smoke 全过 → revert**: `bash ~/person_task/catfish/edge/hermes-plugins/catfish-xcatfish-user/revert-hermes-patches.sh`

5 步走完, hermes 仓回 pristine, plugin 单独 work.

## 验证 plugin 真生效的步骤

升级 hermes 0.16 后:

```bash
# 1. plugin self-test
cd ~/.hermes/hermes-agent
python -m pytest path/to/catfish-xcatfish-user-plugin/tests/ -v
# 如果有 fail → hermes refactor 破坏了 patch, 立刻看 fail message 修

# 2. 重启 hermes, 看 plugin install log
launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway
sleep 3
grep "catfish-xcatfish-user" ~/.hermes/logs/agent.log
# 期望: "catfish-xcatfish-user plugin installed ✓ (11 patches applied)"

# 3. 端到端: Companion + 微信 + 后台 title_gen
# Companion 切 Gemini 发 hi
# 微信 ClawBot 发 hi
# 看 catfish-gateway log, 全部应该有 "acting on behalf of user=..." (X-Catfish-User)
```
