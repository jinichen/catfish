"""skill_publish 单测 — 3 道扫描 (5/21 方案 1).

测覆盖:
- (a) BL-D2 凭据扫描 (5/10 ship) — 拒 password / api_key / sk-* / RSA key
- (b) PII 扫描 (5/21 加) — 拒身份证 / 手机号 / 工号 / 银行卡号
- (c) 内网 URL 扫描 (5/21 加) — 拒 10.* / 192.168.* / 172.16-31.* / *.corp.* / eis-*

每道扫描走的是 _scan_sensitive / _scan_pii / _scan_intranet 内部函数,
直接喂 file_map dict 测, 不跑 httpx upload (避免 mock OAuth + gateway).
"""
from __future__ import annotations

from catfish_tool_bridge import skill_publish


# ─── (a) 凭据扫描 ────────────────────────────────────────────────────

def test_credential_scan_password_literal():
    fm = {"SKILL.md": b"password: Hunter2!@#"}
    hits = skill_publish._scan_sensitive(fm)
    assert hits
    assert "SKILL.md" in hits[0]


def test_credential_scan_api_key():
    fm = {"script.py": b'API_KEY = "abcd1234efgh5678ijkl9012"'}
    hits = skill_publish._scan_sensitive(fm)
    assert hits


def test_credential_scan_openai_sk():
    fm = {"config.yaml": b"openai_key: sk-abc1234567890XYZ12345"}
    hits = skill_publish._scan_sensitive(fm)
    assert hits


def test_credential_scan_rsa_private_key():
    fm = {"key.pem": b"-----BEGIN RSA PRIVATE KEY-----\nABC123==\n-----END RSA PRIVATE KEY-----"}
    hits = skill_publish._scan_sensitive(fm)
    assert hits


def test_credential_scan_clean_passes():
    fm = {"SKILL.md": "# eis-login\n\n用 keychain://eis_password 读密码".encode()}
    hits = skill_publish._scan_sensitive(fm)
    assert not hits


# ─── (b) PII 扫描 (5/21 加) ─────────────────────────────────────────

def test_pii_scan_id_card():
    # 18 位身份证 (北京 1101 + 1990年生 + 后 4 位)
    fm = {"SKILL.md": "员工: 110101199001011234".encode()}
    hits = skill_publish._scan_pii(fm)
    assert hits
    assert "身份证" in hits[0]


def test_pii_scan_mobile_number():
    fm = {"script.py": b"phone = '13812345678'"}
    hits = skill_publish._scan_pii(fm)
    assert hits


def test_pii_scan_employee_id():
    fm = {"SKILL.md": b"employee_id: 12345678"}
    hits = skill_publish._scan_pii(fm)
    assert hits


def test_pii_scan_employee_id_chinese():
    fm = {"SKILL.md": "工号: 87654321".encode()}
    hits = skill_publish._scan_pii(fm)
    assert hits


def test_pii_scan_clean_passes():
    # 占位符 / 参数 不撞
    fm = {"SKILL.md": b"employee_id: {{employee_id}} (params)"}
    hits = skill_publish._scan_pii(fm)
    assert not hits


# ─── (c) 内网 URL 扫描 (5/21 加) ────────────────────────────────────

def test_intranet_scan_10_net():
    fm = {"script.py": b"url = 'http://10.10.40.102/eis/login'"}
    hits = skill_publish._scan_intranet(fm)
    assert hits
    assert "10.10.40.102" in hits[0]


def test_intranet_scan_192_168():
    fm = {"script.py": b"host = '192.168.1.100'"}
    hits = skill_publish._scan_intranet(fm)
    assert hits


def test_intranet_scan_172_private():
    fm = {"config.yaml": b"db_host: 172.16.0.5"}
    hits = skill_publish._scan_intranet(fm)
    assert hits


def test_intranet_scan_corp_domain():
    fm = {"SKILL.md": "接入: https://oa.corp.example/login".encode()}
    hits = skill_publish._scan_intranet(fm)
    assert hits


def test_intranet_scan_eis_hostname():
    fm = {"SKILL.md": "target: eis.ffcs.cn".encode()}
    hits = skill_publish._scan_intranet(fm)
    assert hits


def test_intranet_scan_clean_passes():
    # 公网 example / 环境变量 不撞
    fm = {"SKILL.md": b"target: {{INTRANET_OA_URL}} or https://example.com"}
    hits = skill_publish._scan_intranet(fm)
    assert not hits


# ─── 3 道串联 — skill_publish 端到端 (跳 httpx) ─────────────────────

def test_publish_rejects_pii_before_upload(monkeypatch, tmp_path):
    """skill_publish 端到端: PII 在凭据扫描后跑, 命中拒上传不到 httpx 层."""
    # 不动 httpx — 命中扫描 publish 应该返 ok=False 前就 return
    skill_dir = tmp_path / "skills" / "personal" / "leaky"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: leaky\n---\n\n身份证: 110101199001011234\n",
        encoding="utf-8",
    )
    (skill_dir / "script.py").write_text("# noop\n", encoding="utf-8")
    # 假 OAuth 文件让 token 检查过 (扫描在 token 前跑)
    fake_token = tmp_path / "id_token"
    fake_token.write_text("fake-token", encoding="utf-8")
    monkeypatch.setattr(skill_publish, "OAUTH_ID_TOKEN_PATH", fake_token)

    result = skill_publish.skill_publish({
        "skill_path": str(skill_dir),
        "namespace": "personal",
    })
    assert result["ok"] is False
    assert result.get("scan_phase") == "pii"
    assert "PII" in result["error"]


def test_publish_rejects_intranet_url(monkeypatch, tmp_path):
    """内网 URL 撞 → scan_phase='intranet'."""
    skill_dir = tmp_path / "skills" / "personal" / "intra"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: intra\n---\n\nendpoint: http://10.10.40.102/api\n",
        encoding="utf-8",
    )
    fake_token = tmp_path / "id_token"
    fake_token.write_text("fake-token", encoding="utf-8")
    monkeypatch.setattr(skill_publish, "OAUTH_ID_TOKEN_PATH", fake_token)

    result = skill_publish.skill_publish({
        "skill_path": str(skill_dir),
        "namespace": "personal",
    })
    assert result["ok"] is False
    assert result.get("scan_phase") == "intranet"


def test_publish_rejects_credentials_first(monkeypatch, tmp_path):
    """3 道扫描串联顺序: credentials → pii → intranet. 凭据撞先于 PII 报."""
    skill_dir = tmp_path / "skills" / "personal" / "double"
    skill_dir.mkdir(parents=True)
    # 同时含凭据 + PII, 期待 credentials 先报
    (skill_dir / "SKILL.md").write_text(
        "---\nname: double\n---\n\napi_key: abcd1234efgh5678ijkl9012\n"
        "employee_id: 12345678\n",
        encoding="utf-8",
    )
    fake_token = tmp_path / "id_token"
    fake_token.write_text("fake-token", encoding="utf-8")
    monkeypatch.setattr(skill_publish, "OAUTH_ID_TOKEN_PATH", fake_token)

    result = skill_publish.skill_publish({
        "skill_path": str(skill_dir),
        "namespace": "personal",
    })
    assert result["ok"] is False
    assert result.get("scan_phase") == "credentials"
