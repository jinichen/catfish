"""分享治理 · Companion 侧 (11/08, docs/SHARE-GOVERNANCE-PLAN.md §2.3 / §2.5 / §6): 装机验签 + 审批人试用 + 知识页待审 / 撤回。

全部虚构; skills 根 / HOME / CATFISH_HOME 都在临时目录; 中央走 httpx MockTransport (gateway) + 假 urlopen (skill 下载) 打桩。
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import httpx
import pytest

from catfish_tool_bridge import (
    catfish_tools_install as inst, expert_local, expert_pack, hermes_paths, skill_publish, skill_trial,
    wiki_install, wiki_search,
)
from catfish_tool_bridge import catfish_tools_skill_ops as ops


def _signer():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.generate()
    pub = base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
    return key, pub


FILES = {"SKILL.md": b"---\nname: eis-login\nversion: '1.0.0'\ndescription: x\n---\nbody\n", "notes.txt": b"hi"}
SHA = {p: hashlib.sha256(b).hexdigest() for p, b in FILES.items()}
BUNDLE = skill_trial.bundle_sha256(SHA)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """HOME / CATFISH_HOME / skills 根都在临时目录; 登录令牌有; 中央公钥记好。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setenv("CATFISH_SKILLS_DIR", str(skills))
    tok = tmp_path / "tok"
    tok.write_text("t0k")
    monkeypatch.setattr(skill_publish, "OAUTH_ID_TOKEN_PATH", tok)
    monkeypatch.setattr(hermes_paths, "hermes_home", lambda: tmp_path / "hermes")
    key, pub = _signer()
    expert_pack.pin_key(pub)
    return {"tmp": tmp_path, "skills": skills, "key": key, "pub": pub}


@pytest.fixture()
def gateway(monkeypatch):
    """httpx (gateway /v1/hub/...) 打桩: routes[(method, path)] = fn(req) -> Response。"""
    state = {"reqs": [], "routes": {}}

    def handler(req: httpx.Request) -> httpx.Response:
        state["reqs"].append(req)
        assert req.headers["Authorization"] == "Bearer t0k"
        fn = state["routes"].get((req.method, req.url.path))
        return fn(req) if fn else httpx.Response(404, json={"detail": "nope"})

    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return state


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture()
def hub_files(monkeypatch):
    """skill 元信息 + 文件下载走 urllib (catfish_tools_install._hub_open): 按 URL 返。"""
    state = {"meta": {}, "files": dict(FILES), "urls": []}

    def fake_urlopen(req, timeout=0):
        url = req.full_url
        state["urls"].append((url, req.headers.get("Authorization")))
        if "/files/" in url:
            name = url.split("/files/", 1)[1]
            if name not in state["files"]:
                raise urllib.error.HTTPError(url, 404, "nf", None, None)
            return _Resp(state["files"][name])
        return _Resp(json.dumps(state["meta"]).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return state


def _review(key, **kw):
    rec = {"namespace": "department", "name": "eis-login", "version": "1.0.0", "status": "approved", "scope": "department",
           "departments": ["安全部"], "bundle_sha256": BUNDLE, "reviewed_by": "boss@x", "reviewed_at": "2026-11-08T00:00:00Z", **kw}
    rec["signature"] = base64.b64encode(key.sign(skill_trial.skill_message(
        rec["namespace"], rec["name"], rec["version"], rec["bundle_sha256"], rec["reviewed_by"], rec["reviewed_at"]))).decode()
    return rec


def _meta(review):
    return {"namespace": "department", "name": "eis-login", "version": "1.0.0", "files": list(FILES), "files_sha256": SHA,
            "review": review}


def _trial_token(key, by="boss@x", expires=None):
    expires = expires or int(time.time()) + 7 * 86400
    sig = key.sign(skill_trial.skill_trial_message("department", "eis-login", "1.0.0", BUNDLE, by, expires))
    return {"namespace": "department", "name": "eis-login", "version": "1.0.0", "bundle_sha256": BUNDLE, "trial": True,
            "by": by, "expires": expires, "signature": base64.b64encode(sig).decode(), "public_key": "unused"}


# ── 指纹 / 消息格式 (跟 hub skill_review.py 一字不差) ────────────────

def test_指纹和签名消息格式跟中央一致():
    assert skill_trial.bundle_sha256({"b": "2", "a": "1"}) == hashlib.sha256(b"a\t1\nb\t2").hexdigest()
    assert skill_trial.skill_message("ns", "n", "1", "B", "boss", "T") == b"catfish-skill-v1\nns\nn\n1\nB\nboss\nT"
    assert skill_trial.skill_trial_message("ns", "n", "1", "B", "boss", 7) == b"catfish-skill-trial-v1\nns/n\n1\nB\nboss\n7"
    assert skill_trial.expert_trial_message("qual", "1", "S", "boss", 7) == b"catfish-expert-trial-v1\nqual\n1\nS\nboss\n7"


def test_正式安装验签_通过_改过的拒_没通过的拒_老版本照旧(home):
    rec = _review(home["key"])
    assert skill_trial.check_skill_review(rec, "department", "eis-login", "1.0.0", BUNDLE, "x")["status"] == "signed"
    with pytest.raises(ValueError, match="指纹对不上"):
        skill_trial.check_skill_review(rec, "department", "eis-login", "1.0.0", "deadbeef", "x")
    with pytest.raises(ValueError, match="验不过"):
        skill_trial.check_skill_review({**rec, "bundle_sha256": None}, "department", "eis-login", "1.0.0", "deadbeef", "x")
    with pytest.raises(ValueError, match="还没审核通过"):
        skill_trial.check_skill_review({**rec, "status": "pending"}, "department", "eis-login", "1.0.0", BUNDLE, "x")
    assert skill_trial.check_skill_review({"status": "approved", "legacy": True}, "d", "n", "1", BUNDLE, "x")["status"] == "legacy"
    assert skill_trial.check_skill_review(None, "d", "n", "1", BUNDLE, "x")["status"] == "unreviewed"


def test_试用令牌_过期拒_指纹不对拒(home):
    with pytest.raises(ValueError, match="过期"):
        skill_trial.check_skill_trial(_trial_token(home["key"], expires=int(time.time()) - 5), "department", "eis-login", "1.0.0", BUNDLE)
    with pytest.raises(ValueError, match="指纹"):
        skill_trial.check_skill_trial(_trial_token(home["key"]), "department", "eis-login", "1.0.0", "other")
    assert skill_trial.check_skill_trial(_trial_token(home["key"]), "department", "eis-login", "1.0.0", BUNDLE)["status"] == "trial"


# ── 从 hub 装: 带登录、按下载的文件重算、验签 ────────────────────────

def test_从hub装_带登录令牌_重算指纹验签_通过(home, hub_files):
    hub_files["meta"] = _meta(_review(home["key"]))
    r = inst._install_from_hub("department/eis-login@1.0.0", "http://gw/v1/hub")
    assert r["ok"], r
    assert r["review"]["status"] == "signed" and r["bundle_sha256"] == BUNDLE
    assert all(auth == "Bearer t0k" for _, auth in hub_files["urls"]), "每个请求都带员工自己的登录"
    assert not Path(r["staging_dir"]).exists() or (Path(r["staging_dir"]) / "SKILL.md").exists()


def test_从hub装_文件被改过_签名验不过_拒(home, hub_files):
    rec = _review(home["key"])
    tampered = dict(FILES, **{"notes.txt": b"evil"})
    hub_files["meta"] = {**_meta(rec), "files_sha256": {p: hashlib.sha256(b).hexdigest() for p, b in tampered.items()}}
    hub_files["files"] = tampered
    r = inst._install_from_hub("department/eis-login@1.0.0", "http://gw/v1/hub")
    assert r["ok"] is False and "指纹对不上" in r["error"]
    assert not list((home["tmp"] / ".catfish" / "skill-staging").iterdir()), "拒装后不留暂存"


def test_从hub装_还没通过的拒_403说人话(home, hub_files, monkeypatch):
    hub_files["meta"] = _meta({**_review(home["key"]), "status": "pending", "signature": None})
    r = inst._install_from_hub("department/eis-login@1.0.0", "http://gw/v1/hub")
    assert r["ok"] is False and "还没审核通过" in r["error"] and "试用" in r["error"]

    def forbidden(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 403, "no", None, None)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    r = inst._install_from_hub("department/eis-login@1.0.0", "http://gw/v1/hub")
    assert r["ok"] is False and "审核" in r["error"]


# ── 审批人试用 (技能) ───────────────────────────────────────────────

def test_技能试用_装上带标记_列表_结果回传_不在待审自动卸(home, gateway, hub_files):
    key = home["key"]
    token = _trial_token(key)
    hub_files["meta"] = _meta({**_review(key), "status": "pending", "signature": None})
    posted = {}
    gateway["routes"].update({
        ("GET", "/v1/hub/skills/department/eis-login/1.0.0/trial-token"): lambda r: httpx.Response(200, json=token),
        ("GET", "/v1/hub/skills/review"): lambda r: httpx.Response(200, json={"skills": [
            {"namespace": "department", "name": "eis-login", "version": "1.0.0", "status": "pending"}]}),
        ("POST", "/v1/hub/skills/department/eis-login/1.0.0/trial-result"): lambda r: (
            posted.update(json.loads(r.content)), httpx.Response(200, json={"ok": True, "trials": []}))[1],
    })
    r = skill_trial.install_skill_trial("department", "eis-login", "1.0.0")
    assert r["ok"] and r["trial"]["by"] == "boss@x" and "试用" in r["summary"]
    d = home["skills"] / "department" / "eis-login"
    marker = json.loads((d / "trial.json").read_text())
    assert marker["trial"] and marker["hub"] == {"namespace": "department", "name": "eis-login", "version": "1.0.0",
                                                 "url": f"{skill_publish.GATEWAY_URL}/v1/hub"}

    lst = skill_trial.list_skill_trials()["trials"]
    assert len(lst) == 1 and lst[0]["skill_path"] == "department/eis-login" and lst[0]["expired"] is False

    # 还在待审: 同步不动它
    assert skill_trial.sync_skill_trials()["removed"] == []

    # 回传试用结果 (没有运行记录也行)
    out = skill_trial.skill_trial_result("department/eis-login", True, "能跑, 登录成功")
    assert out["ok"] and posted == {"ok": True, "summary": "能跑, 登录成功", "files": [], "steps": []}

    # 驳回了: 不在待审 → 自动卸掉, 说明状态
    gateway["routes"][("GET", "/v1/hub/skills/review")] = lambda r: httpx.Response(200, json={"skills": []})
    gateway["routes"][("GET", "/v1/hub/skills/department/eis-login/1.0.0")] = lambda r: httpx.Response(
        200, json={"review": {"status": "rejected"}})
    removed = skill_trial.sync_skill_trials()["removed"]
    assert len(removed) == 1 and removed[0]["status"] == "rejected"
    assert not d.exists()
    assert skill_trial.list_skill_trials()["trials"] == []


def test_技能试用_顶掉了正式版_卸掉时挪回来(home, gateway, hub_files):
    key = home["key"]
    d = home["skills"] / "department" / "eis-login"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: eis-login\nversion: '0.9.0'\n---\n旧的正式版\n")
    hub_files["meta"] = _meta({**_review(key), "status": "pending", "signature": None})
    gateway["routes"][("GET", "/v1/hub/skills/department/eis-login/1.0.0/trial-token")] = lambda r: httpx.Response(200, json=_trial_token(key))
    assert skill_trial.install_skill_trial("department", "eis-login", "1.0.0")["ok"]
    assert "notes.txt" in {p.name for p in d.iterdir()}
    r = skill_trial.uninstall_skill_trial("department/eis-login")
    assert r["restored"] == str(d)
    assert "旧的正式版" in (d / "SKILL.md").read_text()
    with pytest.raises(LookupError, match="不是试用装的"):
        skill_trial.uninstall_skill_trial("department/eis-login")


def test_试用结果带上工作流运行的每步和文件(home, monkeypatch):
    from catfish_tool_bridge import workflow_store
    run = {"run_id": "r_20261108000000_abcdef", "skill_path": "pack-qual/qual-approve", "status": "done", "steps": [
        {"id": "login", "status": "done", "result": {"files": ["/tmp/a.xlsx"]}},
        {"id": "push", "status": "failed", "error": "没网"}]}
    workflow_store.save_run(run)
    steps, files, wf = skill_trial._run_evidence("r_20261108000000_abcdef")
    assert steps == ["login: done", "push: failed — 没网"] and files == ["/tmp/a.xlsx"] and wf == "pack-qual/qual-approve"


# ── 审批人试用 (专家包) ─────────────────────────────────────────────

SCRIPT = "def render_eis_login(params):\n    _call('catfish_browser_goto', {'url': 'https://eis.example.com/login'})\n    return {}\n"
DEF = {"id": "qual", "name": "资质专员", "description": "处理资质申请", "persona": "我是资质专员。",
       "workflows": [], "skills": ["personal/eis-login"],
       "routes": [{"when": "登录 EIS", "use": "skill:personal/eis-login"}], "tool_groups": []}


def _expert_pack(home, monkeypatch):
    d = home["skills"] / "personal" / "eis-login"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: eis-login\ndescription: 登录\n---\n")
    (d / "script.py").write_text(SCRIPT)
    monkeypatch.setattr(ops, "_skill_search_roots", lambda: [home["skills"], expert_pack.packs_root()])
    expert_local.save(DEF)
    data, _ = expert_pack.build("qual")
    import shutil
    shutil.rmtree(home["skills"])
    expert_local.delete("qual")
    return data


def test_专家包试用_名字带试用_列表带标_不在待审自动卸(home, gateway, monkeypatch):
    data = _expert_pack(home, monkeypatch)
    key = home["key"]
    sha = hashlib.sha256(data).hexdigest()
    expires = int(time.time()) + 7 * 86400
    token = {"id": "qual", "version": "1.0.0", "sha256": sha, "trial": True, "by": "boss@x", "expires": expires,
             "signature": base64.b64encode(key.sign(skill_trial.expert_trial_message("qual", "1.0.0", sha, "boss@x", expires))).decode()}
    posted = {}
    gateway["routes"].update({
        ("GET", "/v1/hub/experts/review"): lambda r: httpx.Response(200, json={"experts": [{"id": "qual", "version": "1.0.0"}]}),
        ("GET", "/v1/hub/experts/qual/1.0.0/trial-token"): lambda r: httpx.Response(200, json=token),
        ("GET", "/v1/hub/experts/qual/1.0.0/download"): lambda r: httpx.Response(200, content=data),
        ("GET", "/v1/hub/experts"): lambda r: httpx.Response(200, json={"experts": [{"id": "qual", "version": "1.0.0", "summary": {}}]}),
        ("POST", "/v1/hub/experts/qual/1.0.0/trial-result"): lambda r: (
            posted.update(json.loads(r.content)), httpx.Response(200, json={"ok": True}))[1],
    })
    assert skill_trial.pending_experts()["experts"][0]["id"] == "qual"
    assert skill_trial.install_expert_trial("qual", "1.0.0")["need_confirm"]
    r = skill_trial.install_expert_trial("qual", "1.0.0", confirmed=True)
    assert r["ok"] and r["signature"]["status"] == "trial"
    listed = expert_local.list_all()[0]
    assert listed["name"] == "资质专员 (试用)" and listed["pack"]["trial"]["by"] == "boss@x"
    assert expert_pack.market()["experts"][0]["installed_version"] is None, "试用的不算已装"
    with pytest.raises(ValueError, match="不能再打包"):
        expert_pack.build("qual")

    assert skill_trial.expert_trial_result("qual", False, "推送那步没过")["ok"]
    assert posted["ok"] is False and posted["summary"] == "推送那步没过"
    assert skill_trial.sync_expert_trials()["removed"] == []

    gateway["routes"][("GET", "/v1/hub/experts/review")] = lambda r: httpx.Response(200, json={"experts": []})
    gateway["routes"][("GET", "/v1/hub/experts/qual/1.0.0")] = lambda r: httpx.Response(200, json={"status": "approved"})
    removed = skill_trial.sync_expert_trials()["removed"]
    assert removed == [{"id": "qual", "name": "资质专员 (试用)", "version": "1.0.0", "status": "approved"}]
    assert expert_local.list_all() == []


def test_专家包试用_包被改过拒(home, gateway, monkeypatch):
    data = _expert_pack(home, monkeypatch)
    sha = hashlib.sha256(data).hexdigest()
    token = {"id": "qual", "version": "1.0.0", "sha256": sha, "by": "boss@x", "expires": int(time.time()) + 100, "signature": "AA=="}
    gateway["routes"].update({
        ("GET", "/v1/hub/experts/qual/1.0.0/trial-token"): lambda r: httpx.Response(200, json=token),
        ("GET", "/v1/hub/experts/qual/1.0.0/download"): lambda r: httpx.Response(200, content=data + b"\0"),
    })
    with pytest.raises(ValueError, match="sha256"):
        skill_trial.install_expert_trial("qual", "1.0.0", confirmed=True)


def test_pim_入口都在():
    names = set(skill_trial.pim_handlers({}))
    for a in ("skill_pending", "skill_install", "skill_list", "skill_uninstall", "skill_result", "skill_sync",
              "expert_pending", "expert_install", "expert_result", "expert_sync", "runs"):
        assert f"pim/trial_{a}" in names


# ── 发布: 范围 / 部门 / 给审核人的话 走 query, 结果说清在等审 ────────────

def test_发布带范围走query_结果说等主管审(home, gateway):
    skill_dir = home["tmp"] / "mine" / "weekly-report"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: weekly-report\n---\n写周报\n")
    got = {}

    def submit(req):
        got["q"] = dict(req.url.params)
        return httpx.Response(200, json={"namespace": "department", "name": "weekly-report", "version": "1.0.0",
                                         "review": {"status": "pending", "scope": "department"}})
    gateway["routes"][("POST", "/v1/hub/skills/department")] = submit
    r = skill_publish.skill_publish({"skill_path": str(skill_dir), "namespace": "department",
                                     "departments": ["安全部", "研发部"], "note": "请审"})
    assert r["ok"], r
    assert got["q"] == {"scope": "department", "departments": "安全部,研发部", "note": "请审"}
    assert "待审核" in r["summary"] and "部门主管" in r["summary"] and "安全部、研发部" in r["summary"]
    assert r["review"]["status"] == "pending"

    r2 = skill_publish.skill_publish({"skill_path": str(skill_dir), "namespace": "department", "scope": "company"})
    assert "管理员" in r2["summary"] and "全公司" in r2["summary"]
    assert skill_publish.skill_publish({"skill_path": str(skill_dir), "namespace": "department", "scope": "team"})["ok"] is False


# ── 知识页: 待审标 / 撤回标 ───────────────────────────────────────

def _doc(**kw):
    return {"namespace": "dept/研发部", "file_id": "abc123def456", "filename": "wiki/entities/老李.md", "title": "老李",
            "kind": "entity", "frontmatter_yaml": "title: 老李\ntype: entity", "body_md": "正文 资质评估流程",
            "published_by": "alice@x", "published_at": "2026-11-08T01:00:00Z", "updated_at": "2026-11-08T02:00:00Z",
            "stale_after_unpublish": False, **kw}


def test_知识页_装的时候记审核状态_搜索标待审_撤回后本机只标不删(home, gateway, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(home["tmp"] / ".catfish"))
    wiki_search._cache.update({"fingerprint": "", "index": None, "meta": []})
    doc = {"doc": _doc(review_status="pending")}
    gateway["routes"][("GET", "/v1/wiki/documents/dept/研发部/abc123def456")] = lambda r: httpx.Response(200, json=doc["doc"])
    r = wiki_install.wiki_install({"hub_namespace": "dept/研发部", "hub_file_id": "abc123def456"})
    assert r["ok"] and r["review_status"] == "pending" and "待审" in r["summary"]
    d = wiki_install._shared_root() / "dept" / "研发部"
    assert "review_status: pending" in (d / "abc123def456.md").read_text().split("\n---")[0]
    assert json.loads((d / "abc123def456.meta.json").read_text())["review_status"] == "pending"

    found = wiki_search.search_wiki("资质评估流程")
    assert found["count"] == 1 and found["matches"][0]["review"] == "待审" and "还没审过" in found["summary"]

    # 主管撤下: hub 返 withdrawn (body 清零); 「拉更新」→ 本机只标, 不删
    doc["doc"] = _doc(review_status="withdrawn", withdrawn_by="boss@x", review_note="写错了", stale_after_unpublish=True, body_md="")
    r = wiki_install.wiki_install({"hub_namespace": "dept/研发部", "hub_file_id": "abc123def456"})
    assert r["ok"] and r["withdrawn"] and r["review_note"] == "写错了"
    text = (d / "abc123def456.md").read_text()
    assert "review_status: withdrawn" in text and "正文 资质评估流程" in text, "员工的副本没删"
    assert json.loads((d / "abc123def456.meta.json").read_text())["withdrawn_by"] == "boss@x"

    wiki_search._cache.update({"fingerprint": "", "index": None, "meta": []})
    found = wiki_search.search_wiki("资质评估流程")
    assert found["count"] == 0 and found["hidden"] == {"withdrawn": 1} and "已撤回" in found["summary"]
    assert wiki_search.search_wiki("资质评估流程", include_withdrawn=True)["matches"][0]["review"] == "已撤回"

    # 本机没副本的撤回页: 照旧不让装
    (d / "abc123def456.md").unlink()
    r = wiki_install.wiki_install({"hub_namespace": "dept/研发部", "hub_file_id": "abc123def456"})
    assert r["ok"] is False and "撤回" in r["error"] and "写错了" in r["error"]


def test_frontmatter_设字段_替换已有的():
    t = wiki_install._set_frontmatter_field("---\ntitle: a\nreview_status: pending\n---\n\nbody", "review_status", "approved")
    assert t == "---\ntitle: a\nreview_status: approved\n---\n\nbody"
    assert wiki_install._set_frontmatter_field("plain", "review_status", "pending").startswith("---\nreview_status: pending\n---\n")
