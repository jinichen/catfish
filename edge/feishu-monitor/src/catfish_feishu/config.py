"""配置加载 —— `~/.catfish/feishu.yaml` 优先，缺失字段回默认。

设计点：
    读员工在 ~/.hermes/config.yaml 里已经配好的 browser.cdp_url，
    避免让员工重复配 CDP WebSocket 地址。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

CATFISH_HOME = Path.home() / ".catfish"
CONFIG_FILE = CATFISH_HOME / "feishu.yaml"
HERMES_CONFIG = Path.home() / ".hermes" / "config.yaml"
LOG_FILE = CATFISH_HOME / "feishu-monitor.log"
PID_FILE = CATFISH_HOME / "feishu-monitor.pid"
INBOX_DIR = CATFISH_HOME / "feishu-inbox"
DRAFTS_DIR = CATFISH_HOME / "feishu-drafts"


DEFAULT_CONFIG = """# 鲶鱼飞书监听 · 配置
#
# 说明：只监听员工自己已经登录的飞书 Web 页面，不读 cookie 不调 API。
# 关键词越多，相关性过滤越准。建议先从姓名 + 项目名 + 几个同事名开始。

relevance:
  # 命中"强关键词"会立刻弹桌面通知 + 写 inbox + 生成草稿
  strong:
    names: []              # 例：["陈鸿波", "鸿波", "Hongbo"]
    mentions: []           # 例：["@陈鸿波"]（@ 的准确文本）

  # 命中"弱关键词"只写 inbox，不弹通知（避免打扰）
  soft:
    projects: []           # 例：["鲶鱼", "catfish", "合规平台"]
    systems: []            # 例：["网关", "LLM", "Hermes"]
    people: []             # 同事名，别人聊到他们时你可能关心

handlers:
  desktop_notification: true     # macOS 通知中心弹窗
  hermes_inbox: true             # 写到 ~/.catfish/feishu-inbox/
  auto_draft: true               # 预生成回复草稿到 ~/.catfish/feishu-drafts/

draft:
  gateway_url: "http://127.0.0.1:8999"
  model: "catfish-private-main"
  max_length: 200
  tone: "简洁、专业、不卑不亢，跟领导说话别太客套"

runtime:
  # 从 Hermes 配置里自动读 CDP URL（推荐 true）
  # 如果 false，用下面的 cdp_url_override
  cdp_url_from_hermes_config: true
  cdp_url_override: ""

  # 短时间内连续多条消息合并处理（毫秒）
  debounce_ms: 500

  # 只监听这些飞书域名的页面
  feishu_domains:
    - feishu.cn
    - larksuite.com

  log_level: "INFO"
"""


@dataclass
class RelevanceConfig:
    strong_names: list[str] = field(default_factory=list)
    strong_mentions: list[str] = field(default_factory=list)
    soft_projects: list[str] = field(default_factory=list)
    soft_systems: list[str] = field(default_factory=list)
    soft_people: list[str] = field(default_factory=list)


@dataclass
class HandlerConfig:
    desktop_notification: bool = True
    hermes_inbox: bool = True
    auto_draft: bool = True


@dataclass
class DraftConfig:
    gateway_url: str = "http://127.0.0.1:8999"
    model: str = "catfish-private-main"
    max_length: int = 200
    tone: str = "简洁、专业、不卑不亢"


@dataclass
class RuntimeConfig:
    cdp_url_from_hermes_config: bool = True
    cdp_url_override: str = ""
    debounce_ms: int = 500
    feishu_domains: list[str] = field(default_factory=lambda: ["feishu.cn", "larksuite.com"])
    log_level: str = "INFO"


@dataclass
class FeishuConfig:
    relevance: RelevanceConfig = field(default_factory=RelevanceConfig)
    handlers: HandlerConfig = field(default_factory=HandlerConfig)
    draft: DraftConfig = field(default_factory=DraftConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)


def ensure_config_exists() -> Path:
    CATFISH_HOME.mkdir(exist_ok=True)
    INBOX_DIR.mkdir(exist_ok=True)
    DRAFTS_DIR.mkdir(exist_ok=True)
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(DEFAULT_CONFIG, encoding="utf-8")
    return CONFIG_FILE


def _read_hermes_cdp_url() -> Optional[str]:
    if not HERMES_CONFIG.exists():
        return None
    try:
        data = yaml.safe_load(HERMES_CONFIG.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return None
    browser = data.get("browser") or {}
    if not isinstance(browser, dict):
        return None
    url = browser.get("cdp_url")
    return url if isinstance(url, str) and url else None


def load_config() -> FeishuConfig:
    ensure_config_exists()
    data = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
    return FeishuConfig(
        relevance=_parse_relevance(data.get("relevance") or {}),
        handlers=_parse_handlers(data.get("handlers") or {}),
        draft=_parse_draft(data.get("draft") or {}),
        runtime=_parse_runtime(data.get("runtime") or {}),
    )


def resolve_cdp_url(cfg: FeishuConfig) -> Optional[str]:
    """返回最终要连的 CDP WebSocket URL。"""
    if cfg.runtime.cdp_url_override:
        return cfg.runtime.cdp_url_override
    if cfg.runtime.cdp_url_from_hermes_config:
        return _read_hermes_cdp_url()
    return None


def _parse_relevance(d: dict[str, Any]) -> RelevanceConfig:
    strong = d.get("strong") or {}
    soft = d.get("soft") or {}
    return RelevanceConfig(
        strong_names=list(strong.get("names") or []),
        strong_mentions=list(strong.get("mentions") or []),
        soft_projects=list(soft.get("projects") or []),
        soft_systems=list(soft.get("systems") or []),
        soft_people=list(soft.get("people") or []),
    )


def _parse_handlers(d: dict[str, Any]) -> HandlerConfig:
    return HandlerConfig(
        desktop_notification=bool(d.get("desktop_notification", True)),
        hermes_inbox=bool(d.get("hermes_inbox", True)),
        auto_draft=bool(d.get("auto_draft", True)),
    )


def _parse_draft(d: dict[str, Any]) -> DraftConfig:
    return DraftConfig(
        gateway_url=str(d.get("gateway_url") or "http://127.0.0.1:8999"),
        model=str(d.get("model") or "catfish-private-main"),
        max_length=int(d.get("max_length") or 200),
        tone=str(d.get("tone") or "简洁、专业、不卑不亢"),
    )


def _parse_runtime(d: dict[str, Any]) -> RuntimeConfig:
    return RuntimeConfig(
        cdp_url_from_hermes_config=bool(d.get("cdp_url_from_hermes_config", True)),
        cdp_url_override=str(d.get("cdp_url_override") or ""),
        debounce_ms=int(d.get("debounce_ms") or 500),
        feishu_domains=list(d.get("feishu_domains") or ["feishu.cn", "larksuite.com"]),
        log_level=str(d.get("log_level") or "INFO"),
    )
