"""经员工授权的微信聊天导出文件只读适配器。

这里不含解密、抓密钥或模型调用。具体读取由独立导出文件 helper 完成；
Tool Bridge 只负责授权闸门、Picker 绑定、参数限界和结果脱敏。
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from . import picker_state

_MAX_ITEMS = 200
_MAX_OUTPUT_BYTES = 5 * 1024 * 1024
_MAX_RANGE = timedelta(days=31)
_SAFE_SESSION_FIELDS = (
    "session_id", "name", "type", "last_message_at", "message_count",
)
_SAFE_MESSAGE_FIELDS = (
    "message_id", "session_id", "sender_id", "sender_name", "timestamp",
    "type", "text", "is_self",
)
_DOCTOR_REQUIREMENTS = {
    "protocol_version": 1,
    "read_only": True,
    "secure_key_store": True,
    "ephemeral_plaintext_cache": True,
    "modifies_wechat_app": False,
}
_EXPORT_SUFFIXES = {".json", ".jsonl", ".csv"}


class ReaderError(RuntimeError):
    """本机读取器协议错误；消息不得包含密钥或聊天正文。"""


def _config_path() -> Path:
    override = os.environ.get("CATFISH_WECHAT_ARCHIVE_CONFIG", "").strip()
    if override:
        return Path(override).expanduser()
    return picker_state._catfish_home() / "wechat_archive.json"


def _failure(error: str, reason_code: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": error, "reason_code": reason_code, **extra}


def _load_config() -> dict[str, Any] | None:
    try:
        data = json.loads(_config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _authorized_reader() -> tuple[Path, str, Path] | dict[str, Any]:
    config = _load_config()
    if not config or config.get("version") != 2 or config.get("enabled") is not True:
        return _failure(
            "微信历史分析尚未在 Companion 中授权",
            "not_authorized",
        )

    current_model = picker_state.read_picker_model()
    consented_model = str(config.get("consented_picker_model") or "").strip()
    if not current_model:
        return _failure("当前 Picker 没有选择模型", "picker_missing")
    if current_model != consented_model:
        return _failure(
            "Picker 已变化，请在 Companion 中重新确认微信历史分析授权",
            "picker_changed",
            current_picker_model=current_model,
            consented_picker_model=consented_model or None,
        )

    source_type = str(config.get("source_type") or "").strip()
    source: Path | None = None
    if source_type == "export_file":
        raw_source = str(config.get("source_path") or "").strip()
        source_candidate = Path(raw_source).expanduser()
        if not raw_source or not source_candidate.is_absolute():
            return _failure("聊天导出文件路径无效", "source_missing")
        try:
            source = source_candidate.resolve(strict=True)
            source_stat = source.stat()
        except OSError:
            return _failure("聊天导出文件已移动或删除，请重新选择", "source_missing")
        if not source.is_file() or source.suffix.casefold() not in _EXPORT_SUFFIXES:
            return _failure("只支持 JSON、JSONL、CSV 聊天导出文件", "unsupported_format")
        expected_size = config.get("source_size")
        expected_modified = config.get("source_modified_ns")
        if source_stat.st_size != expected_size or source_stat.st_mtime_ns != expected_modified:
            return _failure("聊天导出文件已发生变化，请重新选择并授权", "source_changed")
    else:
        return _failure("尚未选择受支持的微信数据源", "source_missing")

    raw_path = str(config.get("helper_path") or "").strip()
    candidate = Path(raw_path).expanduser()
    if not raw_path or not candidate.is_absolute():
        return _failure("微信历史安全读取器路径无效", "reader_missing")
    try:
        helper = candidate.resolve(strict=True)
    except OSError:
        return _failure("微信历史安全读取器未安装", "reader_missing")
    if not helper.is_file() or not os.access(helper, os.X_OK):
        return _failure("微信历史安全读取器不可执行", "reader_missing")
    return helper, current_model, source


def runtime_available() -> bool:
    """轻量能力探测；不启动读取器，安全自检留到真调用时执行。"""
    return not isinstance(_authorized_reader(), dict)


def _safe_env(temp_dir: str) -> dict[str, str]:
    allowed = ("HOME", "USERPROFILE", "PATH", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR")
    env = {key: os.environ[key] for key in allowed if os.environ.get(key)}
    env["TMPDIR"] = temp_dir
    env["CATFISH_WECHAT_TEMP_DIR"] = temp_dir
    return env


def _run_helper_json(helper: Path, command: str, args: list[str]) -> object:
    """按固定 argv 调审核读取器；不经 shell，也不透传 API key/proxy。"""
    with tempfile.TemporaryDirectory(prefix="catfish-wechat-") as temp_dir:
        try:
            completed = subprocess.run(
                [str(helper), command, "--json", *args],
                cwd=temp_dir,
                env=_safe_env(temp_dir),
                capture_output=True,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ReaderError(f"本机读取器启动失败: {type(exc).__name__}") from exc
    if completed.returncode != 0:
        raise ReaderError(f"本机读取器返回失败状态: {completed.returncode}")
    if len(completed.stdout) > _MAX_OUTPUT_BYTES:
        raise ReaderError("本机读取器输出超过 5 MB 安全上限")
    try:
        return json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReaderError("本机读取器没有返回有效 JSON") from exc


def _check_doctor(helper: Path) -> dict[str, Any] | None:
    try:
        report = _run_helper_json(helper, "doctor", [])
    except ReaderError as exc:
        return _failure(str(exc), "reader_unavailable")
    if not isinstance(report, dict) or any(
        report.get(key) != expected for key, expected in _DOCTOR_REQUIREMENTS.items()
    ):
        return _failure(
            "读取器未通过安全自检：必须只读、使用系统凭据库、临时明文缓存且不修改微信",
            "unsafe_reader",
        )
    return None


def _limit(raw: Any, default: int) -> int:
    try:
        return max(1, min(_MAX_ITEMS, int(raw if raw is not None else default)))
    except (TypeError, ValueError):
        return default


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _time_scope(args: dict[str, Any]) -> tuple[str, str] | dict[str, Any]:
    start_text = str(args.get("start_time") or "").strip()
    end_text = str(args.get("end_time") or "").strip()
    start = _parse_time(start_text)
    end = _parse_time(end_text)
    if start is None or end is None:
        return _failure("必须提供有效的 start_time 和 end_time", "invalid_scope")
    try:
        span = end - start
    except TypeError:
        return _failure("start_time 和 end_time 的时区格式必须一致", "invalid_scope")
    if span <= timedelta(0) or span > _MAX_RANGE:
        return _failure("时间范围必须大于 0 且不超过 31 天", "invalid_scope")
    return start_text, end_text


def _clean_text(value: Any, max_len: int) -> str:
    return str(value)[:max_len]


def _sanitize_items(payload: object, fields: tuple[str, ...], limit: int) -> list[dict[str, Any]]:
    raw_items = payload.get("items", []) if isinstance(payload, dict) else payload
    if not isinstance(raw_items, list):
        raise ReaderError("本机读取器 JSON 缺少 items 数组")
    cleaned: list[dict[str, Any]] = []
    for raw in raw_items[:limit]:
        if not isinstance(raw, dict):
            continue
        item: dict[str, Any] = {}
        for key in fields:
            if key not in raw or raw[key] is None:
                continue
            value = raw[key]
            if isinstance(value, (str, int, float, bool)):
                item[key] = _clean_text(value, 12000) if isinstance(value, str) else value
        cleaned.append(item)
    return cleaned


def _execute(command: str, cli_args: list[str], fields: tuple[str, ...], limit: int) -> dict[str, Any]:
    authorized = _authorized_reader()
    if isinstance(authorized, dict):
        return authorized
    helper, current_model, source = authorized
    doctor_error = _check_doctor(helper)
    if doctor_error is not None:
        return doctor_error
    try:
        helper_args = [*cli_args]
        helper_args.extend(["--source", str(source)])
        payload = _run_helper_json(helper, command, helper_args)
        items = _sanitize_items(payload, fields, limit)
    except ReaderError as exc:
        return _failure(str(exc), "reader_failed")
    return {
        "ok": True,
        "items": items,
        "count": len(items),
        "picker_model": current_model,
        "source_type": "export_file",
        "storage": "edge_only",
    }


def tool_wechat_sessions(args: dict[str, Any]) -> dict[str, Any]:
    limit = _limit(args.get("limit"), 50)
    return _execute("sessions", ["--limit", str(limit)], _SAFE_SESSION_FIELDS, limit)


def tool_wechat_history(args: dict[str, Any]) -> dict[str, Any]:
    session_id = str(args.get("session_id") or "").strip()
    if not session_id or len(session_id) > 300:
        return _failure("session_id 不能为空且不能超过 300 字符", "invalid_scope")
    scope = _time_scope(args)
    if isinstance(scope, dict):
        return scope
    start_time, end_time = scope
    limit = _limit(args.get("limit"), 100)
    cli_args = [
        "--session-id", session_id, "--start", start_time, "--end", end_time,
        "--limit", str(limit),
    ]
    return _execute("history", cli_args, _SAFE_MESSAGE_FIELDS, limit)


def tool_wechat_search(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query or len(query) > 200:
        return _failure("query 不能为空且不能超过 200 字符", "invalid_scope")
    scope = _time_scope(args)
    if isinstance(scope, dict):
        return scope
    start_time, end_time = scope
    limit = _limit(args.get("limit"), 100)
    cli_args = ["--query", query, "--start", start_time, "--end", end_time]
    session_id = str(args.get("session_id") or "").strip()
    if session_id:
        cli_args.extend(["--session-id", session_id])
    cli_args.extend(["--limit", str(limit)])
    return _execute("search", cli_args, _SAFE_MESSAGE_FIELDS, limit)


_HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "catfish_wechat_sessions": tool_wechat_sessions,
    "catfish_wechat_history": tool_wechat_history,
    "catfish_wechat_search": tool_wechat_search,
}


def dispatch(name: str, args: dict[str, Any]) -> dict[str, Any]:
    handler = _HANDLERS.get(name)
    if handler is None:
        raise ValueError(f"unknown wechat archive tool: {name}")
    return handler(args)
