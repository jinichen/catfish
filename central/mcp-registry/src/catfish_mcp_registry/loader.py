"""manifest loader — 扫 manifests/*.yaml → 解析 → 缓存 (BL-D3 Phase 1).

Phase 1: 全在内存, 启动时扫一次 + 启 watcher (改文件自动 reload).
Phase 2+: 加 PostgreSQL 持久化 + 管理员 CRUD.
"""
from __future__ import annotations

import logging
from pathlib import Path

import yaml

from .models import McpManifest

logger = logging.getLogger("catfish.mcp_registry.loader")


class ManifestRegistry:
    """内存 registry — 扫 manifests/*.yaml 解析 + 索引."""

    def __init__(self, manifests_dir: Path):
        self.manifests_dir = manifests_dir
        self._by_id: dict[str, McpManifest] = {}

    def load_all(self) -> int:
        """扫 manifests_dir 下所有 *.yaml, 解析 + 索引. 返回加载了几个.

        非法 yaml / Pydantic 校验失败的跳过 + 报 ERROR, 不阻塞启动.
        """
        self._by_id.clear()
        if not self.manifests_dir.exists():
            logger.warning(
                "manifests dir 不存在: %s — registry 空启动",
                self.manifests_dir,
            )
            return 0

        for yaml_file in sorted(self.manifests_dir.glob("*.yaml")):
            try:
                with yaml_file.open(encoding="utf-8") as f:
                    raw = yaml.safe_load(f)
                if not isinstance(raw, dict):
                    logger.error("manifest %s 不是 dict, 跳过", yaml_file)
                    continue
                manifest = McpManifest.model_validate(raw)
                if manifest.id in self._by_id:
                    logger.error(
                        "manifest id 冲突: %s 已存在, %s 跳过",
                        manifest.id, yaml_file,
                    )
                    continue
                self._by_id[manifest.id] = manifest
                logger.info(
                    "loaded manifest: id=%s name=%s version=%s tools=%d",
                    manifest.id, manifest.name, manifest.version, len(manifest.tools),
                )
            except yaml.YAMLError as e:
                logger.error("manifest %s YAML 解析失败: %s", yaml_file, e)
            except Exception as e:  # noqa: BLE001  Pydantic ValidationError 等
                logger.error("manifest %s 校验失败: %s", yaml_file, e)

        logger.info(
            "manifest registry loaded: %d manifests from %s",
            len(self._by_id), self.manifests_dir,
        )
        return len(self._by_id)

    def get(self, connector_id: str) -> McpManifest | None:
        """按 id 拿 manifest."""
        return self._by_id.get(connector_id)

    def list_all(self) -> list[McpManifest]:
        """列所有 manifest (按 id 字典序排, deterministic)."""
        return [self._by_id[k] for k in sorted(self._by_id.keys())]

    def list_for_dept(self, dept: str | None) -> list[McpManifest]:
        """按部门过滤 — manifest.allowed_dept 空列表 = 全员; 非空 = 在列才返.

        dept 是空字符串 / None 时只返 allowed_dept 空 (= 全员可见) 的, 防 dev
        漏 dept 时露管控连接器.
        """
        out = []
        for m in self.list_all():
            if not m.allowed_dept:
                # 空列表 = 全员可订阅
                out.append(m)
                continue
            if dept and dept in m.allowed_dept:
                out.append(m)
        return out

    @property
    def count(self) -> int:
        return len(self._by_id)
