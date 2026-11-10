"""小鲶派帮手 (delegate_task) 在**真 hermes 网关**上走一遍 —— 按 Companion 的方式发对话 (11/11)。

# 为什么

11/11 临时网关实测 (v0.23.1, 三个鲶鱼插件): hermes 默认 delegation.gateway_mode=auto, 对 API 会话
(Companion) 走后台 —— 本轮立刻结束只回「已派出」, 结果写进会话不叫醒模型, 员工再说一句才看得到;
帮手要删文件的审批请求发不到员工, 干等到超时; 帮手发给网关的请求带的是本机默认用户, 不是这一轮的员工。

Companion 的规定配置因此写 delegation.gateway_mode=inline (hermes_policy_config.rs), 插件登记帮手的会话
(plugin_api_turn.subagent_start_hook)。这里用同样的配置起一个网关, 钉住三件事:
  · 帮手的结果在同一轮回来 (父对话汇总后回答)
  · 帮手要删文件 → 照常弹审批, 拒绝后帮手收到「没同意」, 目录还在
  · 帮手发给网关的请求带这一轮员工的身份

升级 hermes 前跑: `HERMES_ROOT=<新版树> pytest tests/test_delegation_on_real_hermes.py`。
要 `$HERMES_ROOT/venv/bin/python`, 没有就跳过 (CI / 沙箱)。HOME / HERMES_HOME 都指向临时目录。
起一个网关十几秒。
"""
from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

HERMES_ROOT = Path(os.environ.get("HERMES_ROOT", Path.home() / ".hermes" / "hermes-agent"))
HERMES_PY = HERMES_ROOT / "venv" / "bin" / "python"
TESTS = Path(__file__).resolve().parent
PLUGINS = {
    "catfish-xcatfish-user": TESTS.parent,
    "catfish-memory": TESTS.parent.parent / "catfish-memory",
    "catfish-policy": TESTS.parents[3] / "plugins" / "catfish-policy",
}
EMPLOYEE = "alice.test@corp"

pytestmark = pytest.mark.skipif(
    not (HERMES_PY.exists() and (HERMES_ROOT / "tools" / "delegate_tool.py").exists()),
    reason=f"没有带 venv、带 delegate_task 的 hermes 树 ({HERMES_ROOT}) —— CI/沙箱里跳过",
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _config(llm_port: int) -> str:
    # Companion 规定配置里跟这件事有关的几项 (hermes_policy_config.rs) + 跑起来必需的模型 / 插件
    import yaml  # noqa: PLC0415
    cfg = {
        "model": {"provider": "custom", "base_url": f"http://127.0.0.1:{llm_port}/v1", "default": "catfish-auto",
                  "key_env": "OPENAI_API_KEY"},
        "memory": {"provider": "catfish-memory"},
        "plugins": {"enabled": ["catfish-policy", "catfish-xcatfish-user"], "load_timeout_seconds": 90,
                    "entries": {p: {"granted_capabilities": ["tools.override"], "allow_tool_override": True}
                                for p in ("catfish-memory", "catfish-xcatfish-user")}},
        "approvals": {"mode": "manual", "timeout": 120},
        "agent": {"max_turns": 30, "disabled_toolsets": ["connections", "browser"]},
        "delegation": {"gateway_mode": "inline", "subagent_auto_approve": False, "max_concurrent_children": 3},
        "sessions": {"auto_prune": False},
        "security": {"allow_lazy_installs": False},
    }
    return yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False)


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    home = tmp_path_factory.mktemp("home")
    hh = home / ".hermes"
    (hh / "plugins").mkdir(parents=True)
    for name, src in PLUGINS.items():
        (hh / "plugins" / name).symlink_to(src)
    llm_port, api_port, key = _free_port(), _free_port(), secrets.token_hex(32)
    (hh / "config.yaml").write_text(_config(llm_port), encoding="utf-8")
    (hh / ".env").write_text(
        f"API_SERVER_ENABLED=true\nAPI_SERVER_HOST=127.0.0.1\nAPI_SERVER_PORT={api_port}\nAPI_SERVER_KEY={key}\n"
        f"OPENAI_API_KEY=fake\nOPENAI_BASE_URL=http://127.0.0.1:{llm_port}/v1\n"
        f"CATFISH_GATEWAY_URL=http://127.0.0.1:{llm_port}\nCATFISH_DEFAULT_USER=machine.default@catfish\n",
        encoding="utf-8")
    (home / ".catfish").mkdir()
    target = home / "catfish-del-test"
    target.mkdir()
    (target / "keep.txt").write_text("x")
    llm_log = home / "llm.jsonl"
    # HERMES_STATE_DB_GUARD_BYPASS: hermes 认出是在测试里起的, 拒绝打开 HERMES_HOME 下的 state.db (怕碰到真库)。
    # 这里的 HERMES_HOME 就是上面建的临时目录, 放行。
    env = {"HOME": str(home), "HERMES_HOME": str(hh), "HERMES_ROOT": str(HERMES_ROOT),
           "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HERMES_STATE_DB_GUARD_BYPASS": "1"}
    llm = subprocess.Popen([sys.executable, str(TESTS / "_fake_llm_delegation.py"), str(llm_port), str(llm_log)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    gw = subprocess.Popen([str(HERMES_PY), "-m", "hermes_cli.main", "gateway", "run", "--force"], cwd=home, env=env,
                          stdout=open(home / "gw.out", "w"), stderr=subprocess.STDOUT)
    try:
        for _ in range(60):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{api_port}/health", timeout=2) as r:
                    if r.status == 200:
                        break
            except OSError:
                time.sleep(1)
        else:
            pytest.fail("网关没起来:\n" + (home / "gw.out").read_text(errors="replace")[-3000:])
        # 网关先开门、插件的补丁后台再装 (P65 登记员工身份就在后台那一步); 装好之前进来的对话认不出是谁。
        # 等插件装完再发, 不然测的是启动那一两秒的竞态, 不是派帮手。
        agent_log = hh / "logs" / "agent.log"
        for _ in range(90):
            if agent_log.exists() and "delayed install ✓" in agent_log.read_text(errors="replace"):
                break
            time.sleep(1)
        else:
            pytest.fail("鲶鱼插件没装完:\n" + (agent_log.read_text(errors="replace")[-3000:] if agent_log.exists() else "没有 agent.log"))
        yield _converse(api_port, key), llm_log, target
    finally:
        gw.terminate()
        llm.terminate()
        for p in (gw, llm):
            try:
                p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                p.kill()


def _converse(port: int, key: str) -> dict:
    """跟 Companion 一样: 流式 + 会话号 + catfish_source; 遇到审批照 Companion 的方式回「拒绝」。"""
    body = {"model": "catfish-auto", "stream": True,
            "messages": [{"role": "user", "content": "DELEGATE-TEST 帮我并行查两件事"}]}
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions?catfish_source=companion-chat",
        data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "X-Catfish-User": EMPLOYEE, "X-Hermes-Session-Id": f"deleg-test-{secrets.token_hex(4)}"})
    content, approvals, ev = [], [], None
    with urllib.request.urlopen(req, timeout=180) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").rstrip("\n")
            if line.startswith("event: "):
                ev = line[7:].strip()
                continue
            if not line:
                ev = None
                continue
            if not line.startswith("data: ") or line[6:] == "[DONE]":
                continue
            try:
                d = json.loads(line[6:])
                if isinstance(d, str):
                    d = json.loads(d)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            if ev == "approval.request":
                approvals.append(d)
                ans = {"choice": "deny"}
                if d.get("request_id"):
                    ans["request_id"] = d["request_id"]
                if isinstance(d.get("execution_generation"), int):
                    ans["execution_generation"] = d["execution_generation"]
                urllib.request.urlopen(urllib.request.Request(
                    f"http://127.0.0.1:{port}/v1/runs/{d.get('run_id')}/approval", data=json.dumps(ans).encode(),
                    method="POST", headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}),
                    timeout=15).read()
                continue
            ch = d.get("choices")
            for c in ch if isinstance(ch, list) else []:
                if isinstance(c, dict) and isinstance(c.get("delta"), dict):
                    content.append(c["delta"].get("content") or "")
    return {"reply": "".join(content), "approvals": approvals}


def _requests(log: Path) -> list[dict]:
    return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]


def test_帮手的结果在同一轮回来(run):
    result, log, _ = run
    assert "ALPHA 结果: 找到 3 份资料" in result["reply"], result["reply"][:500]
    assert '"status": "dispatched"' not in result["reply"], "又走了后台: 员工要再说一句才看得到结果"
    kinds = [r["kind"] for r in _requests(log)]
    assert kinds.count("child-A") >= 1 and kinds.count("child-B") >= 1


def test_帮手要删文件_照常弹审批_拒绝后没删(run):
    result, log, target = run
    cmds = [a.get("command") for a in result["approvals"]]
    assert "rm -rf ~/catfish-del-test" in cmds, cmds
    assert any("每次都要确认" in str(a.get("description")) for a in result["approvals"])
    b_after = [r for r in _requests(log) if r["kind"] == "child-B" and r["last_role"] == "tool"]
    assert b_after and "BLOCKED" in (b_after[0]["tool_result"] or ""), b_after
    assert (target / "keep.txt").exists()


def test_帮手发给网关的请求带这一轮员工的身份(run):
    _, log, _ = run
    reqs = _requests(log)
    parent = [r for r in reqs if r["kind"] == "parent"]
    children = [r for r in reqs if r["kind"].startswith("child-")]
    assert parent and all(r["headers"].get("X-Catfish-User") == EMPLOYEE for r in parent)
    assert children, "没看到帮手的请求"
    got = {r["headers"].get("X-Catfish-User") for r in children}
    assert got == {EMPLOYEE}, f"帮手带的身份: {got} (machine.default@catfish = 没登记上, 退回了本机默认用户)"
    assert {r["headers"].get("X-Catfish-Source") for r in children} == {"companion-chat"}
