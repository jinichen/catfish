# catfish-wiki-hub

P3.3.18 (6/10): 中央部门 wiki publish 服务. 员工 push 单条 wiki entity/concept/query 到团队共享 vault, 其他员工 pull 装本机看.

跟 skills-hub 同模式. 端口 **8994** (8995 secret-broker / 8996 mcp-registry / 8997 skills-hub / 8998 identity / 8999 gateway 都占).

## Manifesto 兼容性

`CATFISH-CENTRAL-MANIFESTO.md` 公理 2 例外条款明示允许 "员工主动 push 内容到团队 marketplace". skill_publish 是先例, wiki_publish 走同款.

- **公理 2** (数据零出端): 员工主动 push 是允许例外
- **公理 3** (中央 0 控制): 没有"中央 push wiki 到员工本机" endpoint, 只能员工 pull
- **公理 4** (API 物理无能): 没有"反向取回员工本机 wiki" endpoint. **unpublish 时只在中央 PG 标 stale=true + 清 body, 不动员工本机已 pull 的副本**. 客户端 fetch list 看到 stale 标自己决定怎么处理.

## 跟 skills-hub 关键差异

1. **没有 version 概念** — wiki 改了就 update (覆盖 body_md), 不保留历史. skill 是工具包要历史版本, wiki 是知识笔记意义低
2. **没有 file 目录** — wiki 就是单 markdown 文件, frontmatter + body 都进 PG `wiki_documents.frontmatter_yaml` / `body_md` text 字段
3. **unpublish 走软标 stale**, 不硬删 row — 公理 4 要求"已 pull 副本不受影响", 中央硬删的话已 pull 员工看 hub list 见不到这条, 但本机仍有, 容易产生"中央删了我本机还有, 是 bug 吗" 困惑. 软标 stale + 清 body 后, 客户端 fetch 见 row 含 stale=true, 就知道"原作者撤回了" 显灰 + 警告

## 端点

```
GET  /healthz                                          健康
GET  /wiki/documents?include_stale=                     列本部门已发布 wiki (admin 看全部)
GET  /wiki/documents/dept/{部门}/{file_id}              单条详情 + body (别的部门 403)
POST /wiki/documents                                    发布 / 重发到自己部门 (员工)
POST /wiki/documents/dept/{部门}/{file_id}/unpublish    撤回 (员工 self / admin)
GET  /wiki/audit?limit=                                 audit (admin)
```

## Namespace 与部门隔离 (9/30)

namespace 固定是 `dept/<部门>`, `<部门>` 就是发布者身份里的 department 原文
(gateway 注入的 `X-Catfish-User-Dept`, 可以是中文, 如 `dept/研发部`)。
由 hub 推出来, 调用方不传; 员工只能发到自己部门。

- 列表 / 详情 / 撤回只对本部门员工和 admin / sysadmin 开放
- 身份里没设部门的账号: 列表为空, 发布返 400 (提示找管理员补部门)
- 重发同一个 file_id 只有原作者 (或 admin) 可以, 其他人 403
- 部门名里不能有 `/ \ < > : " | ? * # %`、控制字符、`..`, 不能以 `.` 开头, 最长 64
  (见 `namespaces.py`)

9/30 之前 namespace 是员工手填的 `dept/finance`, 路由只接一个路径段 ——
发布一律 405, 这条链路上线后从来没通过。

## 存储

- 配了 `CATFISH_DB_URL`: PG (`wiki_documents` / `wiki_audit`) 为主, FS 做镜像
- 没配: FS 为主, `~/.catfish-hub/wiki/dept/<部门>/<file_id>.md` + `.meta.json`
  (元数据 sidecar: published_by / updated_at / stale, 撤回时只删 .md 留 sidecar)

## 测试

```bash
PYTHONPATH=src python -m pytest tests
# 连 PG 再跑一遍 (库要先 alembic upgrade head):
CATFISH_TEST_PG_URL=postgresql://... PYTHONPATH=src python -m pytest tests
```

## Auth

跟 skills-hub 同模式: 信 gateway 反代注入的 `X-Catfish-User-Sub` / `-Dept` / `-Role`. 不接受外部 Bearer.

向后兼容: dev 期 `CATFISH_WIKI_HUB_DEV_TOKEN` env 走 fallback (sub=`wiki-hub-dev-<token[:8]>`).

## 启动 (dev)

```bash
cd central/wiki-hub
pip install -e .

# 不接 PG (开发简单)
python -m catfish_wiki_hub.app  # port 8994, jsonl + FS 兜底

# 接 PG (真生产)
export CATFISH_DB_URL="postgresql://catfish:pwd@localhost/catfish"
alembic upgrade head
python -m catfish_wiki_hub.app
```

## gateway 反代

`/v1/wiki/*` → wiki-hub `/wiki/*`. 配置在 gateway yaml:

```yaml
wiki_hub:
  upstream_url: http://127.0.0.1:8994
  enabled: true
  timeout: 20
```

详见 `central/llm-gateway/src/catfish_gateway/wiki_hub_proxy.py`.

## 后续 Phase

Phase 1 (本次, P3.3.18) ship:
- wiki-hub FastAPI 服务 (publish / list / get / unpublish / audit)
- gateway `/v1/wiki/*` 反代
- PG schema + FS 兜底

留下次 phase:
- Phase 2: `catfish_wiki_publish` / `catfish_wiki_install` / `catfish_wiki_unpublish` 3 个 tools (tool-bridge 加)
- Phase 3: Companion UI — WikiPreview "分享到部门" 按钮 + 强警告 dialog + WikiHubCard
- Phase 4: 敏感词扫 (~/.catfish/wiki/sensitive_terms.txt) + 30 天 stale 标 polish
