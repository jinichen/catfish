"""连中央的 https: 默认信任库 + 公司内部 CA (11/08, central_tls.py)。"""
from __future__ import annotations

import datetime as dt
import http.server
import ssl
import threading
from pathlib import Path

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from catfish_tool_bridge import central_tls

REPO_EDGE = Path(__file__).resolve().parents[2]


def _ca_and_server(tmp: Path) -> tuple[Path, Path, Path]:
    now = dt.datetime.now(dt.timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Catfish Internal CA")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=30))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                       content_commitment=False, key_encipherment=False,
                                       data_encipherment=False, key_agreement=False,
                                       encipher_only=False, decipher_only=False), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .sign(ca_key, hashes.SHA256()))
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    srv = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
           .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
           .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=30))
           .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
           .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
           .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
           .sign(ca_key, hashes.SHA256()))
    pem = serialization.Encoding.PEM
    (tmp / "ca.pem").write_bytes(ca.public_bytes(pem))
    (tmp / "srv.pem").write_bytes(srv.public_bytes(pem))
    (tmp / "srv.key").write_bytes(key.private_bytes(pem, serialization.PrivateFormat.PKCS8,
                                                    serialization.NoEncryption()))
    return tmp / "ca.pem", tmp / "srv.pem", tmp / "srv.key"


@pytest.fixture
def https_server(tmp_path):
    ca, crt, key = _ca_and_server(tmp_path)

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *a):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), H)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(crt), str(key))
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"https://localhost:{httpd.server_address[1]}/", ca
    httpd.shutdown()


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")  # 本机测试服务器别被开发机的代理截走
    monkeypatch.setenv("no_proxy", "localhost,127.0.0.1")


def test_no_server_ca_changes_nothing(clean_env):
    assert central_tls.ensure_central_ca_env() is None
    import os
    assert "SSL_CERT_FILE" not in os.environ


def test_bundle_is_default_roots_plus_internal_ca(clean_env, https_server):
    url, ca = https_server
    with pytest.raises(httpx.ConnectError):  # 默认信任库不认内部 CA —— 以前 https 网关就是这么挂的
        httpx.get(url)
    dest = central_tls.server_ca_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(ca.read_bytes())
    bundle = central_tls.ensure_central_ca_env()
    assert bundle and Path(bundle).parent == dest.parent
    text = Path(bundle).read_text()
    import certifi
    first_root = Path(certifi.where()).read_text().split("-----END CERTIFICATE-----")[0]
    assert first_root.strip() and first_root.strip().splitlines()[-1] in text  # 默认根证书都还在
    assert ca.read_text().strip() in text                                    # 内部 CA 加在上面
    assert httpx.get(url).text == "ok"                                       # 环境变量生效
    with httpx.Client(verify=central_tls.central_ssl_context()) as c:        # 显式 context 也行
        assert c.get(url).text == "ok"
    # 重跑: 内容不变就不重写
    mtime = Path(bundle).stat().st_mtime_ns
    assert central_tls.ensure_central_ca_env() == bundle
    assert Path(bundle).stat().st_mtime_ns == mtime


def test_does_not_override_it_managed_bundle(monkeypatch, tmp_path, https_server):
    _, ca = https_server
    dest = central_tls.server_ca_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(ca.read_bytes())
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "corp.pem"))
    assert central_tls.ensure_central_ca_env() is None
    import os
    assert os.environ["SSL_CERT_FILE"] == str(tmp_path / "corp.pem")


def test_refuses_private_key(clean_env):
    dest = central_tls.server_ca_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n")
    assert central_tls.ensure_central_ca_env() is None


def test_hermes_plugin_copy_is_identical():
    a = REPO_EDGE / "tool-bridge/src/catfish_tool_bridge/central_tls.py"
    b = REPO_EDGE / "hermes-plugins/catfish-xcatfish-user/central_tls.py"
    assert a.read_text() == b.read_text(), "两份 central_tls.py 要一字不差 (改一处就 cp 到另一处)"
