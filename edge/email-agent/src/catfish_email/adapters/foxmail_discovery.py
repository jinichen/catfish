"""Windows Foxmail Storage 自动发现。

Foxmail 没有稳定公开的路径 API，实际部署可能把 Storage 放在默认目录、
注册表记录的目录，或参数文件引用的外置盘。这个模块只做路径发现：

* 显式 ``CATFISH_FOXMAIL_ROOT`` 最高优先级；
* Windows 注册表只读取 Foxmail 相关分支的路径型值；
* 只在注册表指向的目录和默认用户目录内读取小型配置文件；
* 每个候选必须含 Foxmail ``.box``/``.eml`` 数据才算有效。

不会扫描整个磁盘，也不会读取或解析账号密码、邮件正文。
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Iterable

logger = logging.getLogger("catfish_email.adapters.foxmail_discovery")

ROOT_ENV = "CATFISH_FOXMAIL_ROOT"
MAX_CONFIG_BYTES = 1024 * 1024
MAX_CONFIG_DEPTH = 3
MAX_PATH_LENGTH = 32767

_PATH_RE = re.compile(
    r"(?P<path>(?:[A-Za-z]:[\\/]|\\\\)[^\"'<>|\r\n]{2,32760})"
)
_PATH_VALUE_NAMES = re.compile(
    r"(?:path|dir|directory|folder|storage|profile|profiles|data|home|mail)",
    re.IGNORECASE,
)
_SECRET_VALUE_NAMES = re.compile(
    r"(?:password|passwd|token|secret|credential|apikey|api_key)",
    re.IGNORECASE,
)
_CONFIG_SUFFIXES = {".ini", ".cfg", ".conf", ".json", ".xml"}
_CONFIG_NAMES = {
    "foxmail.ini",
    "foxmail.cfg",
    "foxmail.json",
    "config.ini",
    "config.json",
    "settings.ini",
    "profile.ini",
    "profiles.ini",
    "accounts.ini",
    "global.ini",
}
_REGISTRY_SUBKEYS = (
    r"Software\Tencent\Foxmail",
    r"Software\Tencent\Foxmail7",
    r"Software\Foxmail",
    r"Software\WOW6432Node\Tencent\Foxmail",
    r"Software\WOW6432Node\Tencent\Foxmail7",
)


def discover_storage_roots() -> list[Path]:
    """返回通过安全校验的 Foxmail Storage 候选，按可信度排序。

    显式环境变量是唯一的强制来源。它一旦设置但无效，就不继续猜其它
    目录，避免把错误账号悄悄读出来。没有显式配置时才走注册表/参数文件
    和默认目录。
    """
    explicit = os.environ.get(ROOT_ENV, "").strip()
    if explicit:
        candidate = _normalise_path(explicit)
        return [candidate] if candidate is not None and _looks_like_storage(candidate) else []

    roots: list[Path] = []
    hint = _normalise_path(os.environ.get("CATFISH_FOXMAIL_HINT", "").strip())
    if hint is not None:
        roots.append(hint)
    roots.extend(_registry_and_config_paths())
    roots.extend(_default_paths())
    return _unique_valid_paths(roots)


def discover_storage_path() -> Path | None:
    """返回首个有效 Storage；没有有效候选时返回 ``None``。"""
    roots = discover_storage_roots()
    if roots:
        logger.info("自动发现 Foxmail Storage: %s", roots[0])
    return roots[0] if roots else None


def _default_paths() -> Iterable[Path]:
    for base_name in ("LOCALAPPDATA", "APPDATA"):
        base_value = os.environ.get(base_name, "").strip()
        if not base_value:
            continue
        base = Path(base_value)
        for relative in (
            "Tencent/Foxmail7/Storage",
            "Foxmail7/Storage",
            "Tencent/Foxmail/Storage",
            "Foxmail/Storage",
        ):
            yield base / relative


def _registry_and_config_paths() -> Iterable[Path]:
    """从 Foxmail 注册表分支和附近小型配置文件提取路径。"""
    registry_paths = list(_registry_paths())
    for path in registry_paths:
        yield from _path_variants(path)
        if path.is_dir():
            yield from _paths_from_config_files(path)
        elif path.is_file():
            yield from _paths_from_config_file(path)

    # 注册表有时只记录安装目录，默认用户目录里的参数文件再记录 Storage。
    for base in _default_config_bases():
        yield from _paths_from_config_files(base)

    # 某些 Foxmail 版本把 StoragePath 写在安装目录的参数文件里，而注册表
    # 只记录 InstallPath。补充 Windows 常见安装根目录，但仍限制为 Foxmail
    # 命名目录和小型配置文件，绝不遍历整个磁盘。
    for base in _windows_install_bases():
        yield from _path_variants(base)
        yield from _paths_from_config_files(base)


def _registry_paths() -> Iterable[Path]:
    if os.name != "nt":
        return
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        return

    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            for subkey in _REGISTRY_SUBKEYS:
                try:
                    with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ | view) as key:
                        yield from _walk_registry(key, winreg, depth=0)
                except OSError:
                    continue
            # App Paths stores the executable as its unnamed default value.
            # Do not parse arbitrary shell commands or read unrelated registry trees.
            try:
                with winreg.OpenKey(hive, r"Software\Microsoft\Windows\CurrentVersion\App Paths\Foxmail.exe",
                                    0, winreg.KEY_READ | view) as key:
                    value, _ = winreg.QueryValueEx(key, "")
                    path = _normalise_path(os.path.expandvars(value)) if isinstance(value, str) else None
                    if path is not None and path.name.casefold() == "foxmail.exe":
                        yield path.parent
            except OSError:
                continue


def _walk_registry(key, winreg, *, depth: int) -> Iterable[Path]:
    if depth > 3:
        return
    try:
        subkey_count, value_count, _ = winreg.QueryInfoKey(key)
    except OSError:
        return

    for index in range(value_count):
        try:
            name, value, value_type = winreg.EnumValue(key, index)
        except OSError:
            continue
        if value_type not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
            continue
        if not isinstance(value, str) or not _is_path_value_name(name):
            continue
        path = _normalise_path(os.path.expandvars(value))
        if path is not None:
            yield path

    for index in range(subkey_count):
        try:
            child_name = winreg.EnumKey(key, index)
            with winreg.OpenKey(key, child_name) as child:
                yield from _walk_registry(child, winreg, depth=depth + 1)
        except OSError:
            continue


def _default_config_bases() -> Iterable[Path]:
    for base_name in ("LOCALAPPDATA", "APPDATA"):
        value = os.environ.get(base_name, "").strip()
        if value:
            base = Path(value)
            for relative in (
                "Tencent/Foxmail7",
                "Foxmail7",
                "Tencent/Foxmail",
                "Foxmail",
            ):
                yield base / relative


def _windows_install_bases() -> Iterable[Path]:
    """返回可能包含 Foxmail 参数文件的安装目录。"""
    for env_name in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        value = os.environ.get(env_name, "").strip()
        if not value:
            continue
        base = Path(value)
        for relative in (
            "Tencent/Foxmail",
            "Tencent/Foxmail7",
            "Foxmail",
            "Foxmail7",
        ):
            yield base / relative


def _paths_from_config_files(base: Path) -> Iterable[Path]:
    if not base.is_dir():
        return
    try:
        for directory, dirs, names in os.walk(base):
            relative = Path(directory).relative_to(base)
            if len(relative.parts) >= MAX_CONFIG_DEPTH:
                dirs[:] = []
            for name in names:
                path = Path(directory) / name
                if not _is_config_file(path):
                    continue
                yield from _paths_from_config_file(path)
    except OSError:
        return


def _is_config_file(path: Path) -> bool:
    return path.name.casefold() in _CONFIG_NAMES or path.suffix.casefold() in _CONFIG_SUFFIXES


def _paths_from_config_file(path: Path) -> Iterable[Path]:
    try:
        if not path.is_file() or path.stat().st_size > MAX_CONFIG_BYTES:
            return
        raw = path.read_bytes()
    except OSError:
        return
    text = raw.decode("utf-8-sig", errors="ignore")
    if "\x00" in text:
        text = raw.decode("utf-16", errors="ignore")
    for line in text.splitlines():
        # 只丢弃“键名明确是秘密”的行；StoragePath 旁边出现 password
        # 文字不应影响 StoragePath 本身的提取。
        key = line.split("=", 1)[0].split(":", 1)[0].strip(' \\t"{')
        if _SECRET_VALUE_NAMES.search(key):
            continue
        for match in _PATH_RE.finditer(line):
            candidate = _normalise_path(match.group("path"))
            if candidate is not None:
                yield candidate


def _path_variants(path: Path) -> Iterable[Path]:
    yield path
    for suffix in ("Storage", "Data", "Profile", "Profiles", "Mail"):
        yield path / suffix
    if path.is_file():
        yield path.parent


def _is_path_value_name(name: str) -> bool:
    return bool(_PATH_VALUE_NAMES.search(name)) and not _SECRET_VALUE_NAMES.search(name)


def _normalise_path(raw: str) -> Path | None:
    value = raw.strip().strip('"').strip("'")
    if not value or len(value) > MAX_PATH_LENGTH:
        return None
    leading_slashes = len(value) - len(value.lstrip("\\"))
    if leading_slashes:
        # UNC paths must retain exactly two leading separators; only unescape
        # separators after the server/share prefix.
        value = "\\\\" + value[leading_slashes:]
        value = value[:2] + value[2:].replace("\\\\", "\\")
    else:
        value = value.replace("\\\\", "\\")
    if value.startswith("file://") or "://" in value:
        return None
    # Windows paths copied from JSON/INI may end with a separator or contain
    # a trailing quote-like delimiter captured by the permissive regex.
    value = value.rstrip(" ,;)\t")
    return Path(value)


def _looks_like_storage(path: Path) -> bool:
    try:
        return path.is_dir() and _has_mail_files(path)
    except OSError:
        return False


def _has_mail_files(root: Path) -> bool:
    for directory, dirs, names in os.walk(root):
        try:
            depth = len(Path(directory).relative_to(root).parts)
        except ValueError:
            continue
        if depth >= 6:
            dirs[:] = []
        if any(Path(name).suffix.casefold() in {".box", ".eml"} for name in names):
            return True
    return False


def _unique_valid_paths(paths: Iterable[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        try:
            resolved = path.expanduser().resolve()
        except OSError:
            continue
        key = os.path.normcase(str(resolved))
        if key in seen or not _looks_like_storage(resolved):
            continue
        seen.add(key)
        result.append(resolved)
    return result
