"""专家包 (docs/EXPERT-PACK-PLAN.md 第二 / 三阶段): 打包 / 看包 / 安装 / 升级 / 卸载 / 验签 / 中央。

全部虚构; skills 根、CATFISH_HOME、hermes home 都在临时目录; 中央用 httpx MockTransport 打桩。
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import tarfile

import httpx
import pytest
import yaml

from catfish_tool_bridge import catfish_tools_skill_ops as ops
from catfish_tool_bridge import expert_local, expert_pack, hermes_paths, skill_publish, skill_workflow

SCRIPT = """def render_eis_login(params):
    _call('catfish_browser_goto', {'url': 'https://eis.example.com/login'})
    _call('catfish_browser_click', {'selector': '#ok'})
    return {}
"""


@pytest.fixture()
def env(tmp_path, monkeypatch):
    skills = tmp_path / "skills"

    def mk(path, md="", files=None):
        d = skills / path
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\nname: {d.name}\ndescription: {d.name} 说明\n---\n{md}")
        for name, body in (files or {}).items():
            (d / name).write_text(body)

    mk("department/eis-login", files={"script.py": SCRIPT})
    mk("personal/howto-cert", "照台账核对证书编号")
    wf = {"kind": "workflow", "params": {}, "steps": [
        {"id": "login", "skill": "department/eis-login@^1"},
        {"id": "push", "action": "wechat_push", "params": {"text": "好了"}}]}
    mk("personal/qual-approve", files={"workflow.yaml": yaml.safe_dump(wf, allow_unicode=True)})
    monkeypatch.setattr(ops, "_skill_search_roots", lambda: [skills, expert_pack.packs_root()])
    monkeypatch.setattr(hermes_paths, "hermes_home", lambda: tmp_path / "hermes")
    return {"skills": skills, "tmp": tmp_path}


DEF = {
    "id": "qual", "name": "资质专员", "description": "处理资质申请", "persona": "我是资质专员, 说话简短。",
    "workflows": ["personal/qual-approve"], "skills": ["personal/howto-cert"],
    "routes": [{"when": "审资质待办", "use": "workflow:personal/qual-approve"},
               {"when": "证书怎么核对", "use": "skill:personal/howto-cert"},
               {"when": "其它", "use": "decline"}],
    "checks": [{"after": "personal/qual-approve", "verify": "每条写明结论"}],
    "tool_groups": ["knowledge"], "quick_prompts": ["这周待审批"],
}


def _files(data: bytes) -> dict[str, bytes]:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        return {m.name: tf.extractfile(m).read() for m in tf.getmembers() if m.isfile()}


def _move_away(env):
    """模拟换一台电脑: 原来的技能都不在了, 本机专家定义也没有。"""
    import shutil
    shutil.rmtree(env["skills"])
    expert_local.delete("qual")


def test_打包_带上工作流用到的技能_引用改写成包内(env):
    expert_local.save(DEF)
    data, info = expert_pack.build("qual")
    assert info["version"] == "1.0.0" and info["filename"] == "qual-1.0.0.catfish-expert"
    files = _files(data)
    assert {"expert.yaml", "SOUL.md", "skills/eis-login/script.py", "skills/howto-cert/SKILL.md",
            "workflows/qual-approve/workflow.yaml"} <= set(files)
    wf = yaml.safe_load(files["workflows/qual-approve/workflow.yaml"])
    assert wf["steps"][0]["skill"] == "pack-qual/eis-login", "部门技能也打进包, 不留外部依赖"
    man = yaml.safe_load(files["expert.yaml"])
    assert man["routes"][0]["use"] == "workflow:pack-qual/qual-approve"
    assert man["checks"][0]["after"] == "pack-qual/qual-approve"
    p = info["permissions"]
    assert "推送到微信" in "".join(p["outward"]) or p["outward"], "工作流里的推送算对外"
    assert p["outward_actions"] == ["wechat_push"], "审核页靠它标「停下等确认」"
    assert p["browser"] and p["sites"] == ["https://eis.example.com/login"]
    assert not p["code"] and not p["web"]


def test_打包拒_手机号(env):
    expert_local.save(DEF)
    (env["skills"] / "personal/howto-cert/SKILL.md").write_text("---\nname: x\n---\n联系 13812345678\n")
    with pytest.raises(ValueError, match="个人数据"):
        expert_pack.build("qual")


def test_脚本调白名单外的工具_拒(env):
    expert_local.save(DEF)
    (env["skills"] / "department/eis-login/script.py").write_text(
        "def render_eis_login(params):\n    _call('catfish_terminal_run', {'cmd': 'ls'})\n")
    with pytest.raises(ValueError, match="没过检查"):
        expert_pack.build("qual")


def test_版本只能往上加(env):
    expert_local.save(DEF)
    out = expert_pack.export("qual", dest=str(env["tmp"] / "out" / "q.catfish-expert"))
    assert out["version"] == "1.0.0"
    assert expert_pack.preview("qual")["version"] == "1.0.1", "下次默认补丁号 +1"
    with pytest.raises(ValueError, match="不比上次"):
        expert_pack.build("qual", "1.0.0")
    expert_local.save({**DEF, "description": "改了"})
    assert expert_local.load("qual")["pack"]["version"] == "1.0.0", "保存定义不丢打包记录"


def test_装到别的电脑_要先确认_装好能跑能编译(env):
    expert_local.save(DEF)
    data, _ = expert_pack.build("qual")
    _move_away(env)
    first = expert_pack.install(data)
    assert first == {"ok": False, "need_confirm": True, "inspect": first["inspect"]}
    assert first["inspect"]["state"] == "new" and first["inspect"]["problems"] == []
    r = expert_pack.install(data, confirmed=True)
    assert r["ok"] and r["profile_id"] == "expert-qual" and r["signature"] == {"status": "unsigned"}
    root = expert_pack.packs_root() / "pack-qual"
    assert (root / "eis-login" / "script.py").exists() and (root / "qual-approve" / "workflow.yaml").exists()
    assert not (env["tmp"] / "skills").exists() or not any((env["tmp"] / "skills").rglob("eis-login")), "不进 skills/"
    # 工作流引用能被运行器找到 + 校验通过
    wf = yaml.safe_load((root / "qual-approve" / "workflow.yaml").read_text())
    assert skill_workflow.validate(wf, skill_lookup=ops._find_skill_dir) == []
    c = expert_local.compile("qual")
    assert "工作流 `catfish_run_skill(skill_path=\"pack-qual/qual-approve\"" in c["soul"]
    assert "我是资质专员" in c["soul"]
    listed = expert_local.list_all()[0]
    assert listed["pack"]["version"] == "1.0.0" and listed["pack"]["signed"] is False
    with pytest.raises(ValueError, match="不能直接改"):
        expert_local.save({**DEF, "workflows": ["pack-qual/qual-approve"], "skills": [], "routes": [
            {"when": "x", "use": "workflow:pack-qual/qual-approve"}]})
    with pytest.raises(ValueError, match="不能再打包"):
        expert_pack.build("qual")


def test_升级_权限变多要重新确认_卸载只删自己(env):
    expert_local.save(DEF)
    v1, _ = expert_pack.build("qual")
    expert_local.save({**DEF, "tool_groups": ["knowledge", "forms"]})
    v2, _ = expert_pack.build("qual", "1.0.1")
    _move_away(env)
    expert_pack.install(v1, confirmed=True)
    with pytest.raises(ValueError, match="不用再装"):
        expert_pack.install(v1, confirmed=True)
    need = expert_pack.install(v2)
    assert need["need_confirm"] and any("对外" in x for x in need["inspect"]["expanded"])
    assert expert_pack.install(v2, confirmed=True)["upgraded"]
    assert expert_local.load("qual")["source"]["version"] == "1.0.1"
    assert list(expert_pack.packs_root().iterdir()) == [expert_pack.packs_root() / "pack-qual"], "没留下临时目录"
    expert_pack.uninstall("qual")
    assert not (expert_pack.packs_root() / "pack-qual").exists()
    with pytest.raises(LookupError):
        expert_local.load("qual")


def test_本机已有同id的自建专家_装不上(env):
    expert_local.save(DEF)
    data, _ = expert_pack.build("qual")
    with pytest.raises(ValueError, match="自己搭的"):
        expert_pack.install(data, confirmed=True)


@pytest.mark.parametrize("evil", ["../x.sh", "/etc/passwd"])
def test_坏包拒(evil):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo(evil)
        info.size = 1
        tf.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(ValueError, match="不安全"):
        expert_pack.read_pack(buf.getvalue())


def test_包里工作流引用包外技能_拒(env):
    man = {"kind": "catfish-expert", "id": "qual", "version": "1.0.0", "name": "x",
           "routes": [{"when": "x", "use": "workflow:pack-qual/w"}], "workflows": ["pack-qual/w"]}
    files = {"expert.yaml": yaml.safe_dump(man).encode(), "SOUL.md": b"",
             "workflows/w/SKILL.md": b"---\nname: w\n---\n",
             "workflows/w/workflow.yaml": yaml.safe_dump({"kind": "workflow", "steps": [
                 {"id": "a", "skill": "personal/secret-thing"}]}).encode()}
    info = expert_pack.inspect(expert_pack._tar(files))
    assert any("包外的技能" in p for p in info["problems"])
    with pytest.raises(ValueError, match="没过检查"):
        expert_pack.install(expert_pack._tar(files), confirmed=True)


# ── 签名 + 中央 ──────────────────────────────────────────────────

def _signer():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.generate()
    pub = base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
    return key, pub


def _meta(key, data, **kw):
    m = {"id": "qual", "version": "1.0.0", "sha256": hashlib.sha256(data).hexdigest(), "status": "approved",
         "reviewed_by": "boss@example.com", "reviewed_at": "2026-10-18T00:00:00+00:00", **kw}
    m["signature"] = base64.b64encode(key.sign(expert_pack.signed_message(
        m["id"], m["version"], m["sha256"], m["reviewed_by"], m["reviewed_at"]))).decode()
    return m


def test_签名内容格式跟中央一致():
    # 中央 central/skills-hub experts.signed_message 同一格式, 改一边要改另一边
    assert expert_pack.signed_message("qual", "1.0.0", "ab", "boss@example.com", "T") == \
        b"catfish-expert-v1\nqual\n1.0.0\nab\nboss@example.com\nT"


def test_签名_对的过_改过的包和别的钥匙都拒(env):
    key, pub = _signer()
    data = b"pack-bytes"
    meta = _meta(key, data)
    assert expert_pack.verify_signature(meta, data, pub)["status"] == "signed"
    with pytest.raises(ValueError, match="sha256"):
        expert_pack.verify_signature(meta, data + b"x", pub)
    _, other = _signer()
    with pytest.raises(ValueError, match="签名验不过"):
        expert_pack.verify_signature(meta, data, other)
    with pytest.raises(ValueError, match="签名验不过"):
        expert_pack.verify_signature({**meta, "reviewed_by": "someone@example.com"}, data, pub)


def test_公钥第一次记下_换了就拒(env):
    _, a = _signer()
    _, b = _signer()
    expert_pack.pin_key(a)
    assert expert_pack.pinned_key() == a
    with pytest.raises(ValueError, match="冒充"):
        expert_pack.pin_key(b)


@pytest.fixture()
def hub(env, monkeypatch, tmp_path):
    token = tmp_path / "tok"
    token.write_text("t0k")
    monkeypatch.setattr(skill_publish, "OAUTH_ID_TOKEN_PATH", token)
    state = {"reqs": [], "routes": {}}

    def handler(req: httpx.Request) -> httpx.Response:
        state["reqs"].append(req)
        assert req.headers["Authorization"] == "Bearer t0k"
        fn = state["routes"].get((req.method, req.url.path))
        return fn(req) if fn else httpx.Response(404, json={"detail": "nope"})

    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return state


def test_发布_走gateway带登录_记下版本(hub):
    expert_local.save(DEF)
    got = {}

    def submit(req):
        got["body"] = req.content
        return httpx.Response(200, json={"id": "qual", "version": "1.0.0", "status": "pending"})

    hub["routes"][("POST", "/v1/hub/experts")] = submit
    r = expert_pack.publish("qual", "department", ["安全部"], "请审")
    assert r["status"] == "pending" and b"qual-1.0.0.catfish-expert" in got["body"]
    assert "安全部".encode() in got["body"] or json.dumps(["安全部"]).encode() in got["body"]
    assert expert_local.load("qual")["pack"]["published"]["status"] == "pending"


def test_从市场装_验签装上_公钥记下(hub, env):
    expert_local.save(DEF)
    data, _ = expert_pack.build("qual")
    _move_away(env)
    key, pub = _signer()
    meta = _meta(key, data)
    hub["routes"].update({
        ("GET", "/v1/hub/experts"): lambda r: httpx.Response(200, json={"experts": [{**meta, "summary": {}}]}),
        ("GET", "/v1/hub/experts/qual/1.0.0"): lambda r: httpx.Response(200, json=meta),
        ("GET", "/v1/hub/experts/signing-key"): lambda r: httpx.Response(200, json={"public_key": pub}),
        ("GET", "/v1/hub/experts/qual/1.0.0/download"): lambda r: httpx.Response(200, content=data),
    })
    assert expert_pack.market()["experts"][0]["installed_version"] is None
    assert expert_pack.install_from_hub("qual", "1.0.0")["need_confirm"]
    r = expert_pack.install_from_hub("qual", "1.0.0", confirmed=True)
    assert r["ok"] and r["signature"]["status"] == "signed" and r["signature"]["reviewed_by"] == "boss@example.com"
    assert expert_pack.pinned_key() == pub
    assert expert_local.list_all()[0]["pack"]["signed"] is True


def test_从市场装_被改过的包拒(hub, env):
    expert_local.save(DEF)
    data, _ = expert_pack.build("qual")
    key, pub = _signer()
    meta = _meta(key, data)
    hub["routes"].update({
        ("GET", "/v1/hub/experts/qual/1.0.0"): lambda r: httpx.Response(200, json=meta),
        ("GET", "/v1/hub/experts/signing-key"): lambda r: httpx.Response(200, json={"public_key": pub}),
        ("GET", "/v1/hub/experts/qual/1.0.0/download"): lambda r: httpx.Response(200, content=data + b"\0"),
    })
    with pytest.raises(ValueError, match="sha256"):
        expert_pack.install_from_hub("qual", "1.0.0", confirmed=True)


def test_没登录_说清楚(env):
    with pytest.raises(ValueError, match="登录"):
        expert_pack.market()


def test_pim_入口都在(env):
    names = set(expert_local.pim_handlers({}))
    for a in ("preview", "export", "inspect", "install", "uninstall", "publish", "market", "mine", "withdraw"):
        assert f"pim/expert_pack_{a}" in names
