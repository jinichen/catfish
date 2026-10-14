"""员工接入码 (10/14): certgen.py 算的跟 Companion (server_pairing.rs) 必须一模一样。

两边各有一份实现 (Python 打印给管理员, Rust 核对员工输入的), 用同一张测试证书锁住同一个值。
certgen.py 依赖 cryptography, 这个包的测试环境正好有, 所以放这里。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from cryptography import x509

REPO = Path(__file__).resolve().parents[3]


def _certgen():
    spec = importlib.util.spec_from_file_location(
        "certgen", REPO / "delivery/catfish-poc/tools/certgen.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_same_code_as_companion():
    pem = (REPO / "edge/companion-app/src-tauri/src/util/test_root_ca.txt").read_bytes()
    code = _certgen().pairing_code(x509.load_pem_x509_certificate(pem))
    assert code == "0404-8028-BF1F-2864"   # server_pairing.rs 里同一个值


def test_written_next_to_the_ca(tmp_path, monkeypatch):
    certgen = _certgen()
    monkeypatch.setattr("sys.argv", ["certgen.py", "--ip", "10.0.0.5", "--out", str(tmp_path)])
    assert certgen.main() == 0
    code = (tmp_path / "pairing-code.txt").read_text().strip()
    ca = x509.load_pem_x509_certificate((tmp_path / "ca.pem").read_bytes())
    assert code == certgen.pairing_code(ca)
    assert len(code) == 19 and code.count("-") == 3
