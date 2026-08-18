"""按站点找密码 —— 替掉那个"人工搬 secret_ref"的老流程 (8/18)。

# 病历

8/17 鸿波改了 EIS 密码, 在 Companion 里存成
`catfish-teaching:http://eis.ffcs.cn`。而冻结的 eis-login skill 里焊死的是
`keychain://eis_password` (4/28 建的那条)。两条各自都在, 谁也不知道谁 ——
登录报"账号或密码错误", 从 UI 看一切正常。

根因不是哪一步写错了, 是**中间那次人工搬运**: 存密码的流程和教学的流程之间
没有任何机制传递这个字符串, 靠人记住并告诉模型, 然后被 `_infer_params`
焊成 `password_ref` 默认值凝固进 script.py。焊完就再也改不动了。

站点是天然标识 —— 密码本来就是某个网站的密码。改成按 `page.url` 现查以后:

  · 没有东西要搬 (模型不用知道任何 ref)
  · 没有东西可以焊死 (焊进去的是"按站点查", 换了密码仍然对)
  · resolve_secret 没有缓存, 员工改完下一次跑就是新的

# 这个文件钉什么

  1. hostname 提取的边界 —— 特别是"EIS"这种人起的名字**不能**被当成站点
  2. 不许模糊放宽 (不剥 www., 不退注册域) —— 退一步就拿 A 系统密码登 B 系统
  3. 老数据 (只有 label) 不迁移也要能匹配上
  4. browser_fill 的 secret_for_site 分支: 查得到就填, 查不到返 needs_credential
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(_SRC))

from catfish_tool_bridge import credential_sites as cs


@pytest.fixture
def index(tmp_path, monkeypatch):
    """给一个假索引。返回一个"写进去"的函数。"""
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))

    def write(entries):
        (tmp_path / "teaching_credentials.json").write_text(
            json.dumps(entries, ensure_ascii=False), encoding="utf-8"
        )
    return write


# ───────────────────────── host 提取 ─────────────────────────

@pytest.mark.parametrize("raw,want", [
    ("http://eis.ffcs.cn/cas/login?service=x", "eis.ffcs.cn"),
    ("https://EIS.FFCS.CN/",                   "eis.ffcs.cn"),   # 大小写归一
    ("http://eis.ffcs.cn:8080/x",              "eis.ffcs.cn"),   # 端口不算站点的一部分
    ("eis.ffcs.cn",                            "eis.ffcs.cn"),   # 裸 host
    ("EIS",                                    ""),              # ★ 人起的名字, 不是站点
    ("教学登录",                                "" ),             # ★ 同上
    ("公司 OA 系统",                            ""),              # ★ 带空格更不能当 host
    ("",                                       ""),
    ("some/path",                              ""),
])
def test_host_of(raw, want):
    """★★ "EIS" 那几条是重点。

    老索引里 label 是员工自己起的名字, 大部分**不是** URL。要是把它们
    也当 hostname, `known_sites()` 里就会混进 'eis' 'teaching' 这种鬼东西,
    而更糟的是 sites_of 会给这条凭据配上一个不存在的站点 —— 将来某天真有个
    叫 eis 的内网单标签域名, 就直接串号了。
    """
    assert cs.host_of(raw) == want


def test_不剥_www_():
    """★★ www.a.com 和 a.com 是两个 host, 不许当成一个。

    看着像"顺手做掉更方便", 但方向是错的: 剥了以后 UI 上员工看到的是一条,
    实际覆盖两个站点, 而他并没有同意过。要一起用就在 UI 里显式加第二个。
    """
    assert cs.host_of("http://www.ffcs.cn") == "www.ffcs.cn"
    assert cs.host_of("http://ffcs.cn") == "ffcs.cn"
    assert cs.host_of("http://www.ffcs.cn") != cs.host_of("http://ffcs.cn")


def test_不退到注册域(index):
    """★★★ 这条是安全边界, 不是风格问题。

    公司所有内部系统都在 ffcs.cn 底下 (eis / neis / oa / …)。要是查不到
    eis.ffcs.cn 就退一步找 ffcs.cn, 那存了任意一个系统的密码, 剩下全部
    系统都会拿它去登 —— 密码错还算好的, 撞上锁定策略就是账号被锁。
    """
    index([{"label": "http://ffcs.cn", "reference": "keychain://root"}])
    assert cs.ref_for_url("http://eis.ffcs.cn/login") is None
    assert cs.ref_for_url("http://oa.ffcs.cn") is None
    assert cs.ref_for_url("http://ffcs.cn") == "keychain://root"


# ───────────────────────── 索引查找 ─────────────────────────

def test_老数据不迁移也能匹配(index):
    """8/17 存的那条 label 正好是 URL, 所以不用迁移就能立刻用上。"""
    index([{"label": "http://eis.ffcs.cn",
            "reference": "keychain://catfish-teaching:http://eis.ffcs.cn",
            "createdAt": "2026-08-17T09:37:44Z"}])
    assert cs.ref_for_url("http://eis.ffcs.cn/cas/login") == \
        "keychain://catfish-teaching:http://eis.ffcs.cn"


def test_老数据里非URL的label不参与匹配(index):
    """label='EIS' 这种只能靠员工去 UI 里补 sites, 猜不得。"""
    index([{"label": "EIS", "reference": "keychain://eis_password"}])
    assert cs.known_sites() == []
    assert cs.ref_for_url("http://eis.ffcs.cn") is None


def test_新数据一条覆盖多入口(index):
    """★ 多入口就是这么处理的 —— 显式列, 不猜。"""
    index([{"label": "EIS", "reference": "keychain://eis",
            "sites": ["eis.ffcs.cn", "neis.ffcs.cn"]}])
    assert cs.ref_for_url("http://eis.ffcs.cn/a") == "keychain://eis"
    assert cs.ref_for_url("http://neis.ffcs.cn/b") == "keychain://eis"
    assert cs.ref_for_url("http://oa.ffcs.cn") is None


def test_sites_优先于_label(index):
    """给了 sites 就完全按 sites 走, 别再拿 label 兜底又多匹配一个站点。"""
    index([{"label": "http://old.ffcs.cn", "reference": "keychain://x",
            "sites": ["new.ffcs.cn"]}])
    assert cs.ref_for_url("http://new.ffcs.cn") == "keychain://x"
    assert cs.ref_for_url("http://old.ffcs.cn") is None


def test_同站点多条取最新(index):
    """员工改密码时多半是新存一条, 老的没删。取新的那条。"""
    index([
        {"label": "旧", "reference": "keychain://old", "sites": ["eis.ffcs.cn"],
         "createdAt": "2026-04-28T00:00:00Z"},
        {"label": "新", "reference": "keychain://new", "sites": ["eis.ffcs.cn"],
         "createdAt": "2026-08-17T09:37:44Z"},
    ])
    assert cs.ref_for_url("http://eis.ffcs.cn") == "keychain://new"


def test_空reference的条目不算命中(index):
    """索引里可能有半截数据 —— 有 label 没 reference, 别返个空串上去。"""
    index([{"label": "http://eis.ffcs.cn", "reference": "  "},
           {"label": "x", "reference": "keychain://ok", "sites": ["eis.ffcs.cn"]}])
    assert cs.ref_for_url("http://eis.ffcs.cn") == "keychain://ok"


@pytest.mark.parametrize("bad", ["", "   ", "{不是 json", '{"不是":"数组"}'])
def test_索引坏了当空不炸(index, tmp_path, bad):
    """★ 查不到只意味着"要存一次", 不该让整个 fill 挂掉。"""
    (tmp_path / "teaching_credentials.json").write_text(bad, encoding="utf-8")
    assert cs.ref_for_url("http://eis.ffcs.cn") is None
    assert cs.known_sites() == []


def test_索引不存在也不炸(tmp_path, monkeypatch):
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path / "根本没这个目录"))
    assert cs.ref_for_url("http://eis.ffcs.cn") is None


# ─────────────────── browser_fill 的 secret_for_site 分支 ───────────────────

@pytest.fixture
def fake_browser(monkeypatch):
    """假的 playwright, 让我们能进到 page 之后那段。

    真 Chrome 在沙箱里连不上, 而 secret_for_site 的整段逻辑都在
    `_connect_playwright_browser` **之后** (要 page.url 才知道站点) ——
    不假一个的话这段一行都跑不到。
    """
    from catfish_tool_bridge import catfish_tools_browser as ctb

    class FakePage:
        def __init__(self, url):
            self.url = url
            self.filled = []

        def title(self):
            return "统一身份认证"

        def fill(self, selector, text, timeout=None):
            self.filled.append((selector, text))

    state = {}

    class FakeCtx:
        def __enter__(self):
            return object()

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(ctb, "_import_playwright", lambda: FakeCtx)
    monkeypatch.setattr(
        ctb, "_connect_playwright_browser",
        lambda p: (None, None, state["page"]),
    )

    def go(url):
        state["page"] = FakePage(url)
        return state["page"]
    go.state = state
    return go


def _fill(args):
    from catfish_tool_bridge.catfish_tools_browser_actions import _browser_fill_impl
    return _browser_fill_impl(args)


def test_没给任何值时的报错要提到_secret_for_site():
    """★ 报错里得说得出新写法, 否则模型只会退回去传 secret_ref。"""
    got = _fill({"selector": "#p"})
    assert got["type"] == "error"
    assert "secret_for_site" in got["error"]


def test_查不到就返_needs_credential(index, fake_browser):
    """★★★ 这就是整个重设计的入口。

    前端认 needs_credential 这个标记, 在 tool call 底下嵌一个密码框 ——
    所以它必须带齐"存哪个站点"要用的东西: site / page_url / page_title。
    """
    index([])
    fake_browser("http://neis.ffcs.cn/cas/login?service=x")
    got = _fill({"selector": "#password", "secret_for_site": True})

    assert got["type"] == "error", "不能返 ok —— 模型会以为填成功了往下走"
    assert got["needs_credential"] is True
    assert got["site"] == "neis.ffcs.cn"
    assert got["page_url"].startswith("http://neis.ffcs.cn")
    assert got["page_title"] == "统一身份认证"
    assert got["selector"] == "#password", "重试要用同一个 selector"


def test_查不到时把已存的站点列出来(index, fake_browser):
    """员工看到"eis.ffcs.cn 存过, neis.ffcs.cn 没存"才知道是多入口的事。"""
    index([{"label": "http://eis.ffcs.cn", "reference": "keychain://a"}])
    fake_browser("http://neis.ffcs.cn/")
    got = _fill({"selector": "#password", "secret_for_site": True})
    assert got["known_sites"] == ["eis.ffcs.cn"]
    assert "eis.ffcs.cn" in got["summary"]


def test_查得到就填进去(index, fake_browser, monkeypatch):
    """★★ 走通的那条。密码要真的到 page.fill, 而不是把 ref 当字面量填进去。"""
    index([{"label": "http://eis.ffcs.cn", "reference": "keychain://eis_pw"}])
    page = fake_browser("http://eis.ffcs.cn/cas/login")

    from catfish_tool_bridge import secret_resolver
    monkeypatch.setattr(secret_resolver, "resolve_secret",
                        lambda ref: "真密码" if ref == "keychain://eis_pw" else pytest.fail(ref))

    got = _fill({"selector": "#password", "secret_for_site": True})
    assert got["type"] == "ok"
    assert page.filled == [("#password", "真密码")]


def test_填成功后不回_ref_只回站点(index, fake_browser, monkeypatch):
    """★★★ 故意的, 不是漏了。

    回了 ref, 模型下一轮就学会直接传 secret_ref='keychain://…' —— 那就退回
    老路: 一个人工搬运的字符串, 被 _infer_params 焊进 script.py, 员工改密码
    就失联。这正是 8/17 那个 bug。回站点则怎么焊都还是对的。
    """
    index([{"label": "http://eis.ffcs.cn", "reference": "keychain://eis_pw"}])
    fake_browser("http://eis.ffcs.cn/cas/login")
    from catfish_tool_bridge import secret_resolver
    monkeypatch.setattr(secret_resolver, "resolve_secret", lambda ref: "真密码")

    got = _fill({"selector": "#password", "secret_for_site": True})
    blob = json.dumps(got, ensure_ascii=False)
    assert "keychain://" not in blob, f"ref 泄进结果了: {blob}"
    assert "真密码" not in blob, "密码泄进结果了"
    assert got["site_used"] == "eis.ffcs.cn"
    assert got["security_audit"] == "credential_via_site"


def test_索引有但钥匙串取不出来也要返_needs_credential(index, fake_browser, monkeypatch):
    """★★ 索引和钥匙串是两份数据, 会不同步 (员工手删过 keychain 条目)。

    这时候是"重新存一次"能解决的, 跟"没存过"是同一类, 所以走同一个前端入口。
    要是只返一句 error, 员工看到的就是一条没有出路的报错。
    """
    index([{"label": "http://eis.ffcs.cn", "reference": "keychain://没了"}])
    fake_browser("http://eis.ffcs.cn/")
    from catfish_tool_bridge import secret_resolver

    def boom(ref):
        raise secret_resolver.SecretResolveError("keychain 里找不到 '没了'")
    monkeypatch.setattr(secret_resolver, "resolve_secret", boom)

    got = _fill({"selector": "#password", "secret_for_site": True})
    assert got["needs_credential"] is True
    assert got["site"] == "eis.ffcs.cn"


def test_secret_ref_优先于_secret_for_site(index, fake_browser, monkeypatch):
    """★★ 三个已冻结的 EIS skill 全走 secret_ref, 不能被新路抢掉。

    显式写死的意图更强 —— 而且冻结的 script.py 不会同时传这两个,
    真同时出现只可能是人手写的, 那就按他写死的那个来。
    """
    index([{"label": "http://eis.ffcs.cn", "reference": "keychain://按站点"}])
    page = fake_browser("http://eis.ffcs.cn/")
    from catfish_tool_bridge import secret_resolver
    monkeypatch.setattr(secret_resolver, "resolve_secret",
                        lambda ref: {"keychain://显式": "显式的",
                                     "keychain://按站点": "按站点的"}[ref])

    got = _fill({"selector": "#password", "secret_for_site": True,
                 "secret_ref": "keychain://显式"})
    assert page.filled == [("#password", "显式的")]
    assert got["security_audit"] == "credential_via_secret_ref"


def test_页面没有url时明确报错(index, fake_browser):
    """about:blank / 空 url —— 说清楚是"取不到站点", 别让员工去查密码存没存。"""
    index([{"label": "http://eis.ffcs.cn", "reference": "keychain://a"}])
    fake_browser("about:blank")
    got = _fill({"selector": "#password", "secret_for_site": True})
    assert got["type"] == "error"
    assert "站点" in got["error"]
    assert not got.get("needs_credential"), "这不是'去存密码'能解决的"
