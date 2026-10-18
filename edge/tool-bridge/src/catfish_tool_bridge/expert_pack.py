"""专家包 (docs/EXPERT-PACK-PLAN.md 第二 / 三阶段): 打包 / 导出 / 看包 / 安装 / 升级 / 卸载 / 发布到中央 / 市场 / 验签。

包 = `<id>-<版本>.catfish-expert` (tar.gz):
  expert.yaml   清单: id / 版本 / 名字 / 选路 / 核对 / 能力组 / 用到的工作流和技能 (都写成 pack-<id>/<名字>)
  SOUL.md       人设 (选路 / 核对 / 规矩安装时再编进去, 跟本机专家同一套 build_soul)
  skills/<名字>/     包里自带的技能 (凝固脚本 或 说明型)
  workflows/<名字>/  包里自带的工作流

几条做法 (10/18 定):
- **包自带全部依赖**: 工作流某一步用到的技能 (个人的、部门的都一样) 一起打进包, 引用改写成 `pack-<id>/<名字>` ——
  包不依赖任何人本机的个人 skill, 审核的人看到的就是会跑的全部。
- **装到 `~/.catfish/expert-packs/pack-<id>/<名字>/`**, 不进 `~/.catfish/skills`: 主小鲶挂的是整个 skills 目录,
  放进去它就看得到了; run_skill 的搜索根加上这个目录, 专家 (和它的工作流) 照样找得到。
- **权限摘要是从包里实际内容算的** (能力组 + 工作流积木的级别 + 脚本调的工具 + 脚本里的网址), 不信清单自报。
- **从中央装的要验**: 下载内容的 sha256 == 审核时记的; 中央的 Ed25519 签名能验过 (公钥第一次用时记在
  `~/.catfish/hub_signing_key.json`, 之后换了就拒 —— 防有人冒充中央)。从文件装的标「未签名」。
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import secrets
import shutil
import tarfile
from pathlib import Path
from typing import Any

import yaml

from . import expert_local, skill_publish, skill_verify, skill_workflow, trace_recorder, wiki_files, workflow_store
from . import workflow_catalog as catalog

KIND = "catfish-expert"
EXT = ".catfish-expert"
MAX_PACK_BYTES = 20 * 1024 * 1024
_VER = re.compile(r"^\d+\.\d+\.\d+$")
_URL = re.compile(r"""https?://[^\s'"<>)\]]+""")
_RENDER = re.compile(r"^def render_([A-Za-z0-9_]+)\s*\(", re.M)
_CALL = re.compile(r"""_call\(\s*['"]([a-z_]+)['"]""")
PACK_NS = re.compile(r"^pack-[a-z][a-z0-9-]{1,30}$")

#: 能力组 → 权限摘要里归哪一级 (看 TOOL_GROUPS 每组实际露的工具定的)
GROUP_LEVEL = {"knowledge": "read", "wechat_records": "read", "email": "local", "tasks": "local", "browser": "local",
               "forms": "outward"}
#: 凝固脚本里调到这些工具 = 会操作员工的浏览器
BROWSER_TOOLS = frozenset(t for t in trace_recorder.RECORDED_TOOLS if t.startswith("catfish_browser_"))


def packs_root() -> Path:
    return wiki_files._catfish_home() / "expert-packs"


def pack_ns(pack_id: str) -> str:
    return f"pack-{pack_id}"


def _key_file() -> Path:
    return wiki_files._catfish_home() / "hub_signing_key.json"


def _ver_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def _bump(v: str) -> str:
    a, b, c = _ver_tuple(v)
    return f"{a}.{b}.{c + 1}"


# ── 读包 ─────────────────────────────────────────────────────────

def read_pack(data: bytes) -> tuple[dict[str, Any], dict[str, bytes]]:
    """解出清单和文件 (不落盘)。不安全的路径、软链、超大、不是专家包都拒。"""
    if len(data) > MAX_PACK_BYTES:
        raise ValueError(f"包太大, 上限 {MAX_PACK_BYTES // 1024 // 1024}MB")
    try:
        tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")
    except (tarfile.TarError, OSError) as e:
        raise ValueError(f"不是有效的专家包: {e}") from None
    files: dict[str, bytes] = {}
    with tf:
        for m in tf.getmembers():
            name = m.name[2:] if m.name.startswith("./") else m.name  # 不能 lstrip("./"): 会把 ../x 吃成 x
            if m.issym() or m.islnk() or m.name.startswith("/") or ".." in name.split("/"):
                raise ValueError(f"包里有不安全的路径: {m.name}")
            if m.isfile():
                f = tf.extractfile(m)
                files[name] = f.read() if f else b""
    if "expert.yaml" not in files:
        raise ValueError("包里没有 expert.yaml")
    try:
        man = yaml.safe_load(files["expert.yaml"].decode("utf-8")) or {}
    except (yaml.YAMLError, UnicodeDecodeError) as e:
        raise ValueError(f"expert.yaml 读不了: {e}") from None
    if man.get("kind") != KIND:
        raise ValueError("这不是专家包 (expert.yaml 的 kind 不是 catfish-expert)")
    pid, ver = str(man.get("id") or ""), str(man.get("version") or "")
    try:
        expert_local._dir(pid)
    except ValueError:
        raise ValueError(f"专家包 id 不对: {pid!r}") from None
    if not _VER.match(ver):
        raise ValueError(f"版本号要写成 x.y.z: {ver!r}")
    return man, files


def _units(files: dict[str, bytes]) -> dict[str, dict[str, dict[str, bytes]]]:
    """{'skills': {名字: {相对路径: 内容}}, 'workflows': {...}}"""
    out: dict[str, dict[str, dict[str, bytes]]] = {"skills": {}, "workflows": {}}
    for name, body in files.items():
        parts = name.split("/")
        if len(parts) >= 3 and parts[0] in out:
            out[parts[0]].setdefault(parts[1], {})["/".join(parts[2:])] = body
    return out


# ── 权限摘要 ─────────────────────────────────────────────────────

def permissions(man: dict[str, Any], files: dict[str, bytes]) -> dict[str, Any]:
    """从包里实际内容算: 查 / 写本机 / 对外 各有什么, 会不会操作浏览器, 会打开哪些网站, 要不要终端 / 上网。"""
    levels: dict[str, list[str]] = {"read": [], "local": [], "outward": []}

    def add(level: str, label: str) -> None:
        if label not in levels[level]:
            levels[level].append(label)

    for g in man.get("tool_groups") or []:
        if g in expert_local.TOOL_GROUPS:
            add(GROUP_LEVEL.get(g, "local"), expert_local.TOOL_GROUPS[g]["label"])
    browser = "browser" in (man.get("tool_groups") or [])
    outward_actions: list[str] = []
    units = _units(files)
    for wf_files in units["workflows"].values():
        try:
            wf = yaml.safe_load(wf_files.get("workflow.yaml", b"").decode("utf-8")) or {}
        except (yaml.YAMLError, UnicodeDecodeError):
            continue
        for st in wf.get("steps") or []:
            a = catalog.get(str(st.get("action") or "")) if isinstance(st, dict) and "action" in st else None
            if a is not None:
                add(a.level, a.label)
                if a.level == catalog.OUTWARD and a.name not in outward_actions:
                    outward_actions.append(a.name)
    sites: set[str] = set()
    for sk in units["skills"].values():
        script = sk.get("script.py", b"").decode("utf-8", errors="ignore")
        if set(_CALL.findall(script)) & BROWSER_TOOLS:
            browser = True
        for body in sk.values():
            sites |= {u.rstrip(".,;'\"") for u in _URL.findall(body.decode("utf-8", errors="ignore"))}
    if browser:
        add("local", "操作你的浏览器")
    # outward_actions: 审核页按这个标「跑到这步停下等员工确认」(对外积木一律会停, 不看 step 里写没写 confirm)
    return {**levels, "outward_actions": outward_actions, "browser": browser, "sites": sorted(sites)[:50],
            "code": bool(man.get("allow_code")), "web": bool(man.get("allow_web"))}


def expanded(old: dict[str, Any] | None, new: dict[str, Any]) -> list[str]:
    """新版比旧版多出来的权限 (一句一条); 空 = 没变多。"""
    if not old:
        return []
    out = [f"新的对外能力: {x}" for x in new.get("outward", []) if x not in old.get("outward", [])]
    out += [f"新的写本机能力: {x}" for x in new.get("local", []) if x not in old.get("local", [])]
    out += [f"新的网站: {x}" for x in new.get("sites", []) if x not in old.get("sites", [])]
    if new.get("code") and not old.get("code"):
        out.append("要用终端 / 执行代码")
    if new.get("web") and not old.get("web"):
        out.append("要上网搜索")
    return out


# ── 检查 (打包和安装都过一遍) ─────────────────────────────────────

def _check_files(files: dict[str, bytes]) -> list[str]:
    problems = []
    hits = skill_publish._scan_sensitive(files)
    if hits:
        problems.append("包里有像密码 / 密钥的内容: " + "; ".join(hits[:5]))
    hits = skill_publish._scan_pii(files)
    if hits:
        problems.append("包里有员工个人数据 (先脱敏再打包): " + "; ".join(hits[:5]))
    for name, sk in _units(files)["skills"].items():
        script = sk.get("script.py")
        if script is None:
            if "SKILL.md" not in sk:
                problems.append(f"技能 {name} 没有 SKILL.md")
            continue
        text = script.decode("utf-8", errors="ignore")
        m = _RENDER.search(text)
        if not m:
            problems.append(f"技能 {name} 的脚本里没有 render_ 函数")
            continue
        r = skill_verify.verify_frozen_script(text, trace_recorder.RECORDED_TOOLS, m.group(1))
        if not r.ok:
            problems.append(f"技能 {name} 的脚本没过检查: " + "; ".join(r.errors[:3]))
    return problems


def _check_workflows(pid: str, files: dict[str, bytes]) -> list[str]:
    """工作流里的技能引用只能指向包里自带的。"""
    units = _units(files)
    ns = pack_ns(pid)
    problems = []
    for name, wf_files in units["workflows"].items():
        try:
            wf = yaml.safe_load(wf_files.get("workflow.yaml", b"").decode("utf-8")) or {}
        except (yaml.YAMLError, UnicodeDecodeError) as e:
            problems.append(f"工作流 {name} 的 workflow.yaml 读不了: {e}")
            continue
        for st in wf.get("steps") or []:
            sp = str(st.get("skill") or "") if isinstance(st, dict) else ""
            if sp and not (sp.startswith(ns + "/") and sp.split("/", 1)[1] in units["skills"]):
                problems.append(f"工作流 {name} 用了包外的技能 {sp}")
            elif sp and "script.py" not in units["skills"][sp.split("/", 1)[1]]:
                problems.append(f"工作流 {name} 的一步用了说明型技能 {sp} (没有脚本, 不能当一步)")
    return problems


# ── 打包 ─────────────────────────────────────────────────────────

def _skill_files(d: Path) -> dict[str, bytes]:
    return {k: v for k, v in skill_publish._collect_files(d).items()
            if not k.startswith(".") and ".unverified" not in k and "__pycache__" not in k}


def build(expert_id: str, version: str | None = None) -> tuple[bytes, dict[str, Any]]:
    """把本机专家打成包。返回 (包内容, 摘要)。有问题一次说全 (ValueError)。"""
    raw = expert_local.load(expert_id)
    if (raw.get("source") or {}).get("kind") == "pack":
        raise ValueError("这是从专家包装来的专家, 不能再打包 (要改请找原作者发新版本)")
    d = expert_local.normalize(raw)
    last = (raw.get("pack") or {}).get("version")
    version = (version or "").strip() or (_bump(last) if last else "1.0.0")
    if not _VER.match(version):
        raise ValueError(f"版本号要写成 x.y.z: {version!r}")
    if last and _ver_tuple(version) <= _ver_tuple(last):
        raise ValueError(f"版本 {version} 不比上次打包的 {last} 新")
    ns = pack_ns(d["id"])
    lookup = expert_local._skill_lookup

    # 要带上的: 选中的工作流 + 技能, 再加工作流各步用到的技能
    wf_dirs: dict[str, Path] = {p: lookup(p) for p in d["workflows"]}  # type: ignore[misc]
    sk_paths = list(d["skills"])
    wf_docs: dict[str, dict[str, Any]] = {}
    for p, wd in wf_dirs.items():
        wf = yaml.safe_load((wd / "workflow.yaml").read_text(encoding="utf-8")) or {}
        wf_docs[p] = wf
        for st in wf.get("steps") or []:
            sp = str(st.get("skill") or "").split("@")[0] if isinstance(st, dict) else ""
            if sp and sp not in sk_paths:
                sk_paths.append(sp)
    problems = []
    names: dict[str, str] = {}
    used: set[str] = set()
    for p in list(wf_dirs) + sk_paths:
        base = p.split("/")[-1]
        name = base if base not in used else f"{p.split('/')[0]}-{base}"
        used.add(name)
        names[p] = name
    sk_dirs = {}
    for p in sk_paths:
        sd = lookup(p)
        if sd is None:
            problems.append(f"工作流用到的技能 {p} 在这台电脑上找不到")
        else:
            sk_dirs[p] = sd

    def ref(p: str) -> str:
        return f"{ns}/{names[p.split('@')[0]]}"

    files: dict[str, bytes] = {}
    for p, sd in sk_dirs.items():
        for rel, body in _skill_files(sd).items():
            files[f"skills/{names[p]}/{rel}"] = body
    for p, wd in wf_dirs.items():
        for rel, body in _skill_files(wd).items():
            if rel != "workflow.yaml":
                files[f"workflows/{names[p]}/{rel}"] = body
        wf = wf_docs[p]
        for st in wf.get("steps") or []:
            if isinstance(st, dict) and st.get("skill") and str(st["skill"]).split("@")[0] in names:
                st["skill"] = ref(str(st["skill"]))
        files[f"workflows/{names[p]}/workflow.yaml"] = yaml.safe_dump(wf, allow_unicode=True, sort_keys=False).encode()

    def map_use(use: str) -> str:
        if ":" not in use:
            return use
        kind, p = use.split(":", 1)
        return f"{kind}:{ref(p)}" if p in names else use

    man = {
        "kind": KIND, "id": d["id"], "version": version, "name": d["name"], "description": d["description"],
        "quick_prompts": d["quick_prompts"], "model": d["model"],
        "routes": [{**r, "use": map_use(r["use"])} for r in d["routes"]],
        "checks": [{**c, "after": ref(c["after"]) if c.get("after") in names else c.get("after", "")} for c in d["checks"]],
        "tool_groups": d["tool_groups"], "allow_web": d["allow_web"], "allow_code": d["allow_code"],
        "workflows": [ref(p) for p in d["workflows"]], "skills": [ref(p) for p in d["skills"]],
        "built_at": workflow_store.now(),
    }
    files["SOUL.md"] = (d["persona"] or "").encode()
    perms = permissions(man, files)
    man["summary"] = perms
    files["expert.yaml"] = yaml.safe_dump(man, allow_unicode=True, sort_keys=False).encode()
    problems += _check_files(files) + _check_workflows(d["id"], files)
    if problems:
        raise ValueError("; ".join(problems))
    data = _tar(files)
    if len(data) > MAX_PACK_BYTES:
        raise ValueError(f"包太大, 上限 {MAX_PACK_BYTES // 1024 // 1024}MB")
    return data, {"id": d["id"], "version": version, "name": d["name"], "permissions": perms,
                  "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
                  "workflows": man["workflows"], "skills": sorted(f"{ns}/{n}" for n in {names[p] for p in sk_dirs}),
                  "filename": f"{d['id']}-{version}{EXT}"}


def _tar(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name in sorted(files):
            info = tarfile.TarInfo(name)
            info.size = len(files[name])
            info.mtime = 0
            info.mode = 0o644
            tf.addfile(info, io.BytesIO(files[name]))
    return buf.getvalue()


def _note_packed(expert_id: str, version: str, **extra: Any) -> None:
    p = expert_local._dir(expert_id) / "expert.yaml"
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    raw["pack"] = {**(raw.get("pack") or {}), "version": version, "at": workflow_store.now(), **extra}
    p.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")


def preview(expert_id: str, version: str | None = None) -> dict[str, Any]:
    _, info = build(expert_id, version)
    return {"ok": True, **info}


def export(expert_id: str, version: str | None = None, dest: str | None = None) -> dict[str, Any]:
    data, info = build(expert_id, version)
    out = Path(dest).expanduser() if dest else wiki_files._catfish_home() / "exports" / info["filename"]
    if out.suffix != EXT:
        out = out.with_name(out.name + EXT)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    _note_packed(expert_id, info["version"], exported=str(out))
    return {"ok": True, "path": str(out), **info}


# ── 验签 ─────────────────────────────────────────────────────────

def pinned_key() -> str | None:
    try:
        return json.loads(_key_file().read_text(encoding="utf-8")).get("public_key") or None
    except (OSError, ValueError):
        return None


def pin_key(public_key_b64: str) -> str:
    """第一次: 记下中央公钥; 以后中央给的跟记下的不一样 → 拒 (删这个文件才能重新信任)。"""
    have = pinned_key()
    if have and have != public_key_b64:
        raise ValueError("中央的签名公钥跟这台电脑上次记下的不一样 —— 可能有人冒充中央, 先别装, 找管理员确认")
    if not have:
        p = _key_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"public_key": public_key_b64, "pinned_at": workflow_store.now()}), encoding="utf-8")
    return public_key_b64


def signed_message(pack_id: str, version: str, sha256: str, approved_by: str, approved_at: str) -> bytes:
    """跟中央 (skills-hub experts.signed_message) 一字不差。"""
    return f"catfish-expert-v1\n{pack_id}\n{version}\n{sha256}\n{approved_by}\n{approved_at}".encode()


def verify_signature(meta: dict[str, Any], data: bytes, public_key_b64: str) -> dict[str, Any]:
    from cryptography.exceptions import InvalidSignature  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey  # noqa: PLC0415

    sha = hashlib.sha256(data).hexdigest()
    if sha != meta.get("sha256"):
        raise ValueError("下载到的包跟中央审核时的不一致 (sha256 对不上), 不装")
    if not meta.get("signature"):
        raise ValueError("这个包没有中央签名 (还没审核通过?), 不装")
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64)).verify(
            base64.b64decode(meta["signature"]),
            signed_message(meta["id"], meta["version"], sha, meta.get("reviewed_by") or "", meta.get("reviewed_at") or ""))
    except (InvalidSignature, ValueError, TypeError):
        raise ValueError("中央签名验不过, 不装") from None
    return {"status": "signed", "reviewed_by": meta.get("reviewed_by"), "reviewed_at": meta.get("reviewed_at")}


# ── 看包 / 安装 / 卸载 ───────────────────────────────────────────

def _installed(pack_id: str) -> dict[str, Any] | None:
    try:
        raw = expert_local.load(pack_id)
    except (LookupError, ValueError):
        return None
    return raw


def inspect(data: bytes) -> dict[str, Any]:
    man, files = read_pack(data)
    perms = permissions(man, files)
    problems = _check_files(files) + _check_workflows(man["id"], files)
    cur = _installed(man["id"])
    src = (cur or {}).get("source") or {}
    if cur is None:
        state = "new"
    elif src.get("kind") != "pack":
        state = "conflict"
    elif _ver_tuple(man["version"]) > _ver_tuple(src.get("version") or "0.0.0"):
        state = "upgrade"
    elif man["version"] == src.get("version"):
        state = "same"
    else:
        state = "older"
    return {
        "ok": True, "id": man["id"], "version": man["version"], "name": str(man.get("name") or man["id"]),
        "description": str(man.get("description") or ""), "routes": man.get("routes") or [],
        "quick_prompts": man.get("quick_prompts") or [],
        "workflows": man.get("workflows") or [], "skills": sorted(_units(files)["skills"]),
        "permissions": perms, "sha256": hashlib.sha256(data).hexdigest(), "problems": problems,
        "state": state, "installed_version": src.get("version") if src.get("kind") == "pack" else None,
        "expanded": expanded(src.get("permissions"), perms) if state == "upgrade" else [],
    }


def install(data: bytes, *, confirmed: bool = False, origin: str = "file",
            signature: dict[str, Any] | None = None) -> dict[str, Any]:
    """装 / 升级。新装要 confirmed (界面上看过权限摘要点了安装); 升级权限变多了也要重新 confirmed。"""
    info = inspect(data)
    if info["problems"]:
        raise ValueError("这个包没过检查: " + "; ".join(info["problems"]))
    if info["state"] == "conflict":
        raise ValueError(f"本机已经有一个自己搭的专家用了 id {info['id']}, 装不了")
    if info["state"] in ("same", "older"):
        raise ValueError(f"已经装了 {info['name']} {info['installed_version']}, 不用再装这个版本")
    if not confirmed and (info["state"] == "new" or info["expanded"]):
        return {"ok": False, "need_confirm": True, "inspect": info}
    man, files = read_pack(data)
    pid = man["id"]
    root = packs_root()
    root.mkdir(parents=True, exist_ok=True)
    final = root / pack_ns(pid)
    staging = root / f".staging-{pid}-{secrets.token_hex(3)}"
    for name, body in files.items():
        kind, _, rest = name.partition("/")
        if kind in ("skills", "workflows") and rest.count("/") >= 1:
            dest = staging / rest
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(body)
    staging.mkdir(parents=True, exist_ok=True)
    old = root / f".old-{pid}-{secrets.token_hex(3)}"
    had = final.exists()
    if had:
        final.rename(old)
    staging.rename(final)
    defn = {
        "id": pid, "name": man.get("name"), "description": man.get("description"),
        "persona": files.get("SOUL.md", b"").decode("utf-8", errors="ignore"),
        "quick_prompts": man.get("quick_prompts") or [], "workflows": man.get("workflows") or [],
        "skills": man.get("skills") or [], "routes": man.get("routes") or [], "checks": man.get("checks") or [],
        "tool_groups": man.get("tool_groups") or [], "allow_web": man.get("allow_web"),
        "allow_code": man.get("allow_code"), "model": man.get("model") or "inherit",
    }
    try:
        d = expert_local.normalize(defn)
    except ValueError:
        shutil.rmtree(final, ignore_errors=True)
        if had:
            old.rename(final)
        raise
    shutil.rmtree(old, ignore_errors=True)
    prev = _installed(pid) or {}
    d["created_at"] = prev.get("created_at") or workflow_store.now()
    d["updated_at"] = workflow_store.now()
    d["enabled"] = prev.get("enabled", True) is not False
    d["source"] = {"kind": "pack", "version": man["version"], "sha256": info["sha256"], "origin": origin,
                   "signature": signature or {"status": "unsigned"}, "installed_at": workflow_store.now(),
                   "permissions": info["permissions"]}
    p = expert_local._dir(pid) / "expert.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(d, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return {"ok": True, "id": pid, "profile_id": expert_local.profile_id(pid), "version": man["version"],
            "upgraded": info["state"] == "upgrade", "signature": d["source"]["signature"]}


def install_file(path: str, confirmed: bool = False) -> dict[str, Any]:
    p = Path(path).expanduser()
    if p.suffix != EXT:
        raise ValueError(f"选 {EXT} 文件")
    return install(p.read_bytes(), confirmed=confirmed, origin="file")


def inspect_file(path: str) -> dict[str, Any]:
    return inspect(Path(path).expanduser().read_bytes())


def uninstall(pack_id: str) -> dict[str, Any]:
    """删专家定义 + 包里的技能 / 工作流。运行记录 (workflow_runs) 留着。hermes profile 由 Companion 删。"""
    cur = _installed(pack_id)
    if cur is None:
        raise LookupError(f"没有这个专家: {pack_id}")
    expert_local.delete(pack_id)
    shutil.rmtree(packs_root() / pack_ns(pack_id), ignore_errors=True)
    return {"ok": True, "id": pack_id}


# ── 中央 (走 gateway /v1/hub/experts, 带员工自己的登录) ──────────

def _hub(method: str, path: str, **kw: Any):
    import httpx  # noqa: PLC0415
    token = skill_publish._read_id_token()
    if not token:
        raise ValueError("还没登录公司账号 (设置里登录后再用资源市场)")
    try:
        with httpx.Client(timeout=kw.pop("timeout", 30.0)) as c:
            r = c.request(method, f"{skill_publish.GATEWAY_URL}/v1/hub/experts{path}",
                          headers={"Authorization": f"Bearer {token}"}, **kw)
    except httpx.RequestError as e:
        raise ValueError(f"连不上中央: {e}") from None
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = r.text[:200]
        if r.status_code == 403 and not detail:
            detail = "没有权限"
        raise ValueError(f"中央返回 {r.status_code}: {detail}")
    return r


def publish(expert_id: str, scope: str, departments: list[str] | None = None, note: str = "",
            version: str | None = None) -> dict[str, Any]:
    data, info = build(expert_id, version)
    r = _hub("POST", "", files={"pack": (info["filename"], data, "application/gzip")},
             data={"scope": scope, "departments": json.dumps(departments or [], ensure_ascii=False), "note": note})
    meta = r.json()
    _note_packed(expert_id, info["version"], published={"scope": scope, "status": meta.get("status")})
    return {"ok": True, **info, "status": meta.get("status"), "hub": meta}


def market() -> dict[str, Any]:
    items = _hub("GET", "").json().get("experts") or []
    for m in items:
        src = (_installed(m["id"]) or {}).get("source") or {}
        m["installed_version"] = src.get("version") if src.get("kind") == "pack" else None
        m["local_conflict"] = bool(_installed(m["id"])) and src.get("kind") != "pack"
        m["can_upgrade"] = bool(m["installed_version"]) and _ver_tuple(m["version"]) > _ver_tuple(m["installed_version"])
        m["expanded"] = expanded(src.get("permissions"), m.get("summary") or {}) if m["can_upgrade"] else []
    return {"ok": True, "experts": items}


def mine() -> dict[str, Any]:
    return {"ok": True, "experts": _hub("GET", "/mine").json().get("experts") or []}


def withdraw(pack_id: str, version: str) -> dict[str, Any]:
    return {"ok": True, "hub": _hub("POST", f"/{pack_id}/{version}/withdraw").json()}


def install_from_hub(pack_id: str, version: str, confirmed: bool = False) -> dict[str, Any]:
    meta = _hub("GET", f"/{pack_id}/{version}").json()
    if meta.get("status") != "approved":
        raise ValueError("这个版本还没审核通过, 不能装")
    key = pinned_key() or pin_key(_hub("GET", "/signing-key").json()["public_key"])
    data = _hub("GET", f"/{pack_id}/{version}/download", timeout=120.0).content
    sig = verify_signature(meta, data, key)
    return install(data, confirmed=confirmed, origin="hub", signature=sig)


def pim_handlers(params: dict[str, Any]) -> dict[str, Any]:
    eid = str(params.get("id") or "")
    ver = str(params.get("version") or "") or None
    return {
        "pim/expert_pack_preview": lambda: preview(eid, ver),
        "pim/expert_pack_export": lambda: export(eid, ver, params.get("dest")),
        "pim/expert_pack_inspect": lambda: inspect_file(str(params.get("path") or "")),
        "pim/expert_pack_install": lambda: (
            install_from_hub(eid, str(params.get("version") or ""), bool(params.get("confirmed")))
            if params.get("from") == "hub" else install_file(str(params.get("path") or ""), bool(params.get("confirmed")))),
        "pim/expert_pack_uninstall": lambda: uninstall(eid),
        "pim/expert_pack_publish": lambda: publish(eid, str(params.get("scope") or "department"),
                                                   list(params.get("departments") or []), str(params.get("note") or ""), ver),
        "pim/expert_pack_market": market,
        "pim/expert_pack_mine": mine,
        "pim/expert_pack_withdraw": lambda: withdraw(eid, str(params.get("version") or "")),
    }

