# -*- coding: utf-8 -*-
"""codex_backend.rs 里那段嵌入 Python (HERMES_HELPER) 的行为测试。

# 为什么需要它

那段脚本是以 Rust 字符串常量的形式嵌在 codex_backend.rs 里的:
`cargo test` 碰不到它 (对 Rust 来说就是个字符串), CI 里也没有任何 job 会执行它
—— companion job 是纯 Node (tsc + vite build, 注释里明写"不要 Rust 工具链")。
它只在员工点模型下拉框时才第一次真正运行。

代价已经付过一次: 8/3–8/10 之间, disable 分支少了一条跟 enable 对称的 no-op
短路。后果不在 Codex 那边, 在**普通网关模型**上 —— select 动作里

    action = "enable" if requested_model in available else "disable"

所以员工每换一次 deepseek / qwen 都落进 disable 分支, 无条件调一次
`crs.apply(config, "auto")`。那是 hermes 的**运行时执行器**切换器, 它返的
requires_new_session 说的是"换执行器要新会话", 跟员工选的模型没关系。
Companion 把这一位原样回传, 前端就弹

    「这个模型要新开一个对话才会生效 —— 在当前对话继续发, 跑的还是原来那个」

而这句话在 8/9 P46 之后已经不成立: 会话级模型 override 被砍掉, picker 成为
唯一真源, agent.model 每条消息都被覆盖一次
(见 edge/hermes-plugins/catfish-xcatfish-user/model_authority.py)。
员工照着提示新建对话, 丢掉整段上下文, 换来一件本来就已经成立的事。

# 怎么跑

    python3 edge/companion-app/src-tauri/scripts/test_hermes_helper.py

hermes_cli 的三个模块全部用桩件顶掉, 所以不需要装 hermes, 也不碰真 config。
桩件会记录 `crs.apply` 有没有被调用 —— 那正是这次要盯住的东西: 没有真实的
运行时切换时, 它一次都不该被调。
"""
from __future__ import annotations

import ast
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

_HERE = pathlib.Path(__file__).resolve().parent
RS = _HERE.parent / "src" / "commands" / "codex_backend.rs"


def extract_helper(text: str) -> str:
    m = re.search(r'const HERMES_HELPER: &str = r#"(.*?)"#;', text, re.S)
    assert m, "在 codex_backend.rs 里找不到 HERMES_HELPER —— 常量被改名或换了字符串形式?"
    return m.group(1)


# ── hermes_cli 桩件 ────────────────────────────────────────────────────
# 只实现被 helper 用到的那几个符号。CodexRuntimeStatus 的字段跟 helper 里
# 自己构造它时用的 kwargs 一致 (enable 分支的 already_selected 短路)。

STUB_CRS = '''
import json, os
from dataclasses import dataclass

@dataclass
class CodexRuntimeStatus:
    success: bool = True
    new_value: str = ""
    old_value: str = ""
    message: str = ""
    requires_new_session: bool = False

def _log(ev):
    with open(os.environ["STUB_LOG"], "a") as f:
        f.write(json.dumps(ev) + "\\n")

def get_current_runtime(config):
    return os.environ.get("STUB_RUNTIME", "auto")

def apply(config, target, persist_callback=None):
    # 真调到这里就记一笔 —— 没有真实运行时切换时它一次都不该被调。
    _log({"call": "apply", "target": target})
    # hermes 的真实语义: 换执行器要新会话。这里如实模拟, 才能验证
    # "helper 有没有在不该问的时候去问它"。
    return CodexRuntimeStatus(
        success=True,
        new_value=target,
        old_value=os.environ.get("STUB_RUNTIME", "auto"),
        message="switched",
        requires_new_session=True,
    )
'''

STUB_MODELS = 'def get_codex_model_ids():\n    return ["gpt-5", "gpt-5-codex"]\n'

STUB_CONFIG = '''
import json, os, pathlib
_P = pathlib.Path(os.environ["STUB_CFG"])
def get_config_path(): return _P
def load_config(): return json.loads(_P.read_text())
def read_raw_config(): return json.loads(_P.read_text())
def save_config(c):
    _P.write_text(json.dumps(c))
    with open(os.environ["STUB_LOG"], "a") as f:
        f.write(json.dumps({"call": "save_config"}) + "\\n")
'''


def run_helper(helper_src: str, *, runtime: str, provider: str, default: str,
               model_arg: str, with_backup: bool = False):
    """在隔离的 tmp 目录里跑一遍 helper, 返 (输出 JSON, crs 调用记录)。"""
    d = pathlib.Path(tempfile.mkdtemp(prefix="hermes-helper-test-"))
    try:
        pkg = d / "hermes_cli"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "codex_runtime_switch.py").write_text(STUB_CRS)
        (pkg / "codex_models.py").write_text(STUB_MODELS)
        (pkg / "config.py").write_text(STUB_CONFIG)

        cfg = d / "config.yaml"
        cfg.write_text(json.dumps({"model": {"provider": provider, "default": default}}))
        if with_backup:
            (d / "catfish-codex-backend-backup.json").write_text(json.dumps({
                "version": 2, "provider_present": True, "provider": "openai-api",
                "default_present": True, "default": "catfish-auto",
            }))
        log = d / "calls.log"
        log.write_text("")
        script = d / "helper.py"
        script.write_text(helper_src)

        env = {**os.environ, "PYTHONPATH": str(d), "STUB_RUNTIME": runtime,
               "STUB_CFG": str(cfg), "STUB_LOG": str(log),
               "PYTHONDONTWRITEBYTECODE": "1"}
        proc = subprocess.run([sys.executable, str(script), "select", model_arg],
                              capture_output=True, text=True, env=env, cwd=d)
        assert proc.returncode == 0, (
            f"helper 退出码 {proc.returncode}\nstdout: {proc.stdout}\nstderr: {proc.stderr}")

        payload = {}
        for line in proc.stdout.strip().splitlines():
            try:
                payload = json.loads(line)
            except Exception:  # noqa: BLE001 - 非 JSON 行忽略, 取最后一条
                pass
        calls = [json.loads(l)["call"] for l in log.read_text().splitlines() if l.strip()]
        return payload, calls
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── 用例 ──────────────────────────────────────────────────────────────
# (名字, helper 入参, 期望 requires_new_session, 期望 crs.apply 被调用)

CASES = [
    ("从没启用过 Codex 的机器上换网关模型",
     dict(runtime="auto", provider="openai-api", default="catfish-auto",
          model_arg="Deepseek-v4-flash"),
     False, False),

    ("正在用 Codex, 切回网关模型 (真的换了执行器)",
     dict(runtime="codex_app_server", provider="openai-codex", default="gpt-5",
          model_arg="Deepseek-v4-flash", with_backup=True),
     True, True),

    ("换到 Codex 模型 (enable 路径)",
     dict(runtime="auto", provider="openai-api", default="catfish-auto",
          model_arg="gpt-5"),
     True, True),

    ("已经选中的 Codex 模型再选一次 (enable 的 no-op 短路)",
     dict(runtime="codex_app_server", provider="openai-codex", default="gpt-5",
          model_arg="gpt-5"),
     False, False),
]


def main() -> int:
    src = RS.read_text()
    helper = extract_helper(src)

    # 语法先过一遍 —— 这段脚本坏掉的表现是"所有换模型操作都报错",
    # 而它在 cargo build 时只是个字符串, 编译器不会吭声。
    ast.parse(helper)
    print(f"  HERMES_HELPER 可解析 ✓  ({len(helper.splitlines())} 行)")
    print()

    hdr = "  %-42s %-18s %-16s" % ("场景", "requires_new", "crs.apply 被调")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    failures = []
    for name, kw, want_rns, want_apply in CASES:
        payload, calls = run_helper(helper, **kw)
        got_rns = payload.get("result", {}).get("requires_new_session")
        got_apply = "apply" in calls
        ok = (got_rns == want_rns) and (got_apply == want_apply)
        print("  %-42s %-18s %-16s %s" % (
            name, f"{got_rns}", f"{got_apply}", "" if ok else "← 不符"))
        if not ok:
            failures.append(
                f"{name}: requires_new_session 期望 {want_rns} 得到 {got_rns}; "
                f"crs.apply 期望调用={want_apply} 实际={got_apply}")

    print()
    if failures:
        print("  失败:")
        for f in failures:
            print("    ✗ " + f)
        print()
        print("  第一条挂了通常意味着 disable 分支的 no-op 短路被删了 —— "
              "那会让员工每换一次普通模型都收到一句假的"
              "「这个模型要新开一个对话才会生效」。")
        return 1

    print("  ✓ 全部符合预期")
    return 0


if __name__ == "__main__":
    sys.exit(main())
