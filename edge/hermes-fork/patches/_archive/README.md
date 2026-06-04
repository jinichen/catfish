# Legacy 真 patches (5/29 hermes 0.15.2 升级后 hunk 失配)

这 3 个 patch 真**已被 catfish-xcatfish-user plugin 真 monkey-patch 全 cover**真:

| Patch | hunks | cover 它真 monkey-patch |
|---|---|---|
| 0001-api-server-cors-tauri-origin | 39 (5/29 升级 26 失配) | P8/P9_cors |
| 0002-auth-decouple-service-token | 12 (5/29 升级 9 失配) | P5 (server header extract) + P10 (client header inject) |
| 0003-api-server-companion-proxy | 3 (5/29 升级 1 失配) | P7_companion_proxy_route |

真**`5/29 升级 hunk 失配`** 不影响 catfish runtime — 真**`monkey-patch via plugin 已 cover`** real path.

留这真**`git 历史 + 真**`未来 真**`真**`patch 重做 真**`参考`** + 真**`brand check 不扫 _archive/`**.

真**`catfish 长期策略`**: monkey-patch via plugin, 不真**`fork hermes source`** (5/29 教训).
