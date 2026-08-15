"""往 hermes 里同步凭据 —— config.yaml 的 api_key、service token、边缘 backend key。

8/15 从 catfish.py 搬出来。合并了原来分开的两段:
`# ─── hermes config 同步` 和 `# ─── BL-EDGE-TOOL-KEY (5/24) 中央派发`。

# 为什么这两段必须放同一个文件

它们**互相调用**, 而且调用链上有三个名字被 test_catfish.py monkeypatch:

    _sync_hermes_with_service_token → _patch_hermes_config     ← 被 patch
                                    → _mint_hermes_service_token
    _sync_hermes_edge_tool_configs  → _fetch_edge_tool_list     ← 被 patch
                                    → _fetch_edge_tool_config   ← 被 patch
                                    → _patch_hermes_env_file
                                    → _patch_hermes_config_yaml_blocks

函数体里的自由变量在**定义它的模块**的 globals 里查。把调用者和被 patch 的
callee 拆到两个文件, patch 就打空了 —— 而且这里打空的后果特别脏:
`_patch_hermes_config` 会真的去写 `~/.hermes/config.yaml`,
`_patch_hermes_env_file` 会真的去写 `~/.hermes/.env`。**跑一趟单测就改了
开发机的真实配置**, 而测试照样绿。

同一天 catfish_proxy.py 上已经栽过一次同型的 (那次是真去 restart hermes)。
判据只有一条: **被 monkeypatch 的名字必须和它的调用者同模块**。

# 对外只暴露三个入口

    _sync_hermes_with_service_token  ← cmd_login / cmd_refresh / cmd_token / cmd_refresh_hermes
    _sync_hermes_edge_tool_configs   ← cmd_refresh_hermes
    _patch_hermes_config             ← 老 caller + 测试直接调

# 安全边界 (别改)

`_patch_hermes_env_file` 写的是 `~/.hermes/.env`, 里面是**中央派发下来的
backend key**。它只做 per-key update, 保留无关行, 并且改前备份 —— 因为这个
文件里还可能有员工自己加的东西。写之前 chmod 600。
"""
from __future__ import annotations

import json
import os
import time
import urllib.error   # ← 见 catfish_token.py 顶部关于这一行的说明
import urllib.parse
import urllib.request
from typing import Optional

from catfish_config import (
    DEFAULT_HERMES_CLI_CLIENT_ID,
    DEFAULT_HERMES_CLI_DEV_SECRET,
    HERMES_PROVIDER_NAME,
    HERMES_SERVICE_SCOPES,
    _gateway_url,
    _hermes_config_path,
    _hermes_env_path,
    logger,
)
from catfish_token import _decode_jwt_payload

# ─── hermes config 同步 ───────────────────────────────────


def _patch_hermes_config(token: str) -> Optional[str]:
    """把 token 写进 ~/.hermes/config.yaml 的 custom_providers."Local (localhost:8999)".api_key.

    返 None = 没找到 hermes config 或 provider (用户没 setup 过), 跳过.
    返 str = 改了哪个 provider 的 api_key.

    要求 yaml 库 (PyYAML). 没装 logger.warning 跳过 (不挂).
    """
    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        logger.warning("PyYAML 没装 (pip install pyyaml), 跳过 hermes config 同步")
        return None

    cfg_path = _hermes_config_path()
    if not cfg_path.exists():
        logger.info("hermes config (%s) 不存在, 跳过 (员工先跑 hermes model 配 Custom endpoint)", cfg_path)
        return None

    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    custom_providers = cfg.get("custom_providers")
    if not custom_providers:
        logger.info("hermes config 没 custom_providers 段, 跳过")
        return None

    # hermes 0.13 实际存 list-of-dict 格式 (5/15 鸿波端到端测发现, 之前我误判 dict).
    # 兼容两种格式: list[dict] (hermes 0.13) 和 dict[name → config] (老版本可能).
    target = None
    if isinstance(custom_providers, list):
        # list-of-dict 格式 — 每个 dict 含 name + base_url + api_key + model
        for entry in custom_providers:
            if not isinstance(entry, dict):
                continue
            entry_name = entry.get("name", "") or entry.get("display_name", "")
            base_url = entry.get("base_url", "") or ""
            if entry_name == HERMES_PROVIDER_NAME or "localhost:8999" in base_url or "127.0.0.1:8999" in base_url:
                entry["api_key"] = token
                target = entry_name or "<unnamed>"
                break
    elif isinstance(custom_providers, dict):
        # dict 格式 (老版本)
        if HERMES_PROVIDER_NAME in custom_providers:
            target = HERMES_PROVIDER_NAME
        else:
            for name, p in custom_providers.items():
                base_url = (p or {}).get("base_url", "") if isinstance(p, dict) else ""
                if "localhost:8999" in base_url or "127.0.0.1:8999" in base_url:
                    target = name
                    break
        if target:
            custom_providers[target] = custom_providers[target] or {}
            custom_providers[target]["api_key"] = token

    if not target:
        logger.info(
            "hermes config 没找到 catfish provider (期望 '%s' 或 base_url 含 localhost:8999), 跳过",
            HERMES_PROVIDER_NAME,
        )
        return None

    cfg["custom_providers"] = custom_providers

    # 5/15 鸿波端到端测发现: hermes 0.13 active 配置在顶层 'model:' 段, 不在
    # custom_providers (那是配置库). 选 Custom endpoint 时 hermes 把配置复制到 model:
    # 之后启动只读 model:. 我们必须**同时** patch model.api_key 才真生效.
    model_section = cfg.get("model")
    if isinstance(model_section, dict):
        model_base_url = model_section.get("base_url", "") or ""
        if "localhost:8999" in model_base_url or "127.0.0.1:8999" in model_base_url:
            model_section["api_key"] = token
            logger.info("同步 model.api_key (hermes 真用的 active 配置)")

    # 备份原文件 + 原子写
    backup = cfg_path.with_suffix(".yaml.bak")
    backup.write_text(cfg_path.read_text(encoding="utf-8"))
    tmp = cfg_path.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))
    tmp.replace(cfg_path)
    return target


# ─── BL-HERMES-SERVICE-TOKEN (5/24): hermes service token mint + 同步 ──


def _hermes_cli_client_secret() -> str:
    """读 hermes-cli 的 client_secret.

    生产: env CATFISH_HERMES_CLI_SECRET (跟 identity-server clients.yaml hermes-cli
    那条 bcrypt hash 对应的明文).
    Dev: fallback 到 identity-server clients.yaml 注释里写的 demo secret.

    返空 → 抛错由 caller 处理.
    """
    return os.environ.get("CATFISH_HERMES_CLI_SECRET", DEFAULT_HERMES_CLI_DEV_SECRET)


def _mint_hermes_service_token(
    identity_url: str,
    client_id: str = DEFAULT_HERMES_CLI_CLIENT_ID,
    scopes: str = HERMES_SERVICE_SCOPES,
) -> str:
    """调 catfish-identity /token grant_type=client_credentials 拿 service token.

    实施 BL-RBAC P0 (5/14) 设计的 RFC 6749 §4.4 client_credentials grant. 返
    sub=client:hermes-cli, aud=catfish-gateway, TTL 30 天的 access_token.

    跟用户 OAuth 完全独立 — 哪怕用户没 login / OAuth token 过期, hermes 也照常跑.

    Returns: JWT 字符串 (~600-800 bytes).
    Raises: RuntimeError on 网络 / HTTP / parse 失败.
    """
    secret = _hermes_cli_client_secret()
    if not secret:
        raise RuntimeError(
            "hermes-cli client_secret 没配 (env CATFISH_HERMES_CLI_SECRET 空 + dev "
            "fallback 也被清). 看 identity-server/config/clients.yaml hermes-cli 段."
        )
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": secret,
        "scope": scopes,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{identity_url.rstrip('/')}/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_str = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"client_credentials mint 失败 (HTTP {e.code}): {body_str}. "
            f"检查 CATFISH_HERMES_CLI_SECRET 是否跟 identity-server 配的 hash 对得上."
        ) from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise RuntimeError(
            f"catfish-identity ({identity_url}) 不可达: {e}. "
            f"检查 identity-server 是否在跑."
        ) from e

    tok = data.get("access_token")
    if not tok:
        raise RuntimeError(f"identity 没返 access_token: {data}")
    return tok



# ─── BL-EDGE-TOOL-KEY (5/24): 中央派发 hermes 边缘 backend key ─────
#
# 后续会扩到 image_generate / x_search / 等其他外部 key 工具. 不在 scope:
# Tavily quota 不在 catfish 跟 (Tavily 自家 dashboard 看).
# 详见 central/llm-gateway/src/catfish_gateway/edge_tool_config.py.


def _fetch_edge_tool_list(gateway_url: str, token: str) -> list[str]:
    """GET /v1/edge/tool-config → 拿支持的 tool 列表.

    返空 list = gateway 不支持这接口 (老版本) 或网络挂. caller 应当跳过, 不挂.
    """
    req = urllib.request.Request(
        f"{gateway_url.rstrip('/')}/v1/edge/tool-config",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        # 404 → gateway 老版本没这接口, 静默跳过.
        if e.code == 404:
            logger.info("[edge-tool] gateway %s 没 /v1/edge/tool-config (老版本?), 跳过", gateway_url)
            return []
        logger.warning("[edge-tool] list endpoint HTTP %d: %s", e.code, e.read()[:200])
        return []
    except (urllib.error.URLError, TimeoutError) as e:
        logger.warning("[edge-tool] gateway %s 不可达: %s", gateway_url, e)
        return []
    return list(data.get("supported", []))


def _fetch_edge_tool_config(
    gateway_url: str, token: str, tool_name: str,
) -> Optional[dict]:
    """GET /v1/edge/tool-config/{tool_name} → 拿 env_vars + yaml_block.

    返 None: 任何失败 (403 RBAC 拦 / 503 admin 没配 key / 网络).
    caller 应当 print warning 跳过这个 tool, 不挂.
    """
    req = urllib.request.Request(
        f"{gateway_url.rstrip('/')}/v1/edge/tool-config/{tool_name}",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        if e.code == 403:
            print(f"  edge-tool: ⏭ {tool_name} — 部门 RBAC 没批 (admin 加白名单后重试)")
        elif e.code == 503:
            print(f"  edge-tool: ⏭ {tool_name} — gateway 中央 .env 没配 key (admin 配后重启 gateway)")
        elif e.code == 404:
            # 接口存在但 tool 不在 registry — 不该发生 (我们从 list 拿的), log 一下
            logger.warning("[edge-tool] %s 404: %s", tool_name, body)
        else:
            print(f"  edge-tool: ⏭ {tool_name} — gateway HTTP {e.code}")
            logger.warning("[edge-tool] %s HTTP %d: %s", tool_name, e.code, body)
        return None
    except (urllib.error.URLError, TimeoutError) as e:
        logger.warning("[edge-tool] %s 网络: %s", tool_name, e)
        return None


def _patch_hermes_env_file(env_vars: dict[str, str]) -> int:
    """合并写 ~/.hermes/.env, per-key update, 保留无关行 + 注释.

    格式: 每行 KEY=VALUE 或注释. 已存在的 key 就地覆盖, 不存在的追加在尾部.
    我们的写入行带 catfish marker 注释让员工知道是 catfish 同步进来的.

    返 patched 的 key 数 (0 = env_vars 空 / 全部没变化).

    示例:
        old .env:
            FIRECRAWL_API_KEY=fc-old   # 员工手贴的
        env_vars: {TAVILY_API_KEY: "tvly-new"}
        new .env:
            FIRECRAWL_API_KEY=fc-old   # 员工手贴的
            # ── catfish-cli 同步 (BL-EDGE-TOOL-KEY) ──
            TAVILY_API_KEY=tvly-new
    """
    if not env_vars:
        return 0

    env_path = _hermes_env_path()
    env_path.parent.mkdir(parents=True, exist_ok=True)

    # 读现有内容 (不存在视为空文件)
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()

    # per-key update
    seen: set[str] = set()
    out_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        # 注释 / 空白 / 不含 = 的行: 原样保留
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out_lines.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in env_vars:
            out_lines.append(f"{key}={env_vars[key]}")
            seen.add(key)
        else:
            out_lines.append(line)

    # 没出现过的 key → 追加 (带 marker, 一次写一组)
    new_keys = [k for k in env_vars if k not in seen]
    if new_keys:
        if out_lines and out_lines[-1].strip() != "":
            out_lines.append("")
        out_lines.append("# ── catfish-cli 同步 (BL-EDGE-TOOL-KEY 中央派发) ──")
        for k in new_keys:
            out_lines.append(f"{k}={env_vars[k]}")

    # 备份 + 原子写
    if env_path.exists():
        backup = env_path.with_suffix(".env.bak")
        backup.write_text(env_path.read_text(encoding="utf-8"), encoding="utf-8")
    tmp = env_path.with_suffix(".env.tmp")
    tmp.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    tmp.replace(env_path)
    # chmod 600 — env 含 secret, 跟 token store 一个标准
    try:
        env_path.chmod(0o600)
    except OSError:
        pass

    return len(env_vars)


def _patch_hermes_config_yaml_blocks(yaml_blocks: list[dict]) -> int:
    """合并写 ~/.hermes/config.yaml 的顶层段 (web/image/...), preserve sibling keys.

    yaml_blocks: [{"web": {"backend": "tavily"}}, {"image": {...}}]
      → 把每个 dict 的顶层 key 合并进 config.yaml 顶层.
      已有的 sibling key (model / custom_providers / 等) 不动.

    返 patched 的 top-level key 数. config.yaml 不存在则跳过 (返 0).
    """
    if not yaml_blocks:
        return 0

    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        logger.warning("PyYAML 没装, 跳过 ~/.hermes/config.yaml yaml block 同步")
        return 0

    cfg_path = _hermes_config_path()
    if not cfg_path.exists():
        # 没 config.yaml → 仅靠 .env 的 auto-detect 也能让 hermes web_search 跑
        # (TAVILY_API_KEY 存在 → 自动选 Tavily). 跳过 yaml 不是错.
        logger.info("hermes config (%s) 不存在, 跳过 yaml block 同步 (env 已写够用)", cfg_path)
        return 0

    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    patched_keys: list[str] = []
    for block in yaml_blocks:
        if not isinstance(block, dict):
            continue
        for top_key, top_val in block.items():
            existing = cfg.get(top_key)
            if isinstance(existing, dict) and isinstance(top_val, dict):
                # 浅合并 — sibling sub-key 保留, 同名 sub-key 覆盖
                existing.update(top_val)
                cfg[top_key] = existing
            else:
                cfg[top_key] = top_val
            patched_keys.append(top_key)

    if not patched_keys:
        return 0

    # 备份 + 原子写 (跟 _patch_hermes_config 同款)
    backup = cfg_path.with_suffix(".yaml.bak")
    backup.write_text(cfg_path.read_text(encoding="utf-8"), encoding="utf-8")
    tmp = cfg_path.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    tmp.replace(cfg_path)

    return len(set(patched_keys))


def _sync_hermes_edge_tool_configs(gateway_url: str, token: str) -> tuple[int, int]:
    """把中央派发的 backend key 同步到 ~/.hermes/.env + config.yaml.

    返 (env_key_count, yaml_top_key_count) 让 caller 打 summary.
    任一步失败 (网络 / RBAC 拦 / 没配 key) 都不挂, 返 (0, 0).

    幂等: 重跑只覆盖差异行, .env / yaml 里别的内容不动.
    """
    tools = _fetch_edge_tool_list(gateway_url, token)
    if not tools:
        return 0, 0

    # RBAC 是 per-tool, 必须每个 tool 单独打 endpoint 让 gateway 判权
    # (理论上 admin 可以放 web_search 但不放 web_extract 给某部门).
    # 但**写盘 dedupe by tool_group** — 3 个 web tool 共用 TAVILY_API_KEY,
    # 拉 3 次后只往 .env 写 1 行, yaml 也只更新 1 段.
    env_vars: dict[str, str] = {}
    yaml_blocks: list[dict] = []
    seen_groups: set[str] = set()
    for name in tools:
        cfg = _fetch_edge_tool_config(gateway_url, token, name)
        if cfg is None:
            continue
        group = cfg.get("tool_group") or name
        if group in seen_groups:
            continue
        seen_groups.add(group)
        env_vars.update(cfg.get("env_vars") or {})
        yb = cfg.get("yaml_block")
        if isinstance(yb, dict) and yb:
            yaml_blocks.append(yb)

    env_n = _patch_hermes_env_file(env_vars)
    yaml_n = _patch_hermes_config_yaml_blocks(yaml_blocks)
    return env_n, yaml_n


def _sync_hermes_with_service_token(
    identity_url: str,
    user_fallback_token: Optional[str] = None,
) -> Optional[str]:
    """Mint hermes-cli service token + 写进 hermes config api_key.

    主路径: client_credentials → 30 天 token → 写盘.
    Fallback: 如果 mint 失败但 caller 给了 user_fallback_token, 退化到老行为
    (用 user OAuth access_token, 1 小时后会撞 401, 但至少能立刻用).

    返 _patch_hermes_config 的结果 (patched provider name 或 None).
    """
    try:
        tok = _mint_hermes_service_token(identity_url)
    except Exception as e:
        logger.warning("[hermes-svc] mint 失败: %s", e)
        if user_fallback_token:
            logger.warning(
                "[hermes-svc] fallback 写 user access_token (1h TTL, 之后会 401, "
                "请检查 CATFISH_HERMES_CLI_SECRET / identity-server)"
            )
            return _patch_hermes_config(user_fallback_token)
        return None

    # 友好打印: service token 的 sub / exp 让员工知道发生了啥
    payload = _decode_jwt_payload(tok)
    sub = payload.get("sub", "?")
    exp = payload.get("exp", 0)
    if exp:
        remaining_days = (exp - time.time()) / 86400.0
        print(
            f"  hermes-svc: ✓ 已 mint service token "
            f"(sub={sub}, 还有 {remaining_days:.1f} 天有效)"
        )
    else:
        print(f"  hermes-svc: ✓ 已 mint service token (sub={sub})")

    patched = _patch_hermes_config(tok)

    # BL-EDGE-TOOL-KEY (5/24): 顺手拉中央派发的 backend key (Tavily 等), 写进
    # ~/.hermes/.env + config.yaml. 失败 (网络 / RBAC / admin 没配) 不挂主流程,
    # hermes 自己 token 写完才是 critical path, 工具 key 是 nice-to-have.
    try:
        env_n, yaml_n = _sync_hermes_edge_tool_configs(_gateway_url(), tok)
        if env_n or yaml_n:
            print(
                f"  edge-tool: ✓ 同步 {env_n} 个 env key + {yaml_n} 个 yaml 段 "
                f"(~/.hermes/.env, ~/.hermes/config.yaml)"
            )
    except Exception as e:  # pragma: no cover — defensive
        logger.warning("[edge-tool] 同步出错 (不影响 hermes token): %s", e)

    return patched


