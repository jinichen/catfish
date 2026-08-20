"""`_apply_patches` 的每个调用点，跟重构**之前**的版本语义必须一致。

# 为什么是拿 git 历史对拍，而不是拿当前代码自检

8/13 把 19 个一模一样的 try 块收成 `_try_patch(fn, msg)` 时，AST 转换只取了
`call.func.id`，结果吞掉两样东西：

  1. **P42 的实参** —— 原来是 `_patch_p42_memory_skip_background(CV_CF_SOURCE)`。
     参数没了 → TypeError → memory 来源闸整个没装上。那道闸挡的是"员工邮件正文
     进个人知识库"，是 8/8 专门修过的隐私红线。
  2. **19 个 handler 的 `exc_info=True`** —— patch 失败不再有堆栈。

当时是做了验证的，而且全绿："19 条 error 文案逐字一致"。问题在于**我只比了
`args[0]`，也就是我自己选择要保留的那个东西**。验证范围等于设计范围，所以它
天然抓不到没想到的部分。形状检查同理：验了 `len(handler.args) == 2`，没看
`handler.keywords`；验了 try 体是单个 `_patch_*` 调用，没看那个调用有没有实参。

所以这个文件的做法反过来：**不预设该保留什么**，直接把重构前那一版从 git 取出来，
逐个调用点比对「函数名 + 实参 + 错误文案 + handler 的全部 kwargs」。凡是当年
存在的，现在都得在。

`REFACTOR_BASE` 是重构前最后一个提交。它固定不动 —— 这条测试比的是"跟历史一致"，
不是"跟上一版一致"，所以基准不该随着后续提交漂移。
"""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

_DIR = Path(__file__).resolve().parent.parent
_REPO = _DIR.parents[2]
_REL = "edge/hermes-plugins/catfish-xcatfish-user/plugin.py"

#: 把 19 个 try 块收成 _try_patch 之前的最后一个提交。
REFACTOR_BASE = "cc4d71a"

#: **有意删掉**的 patch —— 必须逐个写理由。
#:
#: 这个名单存在的意义是「删除得是个决定，不是个意外」。基准提交里有、现在没有的
#: patch，要么在这里有名字有理由，要么这条测试红。
#:
#: 为什么不干脆把基准提交往前挪: 挪了就等于把所有历史差异一笔勾销，包括那些
#: **不该发生**的丢失。8/13 那次就是靠跟历史对拍才发现 P42 的实参被吞掉的。
#: **基准之后新增**的 patch —— 同样必须逐个写理由。
#:
#: 8/15 补。原来只有 INTENTIONALLY_REMOVED，但下面那条断言是 `set(old) == set(new)`
#: 双向相等，于是**加**一个 patch 也会红，而红了之后没有正规的记录去处 ——
#: 只能要么改断言(把闸放松)，要么不加。两个都不对。
#:
#: 按这个文件自己的哲学补齐: 「删除得是个决定，不是个意外」，新增也一样。
#: 一个 patch 悄悄多出来跟悄悄少一个同等危险 —— monkey-patch 是全进程生效的，
#: 谁加的、为什么加、影响面多大，得有地方写。
INTENTIONALLY_ADDED: dict[str, str] = {
    "_patch_p44_service_call_lean": (
        "8/15 加。后台分类调用不背 agent 上下文。\n"
        "  病: 邮件评级一次 42,114 输入 token 换 185 输出 token (230:1)。8/15 百炼\n"
        "      周配额 07:54 重置、09:17 就空了，83 分钟约 3000 万 token，\n"
        "      companion-email-scheduler 一家占 2470 万 (83%)。\n"
        "  改: 白名单来源在 api_server 平台上不注入工具 schema、不读记忆。\n"
        "  影响面: **只有** companion-email-scheduler / companion-phishing-scan\n"
        "      两个来源。聊天(无 source)、早安(briefing-card/advisor)、\n"
        "      知识库(wiki-suggest) 一律不动 —— 见 test_p44_service_lean.py 里\n"
        "      专门针对这三样的用例。\n"
        "  跟 P42 的区别: P42 挡记忆**写**且判据是「有 source 就跳」；P44 挡工具\n"
        "      和记忆**读**，判据是**只含两项的白名单**。沿用 P42 那条宽判据会\n"
        "      误伤早安和知识库。"
    ),
    "_install_p45_deferred_tool_guard": (
        "8/19 加。被 defer 的工具被叫到时，不许被模糊改名成另一个工具。\n"
        "  病: 鸿波说「固化 eis-login SKILL」，小鲶连发六次 catfish_browser_fill，\n"
        "      参数却是 catfish_freeze_skill 的 (name/namespace/description/\n"
        "      overwrite/target)。工具返 `'selector' is a required property`，\n"
        "      模型看不懂，再发一次，六轮。看着像模型犯傻。\n"
        "  真因: hermes 0.20 的 progressive disclosure 把 catfish 78 个工具里\n"
        "      P43 没提升的那 67 个全 defer 掉 —— **这是对的**。而\n"
        "      agent.valid_tool_names 是从装配**之后**的可见列表派生的\n"
        "      (tools/mcp_tool.py:6832)，于是被 defer 的名字落进\n"
        "      repair_tool_call 的模糊兜底 get_close_matches(cutoff=0.7)。\n"
        "      catfish 工具名共享 28 字符前缀，这个阈值形同虚设，实算:\n"
        "        catfish_teach_start  → catfish_search_docs   0.872\n"
        "        catfish_freeze_skill → catfish_browser_fill  0.800\n"
        "      跟当天日志里那两串一模一样。改名是就地改 tc.function.name，\n"
        "      发生在落库之前，所以事后翻 state.db 看到的是「模型调错了工具」。\n"
        "  改: 包一层 repair_tool_call —— 名字若「已注册但本次被 defer」\n"
        "      (用上游自己的 tool_search.is_deferrable_tool_name 判，不另写一份)，\n"
        "      返 None 不猜；错误消息改成指向 tool_call。\n"
        "  影响面: 只作用于**已注册且不可见**的名字。没注册的幻觉名字\n"
        "      (is_deferrable 返 False) 照常走上游模糊修复 —— 那本来就该修；\n"
        "      P43 提升过的 11 个是 core，也返 False，不受影响。\n"
        "  跟 P43 的关系: P43 是「把某几个工具提成核心免于 defer」，一次救一个，\n"
        "      名单从 4 长到 11，每次都是等员工先撞一次。P45 不改可见性，只保证\n"
        "      **剩下 67 个被叫到时不会被换成别的工具**，新加工具自动受保护。\n"
        "      两者判据不冲突: P43 决定「发不发」，P45 决定「没发时怎么答」。\n"
        "  升级: 锚点由 audit_hermes_compat.sh Section 18 盯着，含「run_agent.py\n"
        "      仍是方法内晚绑定 import」这条 —— 上游改成顶部 import 的话\n"
        "      monkeypatch 会静默失效，那一条会当场变红。"
    ),
}

INTENTIONALLY_REMOVED: dict[str, str] = {
    "_patch_p25_cron_env_isolation": (
        "8/19 删。P25 (P3.5.104, 6/24) 治的是 hermes cron 把 HERMES_CRON_SESSION\n"
        "写进 os.environ 不清 —— env 是进程级跨线程的，整个 daemon 被污染，之后任何\n"
        "chat / api 调 execute_code 看见 env=1 + cron_mode=deny 就 BLOCKED。现象是\n"
        "员工那句「execute_code 一直被拦」。做法是 threadlocal 精准判定「本线程真在\n"
        "cron run_job 里」+ wrap check_execute_code_guard 在非 cron 线程临时 pop 掉 env。\n"
        "\n"
        "**上游把病因改掉了** (hermes 0.20):\n"
        "  cron/scheduler.py:3124  _cron_session_var.set('1')      ← ContextVar 不是 env\n"
        "  cron/scheduler.py:3777  _cron_session_var.reset(token)\n"
        "  tools/approval.py:227   _is_cron_approval_context() 优先 get_session_env,\n"
        "                          docstring: 'so one cron job cannot taint unrelated\n"
        "                          gateway/API/TUI turns in the same process'\n"
        "P25 注释指名的病灶行 cron/scheduler.py:1558 现在是 delivery thread_id 的代码。\n"
        "全树搜谁还在写全局 env: catfish 0 处、~/.hermes/.env 0 处、hermes 0 处。\n"
        "\n"
        "**退役证据是 P25 自己攒的** —— 它内置了探针，8/13 有人特意把它从\n"
        "logger.debug 提到 warning (「一个只在 debug 级打的证据等于没有证据」)。\n"
        "8/19 读日志: 窗口 8/08→8/20 共 12 天，同期 340 次 cron job + 494 次\n"
        "execute_code，对照组「P25 wrap」1810 次装载记录，而「接住污染」**0 次**、\n"
        "「装载时清掉污染」**0 次**。\n"
        "\n"
        "当年写下的退役条件是「长期不打 → 可以考虑退役 (但要先确认所有部署的\n"
        "hermes 版本)」。鸿波 8/19 确认: 员工机 hermes 统一 0.20，大版本升级一起升。\n"
        "两个条件都满足。\n"
        "\n"
        "⚠ 删掉之后**依赖上游那个 ContextVar 化**，它改回 os.environ 的话故障形状\n"
        "跟当年一样而且不报错。两道保险:\n"
        "  tests/test_p25_pollution_visible.py (5 条，读真 hermes 树；含一条全树扫\n"
        "    「谁还在写全局 env」)\n"
        "  audit_hermes_compat.sh Section 20 (升级换树之前验锚点)\n"
        "墓碑在 plugin_cron.py 尾部。判定过程见 docs/HERMES-PATCH-AUDIT-2026-08-19.md。"
    ),
    "_patch_p36_terminal_cwd_home": (
        "8/19 删。P36 (7/8) setenv TERMINAL_CWD=$HOME —— launchd 起 hermes 时\n"
        "process cwd='/'，execute_code / terminal / file_tools 的相对路径全从 / 起。\n"
        "病是真的，但**上游 hermes 0.20 已经做了同一件事**，连「设过就尊重」那半句\n"
        "都一样 (gateway/run.py:2207-2221):\n"
        "    _configured_cwd = os.environ.get('TERMINAL_CWD', '')\n"
        "    if not _configured_cwd or _configured_cwd in CWD_PLACEHOLDERS:\n"
        "        _resolved_cwd = resolve_placeholder_terminal_cwd(\n"
        "            ..., home_fallback=str(Path.home()))\n"
        "而 gateway/cwd_placeholder.py:40-42 在 local backend 下\n"
        "`return messaging or home_fallback` —— 必然返值，不会是 None\n"
        "(返 None 上游会 pop 掉 TERMINAL_CWD，那才会退回 os.getcwd())。\n"
        "\n"
        "员工机实测 (8/19): config.yaml 无 terminal 段，.env 里 TERMINAL_ENV /\n"
        "TERMINAL_CWD / MESSAGING_CWD 三个都没配 → backend 默认 'local' →\n"
        "home_fallback 必然生效。所以 P36 是纯 no-op。\n"
        "\n"
        "⚠ 删掉之后我们**依赖上游那个 home_fallback**，它没了故障形状跟当年一样\n"
        "而且不报错。两道保险钉着:\n"
        "  tests/test_p36_retired_upstream_covers_it.py (读真 hermes 树验行为)\n"
        "  audit_hermes_compat.sh Section 19 (升级前验锚点)\n"
        "墓碑在 plugin_wechat_qr.py 尾部。判定过程见\n"
        "docs/HERMES-PATCH-AUDIT-2026-08-19.md。"
    ),
    "_patch_p18_compress_endpoint": (
        "8/13 删。P18 (6/17) 做的是「Companion 在 80% 上下文时主动触发 hermes 压缩」，"
        "但查证后确认它解的问题已经被别的机制解掉了:\n"
        "  · 网关 conversation_compressor 自动压，阈值 min(context×0.7, 12万) —— "
        "    **比 P18 想要的 80% 还低**，员工根本走不到 80%\n"
        "  · 实测网关每轮都在压 (12:31 三次, 省 41~43%)，微信那条路同样覆盖\n"
        "  · hermes 自己的 ContextCompressor 在 agent.log 里**一次都没压过**\n"
        "  · P18 的 endpoint 注册了 25 次，**真调用 0 次**，TS/Rust 里零引用\n"
        "而且它同一天 (6/17) 被 P3.5.17.c.2 抽掉了前提——ContextCounter 因为"
        "「cumulative cost ≠ ctx 占用，数学错」被砍，注释写着「不需 Companion 算」。"
        "Phase 2 两个月没做不是没排上，是前提当天就没了。"
    ),
}


def _old_source() -> str | None:
    try:
        r = subprocess.run(
            ["git", "show", f"{REFACTOR_BASE}:{_REL}"],
            capture_output=True, text=True, cwd=_REPO, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 and r.stdout else None


def _fn_of(node: ast.Call) -> str | None:
    return node.func.id if isinstance(node.func, ast.Name) else None


def _old_call_sites(src: str) -> dict[str, dict]:
    """重构前: 每个 try 块 → {args, msg, handler_kwargs}"""
    n = next(x for x in ast.parse(src).body
             if getattr(x, "name", None) == "_apply_patches")
    out: dict[str, dict] = {}
    for st in n.body:
        if not isinstance(st, ast.Try):
            continue
        call = st.body[0].value
        h = st.handlers[0].body[0].value
        out[_fn_of(call)] = {
            "args": [ast.unparse(a) for a in call.args],
            "msg": ast.literal_eval(h.args[0]),
            "handler_kwargs": sorted(
                f"{k.arg}={ast.unparse(k.value)}" for k in h.keywords
            ),
        }
    return out


def _new_call_sites() -> dict[str, dict]:
    """重构后: 每个 `_try_patch(fn, msg, *args)` → 同样的三元组。

    handler kwargs 现在是 `_try_patch` 里那一处统一的 logger.error，
    所以从那个函数体里读。
    """
    src = (_DIR / "plugin.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    # _try_patch 内部那次 logger.error 的 kwargs（19 个调用点共用）
    tp = next(x for x in tree.body if getattr(x, "name", None) == "_try_patch")
    shared_kwargs: list[str] = []
    for c in ast.walk(tp):
        if (isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                and c.func.attr == "error"):
            shared_kwargs = sorted(
                f"{k.arg}={ast.unparse(k.value)}" for k in c.keywords
            )
    ap = next(x for x in tree.body if getattr(x, "name", None) == "_apply_patches")
    out: dict[str, dict] = {}
    for c in ast.walk(ap):
        if not (isinstance(c, ast.Call) and _fn_of(c) == "_try_patch"):
            continue
        fn = c.args[0].id
        out[fn] = {
            "args": [ast.unparse(a) for a in c.args[2:]],
            "msg": ast.literal_eval(c.args[1]),
            "handler_kwargs": shared_kwargs,
        }
    return out


@pytest.fixture(scope="module")
def pair():
    old = _old_source()
    if old is None:
        pytest.skip(f"取不到基准提交 {REFACTOR_BASE}（浅克隆 / 非 git 环境）")
    old_sites = _old_call_sites(old)
    # 有意删掉的从基准里摘出去 —— 但摘之前先确认它当年真在，
    # 免得名单里堆着一堆早就不存在的名字，久了没人敢清。
    for name in INTENTIONALLY_REMOVED:
        assert name in old_sites, (
            f"INTENTIONALLY_REMOVED 里的 {name} 在基准提交 {REFACTOR_BASE} 里并不存在 —— "
            "名单过期了，删掉这条"
        )
        old_sites.pop(name)
    return old_sites, _new_call_sites()


def test_same_set_of_patches(pair):
    """调用点集合只准按两张名单增减，不准无声变化。

    删一个 patch 是个决定，得有名字有理由。这条测试逼那个决定显式化 ——
    否则"某个 patch 悄悄没了"跟"某个 patch 被吞了实参"一样看不出来。
    加一个同理: monkey-patch 全进程生效，多出来一个而没人记一笔同样危险。
    """
    old, new = pair
    lost = set(old) - set(new)
    added = set(new) - set(old) - set(INTENTIONALLY_ADDED)
    assert not lost, (
        f"调用点少了 {lost} —— 有意删的请写进 INTENTIONALLY_REMOVED 并附理由。"
    )
    assert not added, (
        f"调用点多了 {added} —— 新增的请写进 INTENTIONALLY_ADDED 并附理由 "
        "(说清改了什么、影响面多大、跟已有的闸有没有判据冲突)。"
    )


def test_added_patches_are_actually_wired(pair):
    """INTENTIONALLY_ADDED 的名单不能过期 —— 声明加了却没真加上就是空账。

    跟 test_removed_patches_leave_no_trace 对称: 那条查"声明删了却还留着引用",
    这条查"声明加了却没真接进 _apply_patches"。
    """
    _old, new = pair
    for name in INTENTIONALLY_ADDED:
        assert name in new, (
            f"INTENTIONALLY_ADDED 里的 {name} 并没有出现在 _apply_patches 里 —— "
            "要么忘了接线，要么名单过期了"
        )


def test_removed_patches_leave_no_trace():
    """有意删掉的 patch，代码里不能还剩引用。

    P18 那次删了 4 处：re-export / _apply_patches 调用 / _patched_app_init 里的
    路由注册 / 一整段设计注释。漏掉任何一处的表现都不同——漏 re-export 是
    AttributeError，漏路由注册是 handler 不存在时 500，漏注释是误导下一个人。
    """
    src = (_REPO / _REL).read_text(encoding="utf-8")
    for name in INTENTIONALLY_REMOVED:
        assert name not in src, f"{name} 已声明删除，但 plugin.py 里还有引用"


def test_no_call_lost_its_arguments(pair):
    """★ P42 就是栽在这条上。

    `_patch_p42_memory_skip_background(CV_CF_SOURCE)` 的实参被吞掉后，patch 抛
    TypeError 被 except 接住，只留一行 error —— 而当时收尾日志还是写死的
    "✓ (15 patches applied)"，所以什么都看不出来。
    """
    old, new = pair
    lost = {k: old[k]["args"] for k in old
            if old[k]["args"] and old[k]["args"] != new[k]["args"]}
    assert not lost, f"这些 patch 的实参丢了或变了: {lost}"


def test_no_handler_lost_its_kwargs(pair):
    """★ `exc_info=True` 就是栽在这条上。

    19 个 handler 原来都带 exc_info=True。丢了之后 patch 失败只剩一句话，
    "name 'functools' is not defined" 这种错根本不知道在哪一行。
    """
    old, new = pair
    lost = {k: old[k]["handler_kwargs"] for k in old
            if set(old[k]["handler_kwargs"]) - set(new[k]["handler_kwargs"])}
    assert not lost, f"这些 handler 的 kwargs 丢了: {lost}"


def test_error_messages_unchanged(pair):
    """文案是历次事故留下的，重构不该顺手改掉一个字。"""
    old, new = pair
    diff = {k: (old[k]["msg"], new[k]["msg"]) for k in old
            if old[k]["msg"] != new[k]["msg"]}
    assert not diff, f"文案变了: {diff}"


def test_try_patch_forwards_args():
    """`_try_patch` 得真把 *args 转给 fn —— 光在签名上写着不算。

    第一版签名里连 *args 都没有；补上之后如果只是收着不转发，P42 一样是死的，
    而且更难看出来（不报错，闸就是不生效）。
    """
    src = (_DIR / "plugin.py").read_text(encoding="utf-8")
    tp = next(x for x in ast.parse(src).body
              if getattr(x, "name", None) == "_try_patch")
    assert tp.args.vararg is not None, "_try_patch 没有 *args"
    forwarded = any(
        isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id == "fn"
        and any(isinstance(a, ast.Starred) for a in c.args)
        for c in ast.walk(tp)
    )
    assert forwarded, "*args 收了但没转发给 fn —— P42 会静默失效"
