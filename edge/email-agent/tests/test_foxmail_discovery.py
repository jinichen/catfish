"""Foxmail Windows Storage 自动发现测试。"""

from __future__ import annotations

from pathlib import Path
from contextlib import nullcontext

import catfish_email.adapters.foxmail_discovery as discovery


def test_registry_enumerates_values_when_there_are_no_subkeys():
    class Registry:
        REG_SZ, REG_EXPAND_SZ = 1, 2

        def QueryInfoKey(self, key):
            return (0, 2, 0)

        def EnumValue(self, key, index):
            return [("InstallPath", "D:/Foxmail", 1), ("StoragePath", "E:/Mail", 1)][index]

        def EnumKey(self, key, index):
            raise AssertionError("no subkeys")

    assert list(discovery._walk_registry("root", Registry(), depth=0)) == [
        Path("D:/Foxmail"), Path("E:/Mail"),
    ]


def test_registry_walks_subkeys_even_when_parent_has_no_values():
    class Registry:
        REG_SZ, REG_EXPAND_SZ = 1, 2

        def QueryInfoKey(self, key):
            return (1, 0, 0) if key == "root" else (0, 1, 0)

        def EnumKey(self, key, index):
            return "Profile"

        def OpenKey(self, key, child):
            return nullcontext(child)

        def EnumValue(self, key, index):
            assert key == "Profile"
            return "StoragePath", "E:/Mail", 1

    assert list(discovery._walk_registry("root", Registry(), depth=0)) == [Path("E:/Mail")]


def test_default_install_directory_can_be_storage(monkeypatch, tmp_path):
    storage = _storage(tmp_path)
    monkeypatch.delenv(discovery.ROOT_ENV, raising=False)
    monkeypatch.setattr(discovery, "_registry_paths", lambda: iter([]))
    monkeypatch.setattr(discovery, "_windows_install_bases", lambda: iter([storage]))
    assert storage.resolve() in discovery.discover_storage_roots()


def _storage(tmp_path: Path) -> Path:
    root = tmp_path / "Foxmail" / "Storage" / "hongbo@example.com" / "Mail"
    root.mkdir(parents=True)
    (root / "Inbox.eml").write_text("Subject: hello\n\nbody", encoding="utf-8")
    return root.parents[2]


def test_explicit_root_is_authoritative_and_skips_other_sources(monkeypatch, tmp_path):
    storage = _storage(tmp_path)
    monkeypatch.setenv("CATFISH_FOXMAIL_ROOT", str(storage))
    monkeypatch.setattr(
        discovery,
        "_registry_paths",
        lambda: (_ for _ in ()).throw(AssertionError("不应读取注册表")),
    )

    assert discovery.discover_storage_roots() == [storage.resolve()]


def test_registry_candidate_is_accepted_only_when_mail_data_exists(monkeypatch, tmp_path):
    storage = _storage(tmp_path)
    monkeypatch.delenv("CATFISH_FOXMAIL_ROOT", raising=False)
    monkeypatch.setattr(discovery, "_registry_paths", lambda: iter([storage]))

    assert discovery.discover_storage_path() == storage.resolve()


def test_config_file_extracts_windows_path_without_treating_secrets_as_paths(tmp_path):
    config = tmp_path / "Foxmail.ini"
    config.write_text(
        "StoragePath=E:\\\\nextcloud\\\\mailstore\\\\ffchenhb@chinatelecom.cn\n"
        "Password=C:\\\\Users\\\\secret\\\\mail.pwd\n"
        "Homepage=https://example.com/a\\n",
        encoding="utf-8",
    )

    paths = list(discovery._paths_from_config_file(config))

    assert Path("E:\\nextcloud\\mailstore\\ffchenhb@chinatelecom.cn") in paths
    assert Path("C:\\Users\\secret\\mail.pwd") not in paths


def test_registry_secret_value_names_are_not_path_settings():
    assert discovery._is_path_value_name("StoragePath")
    assert not discovery._is_path_value_name("PasswordPath")
    assert not discovery._is_path_value_name("api_token")


def test_unc_path_keeps_the_network_share_prefix():
    assert str(discovery._normalise_path("\\\\server\\share\\Foxmail")) == (
        "\\\\server\\share\\Foxmail"
    )


def test_default_config_scan_is_limited_to_foxmail_directories(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    bases = list(discovery._default_config_bases())

    assert bases == [
        tmp_path / "Tencent/Foxmail7",
        tmp_path / "Foxmail7",
        tmp_path / "Tencent/Foxmail",
        tmp_path / "Foxmail",
    ]


def test_install_config_bases_cover_external_storage_setup(monkeypatch, tmp_path):
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    bases = list(discovery._windows_install_bases())

    assert bases == [
        tmp_path / "Tencent/Foxmail",
        tmp_path / "Tencent/Foxmail7",
        tmp_path / "Foxmail",
        tmp_path / "Foxmail7",
    ]
