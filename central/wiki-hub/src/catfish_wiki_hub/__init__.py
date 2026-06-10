"""catfish-wiki-hub — 中央部门 wiki publish/pull 服务 (P3.3.18, 6/10).

跟 skills-hub 同款架构, 但服务的是 wiki markdown 笔记而非 skill 包.

manifesto 兼容性:
- 公理 2 (数据零出端): wiki publish 是员工**主动 push** 的例外, 跟
  catfish_skill_publish 同款 (CATFISH-CENTRAL-MANIFESTO.md line 34-36)
- 公理 3 (中央 0 控制): 没有"中央 push wiki 到员工本机"endpoint, 只能员工 pull
- 公理 4 (API 物理无能): 没有"反向取回员工本机 wiki" endpoint, 也没有强制
  本机卸载已 pull 副本的能力. unpublish 时只在中央 PG 标 stale=true,
  客户端 fetch list 时看到, 自己决定怎么处理本机副本.
"""

__version__ = "0.1.0"
