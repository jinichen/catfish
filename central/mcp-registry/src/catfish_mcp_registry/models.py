"""Pydantic models — manifest schema + API request/response (BL-D3 Phase 1)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# ── manifest schema (yaml → Pydantic) ────────────────────────────────


class McpCommand(BaseModel):
    """MCP server 启动命令 (Phase 3 用)."""

    type: Literal["uvx", "npx", "docker", "exec"] = "uvx"
    package: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


class McpTool(BaseModel):
    """MCP server 暴露的单个 tool."""

    name: str
    description: str = ""


class OAuthConfig(BaseModel):
    """OAuth2 配置 (Phase 2 用)."""

    authorize_url: str
    token_url: str
    scopes: list[str] = Field(default_factory=list)
    required_env: list[str] = Field(default_factory=list)


class PathAllowlist(BaseModel):
    """文件系统路径白名单 (filesystem 连接器用, 替代 OAuth)."""

    default_paths: list[str] = Field(default_factory=list)
    configurable: bool = True
    forbidden_paths: list[str] = Field(default_factory=list)


class UiMeta(BaseModel):
    """Companion Dashboard 渲染元信息."""

    icon: str = ""
    category: str = ""
    vendor: str = ""
    homepage: str = ""


class McpManifest(BaseModel):
    """单个 MCP 连接器 manifest (一份 yaml 一个).

    yaml 文件位置: central/mcp-registry/manifests/<id>.yaml.
    Phase 1 只读 yaml, 不存 DB. Phase 2+ 加 DB 跟订阅状态合并.
    """

    id: str
    name: str
    version: str
    description: str = ""
    provider: str = ""
    status: Literal["active", "preview", "deprecated"] = "active"

    allowed_dept: list[str] = Field(default_factory=list)
    """空列表 = 全员可订阅; 非空 = 限制部门."""

    mcp_command: McpCommand
    tools: list[McpTool] = Field(default_factory=list)

    auth_type: Literal["oauth2", "path_allowlist", "none", "api_key"] = "none"
    oauth: OAuthConfig | None = None
    path_config: PathAllowlist | None = None

    ui: UiMeta = Field(default_factory=UiMeta)


# ── API request/response shapes ──────────────────────────────────────


class ConnectorListItem(BaseModel):
    """GET /v1/mcp/registry 响应里的单个 item.

    跟 McpManifest 几乎一致, 多 subscribed 字段 (Phase 2 加 DB 后 join).
    """

    id: str
    name: str
    version: str
    description: str
    provider: str
    status: str
    allowed_dept: list[str]
    auth_type: str
    tools: list[McpTool]
    ui: UiMeta
    subscribed: bool = False  # Phase 1 永远 False, Phase 2+ join mcp_subscriptions
    subscriber_count: int = 0  # Phase 2+ 真实统计


class RegistryResponse(BaseModel):
    """GET /v1/mcp/registry 完整响应."""

    connectors: list[ConnectorListItem]
    total: int
    user_dept: str = ""  # 当前员工部门 (从 JWT)
    filtered_by_dept: bool = False  # 是否做了部门过滤


class ManifestResponse(BaseModel):
    """GET /v1/mcp/manifest/{id} 完整响应 (含 OAuth / mcp_command 等内部字段)."""

    manifest: McpManifest


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "down"] = "ok"
    version: str
    manifests_loaded: int
    extras: dict[str, Any] = Field(default_factory=dict)


# ── Phase 2 (5/9): subscription / oauth schemas ──────────────────────


class SubscriptionView(BaseModel):
    """单个订阅 (mcp_subscriptions 一行)."""

    id: str
    user_sub: str
    connector_id: str
    status: Literal["pending_oauth", "active", "revoked"]
    oauth_token_ref: str | None = None
    subscribed_at: str  # iso
    # connector 元信息 join 进来给 Companion 渲染 (减一次往返)
    connector_name: str | None = None
    connector_version: str | None = None


class SubscribeRequest(BaseModel):
    connector_id: str = Field(..., min_length=1, max_length=100)


class SubscribeResponse(BaseModel):
    subscription: SubscriptionView
    next_step: Literal["oauth", "ready"]
    """oauth = 还要去授权; ready = 直接可用 (auth_type=none/path_allowlist)."""
    oauth_start_url: str | None = None


class OAuthStartRequest(BaseModel):
    subscription_id: str


class OAuthStartResponse(BaseModel):
    """OAuth flow 第一步返 — Phase 2 dev mock 模式: 直接返 mock callback URL,
    Companion 跳转后立即模拟成功. Phase 2 真接 Jira/GitLab 时 redirect 到 provider.
    """

    authorize_url: str
    state: str  # 也写到 mcp_subscriptions.oauth_state, callback 用


class OAuthCallbackRequest(BaseModel):
    state: str
    code: str  # provider 返的 authorization code
    # mock 模式 dev 用 (Phase 2 真 OAuth 后从 token endpoint 拿)
    mock_token: str | None = None


class OAuthCallbackResponse(BaseModel):
    subscription: SubscriptionView
    # P3.4.1 (6/13 hb): token 不再走中央 secret-broker. 中央 callback 拿到 token
    # 后直接返给 Companion, Companion 落本机 ~/.catfish/mcp/oauth-tokens/<ref>.
    # 中央对 token 不留存, audit 也只记 token_ref (不含 value).
    access_token: str | None = None
    token_ref_local: str | None = None  # Companion 本机存档建议用的 ref


class SubscriptionListResponse(BaseModel):
    subscriptions: list[SubscriptionView]
    total: int
