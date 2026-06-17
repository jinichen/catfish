#!/usr/bin/env python3
"""P3.5.29 Phase 3 (6/17 鸿波) — 真**ops 脚本** sync RBAC db rows ← roles.yaml.

# 真**为啥**

`departments.allowed_models` 真**JSONB list of model names**, alembic
migration 真**示范 INSERT 写死 model name** (e.g.
``["catfish-public-deepseek-flash"]``). 真**客户改 roles.yaml** → migration
真**不会**自动 update db row → RBAC 真**永远引用 stale name**.

这脚本真**部署后跑一次**: 读 roles.yaml ``rbac_default_allowed`` 段 →
roles.resolve() resolve role refs → UPDATE departments. 客户改 yaml 真**重跑**
脚本即可 sync.

# 真**用法**

```bash
# 真**预览**模式 (默认): 真**不动 db**, print SQL
python scripts/seed_rbac_from_roles.py

# 真**实际**写 db
python scripts/seed_rbac_from_roles.py --apply

# 真**指定** roles.yaml 路径 (默认 central/llm-gateway/config/roles.yaml)
python scripts/seed_rbac_from_roles.py --roles-yaml /path/to/roles.yaml --apply

# 真**指定** DB connection (默认 env CATFISH_PG_DSN)
python scripts/seed_rbac_from_roles.py --dsn postgresql://user:pw@host/db --apply
```

# 真**RBAC role → department name** 映射

约定:
  rbac_default_allowed.employee → 真**初始** dept "engineering" (默认全允许 = role
                                    employee allowed models)
  rbac_default_allowed.manager  → 真**初始** dept "ops" (manager 真**多 role**)
  rbac_default_allowed.admin    → 真**初始** dept "sales" ← 客户真**通常**写死
                                    (sales 真**便宜模型 only**), 默认覆盖
  rbac_default_allowed.sysadmin → 真**初始** dept "legal" (sysadmin 全权但
                                    legal 真**强合规要内网**)

这真**约定**, 客户可 override 任何 department - role 映射.

# 真**不会** drop / delete

真**只 UPDATE 已存在 row**. 新 dept 真**不创建** (alembic migration 真已 INSERT
4 示范 dept). 客户自己 admin UI 加新 dept.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sync RBAC db rows ← roles.yaml (P3.5.29 Phase 3)"
    )
    parser.add_argument(
        "--roles-yaml",
        type=Path,
        default=None,
        help="roles.yaml 真路径 (默认: central/llm-gateway/config/roles.yaml)",
    )
    parser.add_argument(
        "--dsn",
        type=str,
        default=None,
        help="DB connection 真**DSN** (默认: env CATFISH_PG_DSN). "
             "P3.5.29.2 改 None default — shell `--dsn $VAR` 真**展开空** 不再"
             "撞 argparse 'expected one argument' 错.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真**实际写 db**. 默认仅 print 预览 SQL.",
    )
    args = parser.parse_args()

    # 真**加载** roles.yaml
    repo_root = Path(__file__).parent.parent
    sys.path.insert(0, str(repo_root / "central" / "llm-gateway" / "src"))
    from catfish_gateway import roles as roles_module

    roles_yaml_path = args.roles_yaml or (
        repo_root / "central" / "llm-gateway" / "config" / "roles.yaml"
    )
    if not roles_yaml_path.exists():
        print(f"❌ roles.yaml 真**不存在**: {roles_yaml_path}", file=sys.stderr)
        return 1

    roles_module.load_roles(roles_yaml_path)

    # 真**RBAC role → department name** 约定 (见 docstring)
    rbac_to_dept = {
        "employee": "engineering",
        "manager": "ops",
        "admin": "sales",
        "sysadmin": "legal",
    }

    # 真**生成 UPDATE 真 SQL list**
    sql_list: list[tuple[str, list[str]]] = []
    for rbac_role, dept in rbac_to_dept.items():
        try:
            models = roles_module.list_models_for_rbac(rbac_role)
        except roles_module.UnknownRoleError:
            print(
                f"⚠ rbac_default_allowed 真**没** {rbac_role!r}, 跳过 dept "
                f"{dept!r}",
                file=sys.stderr,
            )
            continue
        sql_list.append((dept, models))

    if not sql_list:
        print("❌ 真**0** rbac role 真能 resolve, 退出 (检查 roles.yaml)")
        return 1

    print(f"✓ 加载 roles.yaml: {roles_yaml_path}")
    print(f"✓ 真 {len(sql_list)} 个 dept 真**待 sync**:")
    for dept, models in sql_list:
        print(f"    {dept!r:15} ← {models}")
    print()

    # 真**预览 SQL** (always print)
    print("=" * 60)
    print("真**SQL 预览**:")
    print("=" * 60)
    for dept, models in sql_list:
        models_jsonb = json.dumps(models, ensure_ascii=False)
        sql = (
            f"UPDATE departments SET allowed_models = "
            f"'{models_jsonb}'::jsonb, updated_at = NOW() "
            f"WHERE name = '{dept}';"
        )
        print(sql)
    print()

    if not args.apply:
        print("ℹ 真**预览**模式 (默认). 加 --apply 真**实际写 db**.")
        return 0

    # 真**实际**写 db
    # P3.5.29.2: --dsn 真**优先**, 真不传 fallback env CATFISH_PG_DSN.
    dsn = args.dsn or os.environ.get("CATFISH_PG_DSN", "")
    if not dsn:
        print(
            "❌ 真**没 DSN** — --dsn 没传 (或真**shell 展开成空**), "
            "env CATFISH_PG_DSN 也没设. 退出.\n"
            "   真**用法**:\n"
            "     export CATFISH_PG_DSN=postgresql://catfish:pw@localhost/catfish\n"
            "     python3 scripts/seed_rbac_from_roles.py --apply\n"
            "   或:\n"
            "     python3 scripts/seed_rbac_from_roles.py --apply --dsn 'postgresql://...'",
            file=sys.stderr,
        )
        return 1

    try:
        import psycopg
    except ImportError:
        print(
            "❌ psycopg 真**没装** — pip install psycopg[binary]",
            file=sys.stderr,
        )
        return 1

    print("=" * 60)
    print(f"真**执行** UPDATE 到 db: {dsn[:30]}...")
    print("=" * 60)

    affected = 0
    with psycopg.connect(dsn, autocommit=False) as conn:
        with conn.cursor() as cur:
            for dept, models in sql_list:
                models_jsonb = json.dumps(models, ensure_ascii=False)
                cur.execute(
                    "UPDATE departments SET allowed_models = %s::jsonb, "
                    "updated_at = NOW() WHERE name = %s",
                    [models_jsonb, dept],
                )
                rows = cur.rowcount
                affected += rows
                print(f"    {dept!r:15} → {rows} row(s) updated")
        conn.commit()

    print()
    print(f"✓ 真**总计** {affected} 个 dept row updated.")
    if affected < len(sql_list):
        print(
            f"⚠ 真**预期** {len(sql_list)} 个, 真**实际** {affected} 个 — "
            "部分 dept 真**db 里不存在** (rbac_to_dept 约定 mismatch?)",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
