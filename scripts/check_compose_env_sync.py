#!/usr/bin/env python3
"""交付包的 docker-compose 必须跟 central 那份转发同样的环境变量 (8/1).

## 为什么需要这个检查

`central/docker-compose.yml` 里每个环境变量都是**显式列名**转发的:

    INTERNAL_LLM_KEY: ${INTERNAL_LLM_KEY:-}

而 `delivery/dahua-poc/docker-compose.yml` 是交给客户的那份, 两边各存一份。
漏同步一行的后果是**静默的**:

  客户照 SOP 在 .env 里填了 CATFISH_SECRET_KEY
    → 容器里读不到 (compose 没转发)
    → 界面上填 API key 时报"没有配置 CATFISH_SECRET_KEY"
    → 客户去看 .env, 明明有

查起来要同时想到"env 是显式转发的"和"交付包是另一份文件"这两件事,
而这两件事都不写在任何一处。

8/1 加主密钥时就差点漏掉交付包那份。

跑: python3 scripts/check_compose_env_sync.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CENTRAL = ROOT / "central" / "docker-compose.yml"
DELIVERY = ROOT / "delivery" / "dahua-poc" / "docker-compose.yml"

# 交付包**有意**不带的变量放这里, 并写明理由 —— 空着的话这个检查会
# 因为一堆已知差异而被当成噪音忽略掉 (同 check_file_sizes 那个教训)。
KNOWN_DIFF: dict[str, str] = {}


def env_of(path: Path) -> dict[str, set[str]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    out: dict[str, set[str]] = {}
    for name, svc in (data.get("services") or {}).items():
        env = svc.get("environment") or {}
        out[name] = set(env.keys()) if isinstance(env, dict) else set()
    return out


def main() -> int:
    if not DELIVERY.exists():
        print(f"跳过: {DELIVERY} 不存在")
        return 0

    a, b = env_of(CENTRAL), env_of(DELIVERY)
    problems: list[str] = []
    for svc, keys in a.items():
        if svc not in b:
            continue  # 交付包裁掉了整个服务, 那是有意的
        missing = {k for k in keys - b[svc] if k not in KNOWN_DIFF}
        if missing:
            problems.append(
                f"  服务 {svc}: 交付包少转发 {len(missing)} 个变量 "
                f"→ {', '.join(sorted(missing))}"
            )

    if not problems:
        print("✓ 交付包 compose 的环境变量转发跟 central 一致")
        return 0

    print("✗ 交付包 docker-compose.yml 漏了环境变量转发:\n")
    print("\n".join(problems))
    print(
        "\n后果是静默的: 客户照 SOP 在 .env 里填了这些值, 容器里却读不到 ——"
        "\n表现成'填了但说没配', 而 .env 里明明有。"
        f"\n\n修法: 在 {DELIVERY.relative_to(ROOT)} 对应服务的 environment 下补上,"
        "\n有意不带的话加进本脚本的 KNOWN_DIFF 并写明理由。"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
