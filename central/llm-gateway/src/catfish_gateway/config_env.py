"""models.yaml / 库里模型配置的 env 占位符处理.

8/1 按 CLAUDE.md 军规 §1 从 config.py 拆出 —— config.py 是 712 行 (警戒区),
而马上要做的 provider 拆分还要往里加合并逻辑, 必然推过 800 红线。军规 §4:
"生成代码前: 看一下目标文件多大. > 500 行就考虑拆而不是继续堆"。

军规 §3 (re-export): config.py 顶部把这里的符号再导出一次, 老 caller
(`from .config import restore_placeholders` 等) 不受影响。
模块级常量 _ENV_PATHS / _MODEL_ERRORS 跟函数一起搬, 不拆两半 (军规 §3 第 4 条)。

## 这一族在解决什么

models.yaml 里内网地址是故意写成 `api_base: ${INTERNAL_LLM_BASE_QWEN_VISION}`
的 —— 真实地址在 .env 里, 不进配置文件、不进 git。7/30 把模型搬进库时,
播种用的是插值**之后**的对象, 于是真实地址被烤成了库里的字面量:
隔离没了, 而且改 .env 从此不生效 (库里是烤死的旧值, 播种又是
ON CONFLICT DO NOTHING), 不报错不提示。

这里的三件事:
  · interpolate_model_row  读库时插值, 且**单个模型失败不掀翻整份配置**
  · restore_placeholders   把已经烤死的值换回 ${VAR}, 换不回的报出来
  · _ENV_PATHS             只对 upstream.api_base 插值, 不整行递归 ——
                           display_name 会经匿名的 /v1/catalog 发出去
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 从 config 拿, 避免两处各写一份正则 (那正是这两天反复查到的漂移成因)。
# 延迟到函数里 import 会更啰嗦, 而 config → config_env 是单向的, 这里
# 反向 import 会成环 —— 所以正则留在 config.py, 这里只用它。


def _pattern():
    from .config import _ENV_PATTERN  # noqa: PLC0415  (单向依赖, 见上)

    return _ENV_PATTERN


def _interp(value: Any) -> Any:
    from .config import _interpolate_env  # noqa: PLC0415

    return _interpolate_env(value)


# yaml 那条路径 (load_config) 仍是整份递归插值 —— 那份文件是我们自己出厂的,
# 不是客户能在界面上编辑的。
_ENV_PATHS: tuple[tuple[str, ...], ...] = (("upstream", "api_base"),)


def _get_path(d: Any, path: tuple[str, ...]) -> Any:
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def _set_path(d: dict[str, Any], path: tuple[str, ...], value: Any) -> dict[str, Any]:
    out = dict(d)
    cur = out
    for k in path[:-1]:
        cur[k] = dict(cur.get(k) or {})
        cur = cur[k]
    cur[path[-1]] = value
    return out


def interpolate_model_row(row: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """给库里读出来的一行做 env 插值. 返回 (插值后的行, 出错说明或 None).

    ## 为什么要吞掉异常

    _interpolate_env 在变量没设时会 raise。如果让它一路抛上去:

      · 冷启动 —— lifespan 第一行就是 get_config(), 网关直接**起不来**, 全站 502
      · 热态 —— get_config 沿用旧缓存, 于是**整份配置冻结**, 之后所有模型编辑
        都不生效, 界面显示的还是旧值, 只有一行日志

    而触发它只需要管理员在界面上把 api_base 填成 ${打错的变量名}。一个人打错
    一个字, 代价是全站 —— 这个杠杆比例不对。

    所以这里按**单个模型**兜住: 出错的那个模型保留占位符原文 (调用时会明显
    失败, 错误信息里就带着变量名, 自解释), 其余模型照常工作, 同时把说明
    交给上层显示在「模型」页上。
    """
    out = row
    for path in _ENV_PATHS:
        raw = _get_path(out, path)
        if not isinstance(raw, str) or not _pattern().search(raw):
            continue
        try:
            out = _set_path(out, path, _interp(raw))
        except RuntimeError as e:
            return row, f"{'.'.join(path)}: {e}"
    return out, None


# 上一次组装时发现的模型配置问题 (模型名 → 说明)。
# 给 /api/admin/models 显示用 —— 否则"这个模型为什么不工作"没有任何线索。
_MODEL_ERRORS: dict[str, str] = {}


def set_model_config_errors(errs: dict[str, str]) -> None:
    """由 config._assemble_config 在每次组装后调用.

    ⚠ 军规 §3 第 4 条: 模块级全局要跟函数一起搬, 别拆两半。
    这个 dict 的写在 config.py (组装时), 读在这里 —— 拆开之后
    `globals()["_MODEL_ERRORS"] = ...` 会写到 config.py 的命名空间去,
    而这里读到的永远是空的, 而且**不报错**。所以必须走显式 setter。
    """
    global _MODEL_ERRORS
    _MODEL_ERRORS = dict(errs)


def model_config_errors() -> dict[str, str]:
    """上一次组装配置时, 哪些模型的 env 占位符没解析成功."""
    return dict(_MODEL_ERRORS)


def restore_placeholders(
    stored: dict[str, Any], raw: dict[str, Any]
) -> tuple[dict[str, Any], str | None]:
    """把库里已经烤死的值换回 ${VAR} 占位符. 返回 (新的行, 存疑说明或 None).

    比对 _ENV_PATHS 上的每个路径: raw (yaml 原文) 是含 ${VAR} 的字符串, 而
    stored 在同一处正好等于它插值后的结果 → 说明是播种时烤进去的, 换回占位符。

    ## 对不上的时候为什么要报出来而不是静默跳过

    第一版是"对不上就当客户手填的, 不动"。但**这个 bug 的发现路径恰恰是
    「IT 改了 .env 但不生效」** —— 到打补丁的时候, 库里是旧地址、.env 里是
    新地址, 两者必然对不上, 于是回迁在最需要它的场景里静默失效。

    函数没有能力区分"人手填的"和"env 后来改过了"。所以不猜: 换不回的时候
    把这个情况说出来, 让运维自己看一眼。静默跳过等于把问题埋回去。
    """
    if not isinstance(stored, dict) or not isinstance(raw, dict):
        return stored, None
    out = stored
    note: str | None = None
    for path in _ENV_PATHS:
        raw_v = _get_path(raw, path)
        cur = _get_path(out, path)
        if not isinstance(raw_v, str) or not _pattern().search(raw_v):
            continue
        if not isinstance(cur, str) or _pattern().search(cur):
            continue  # 库里本来就是占位符, 已经是对的
        try:
            expanded = _interp(raw_v)
        except RuntimeError:
            note = f"{'.'.join(path)}: yaml 里是 {raw_v}, 但那个环境变量现在没设, 无法判断"
            continue
        if cur == expanded:
            out = _set_path(out, path, raw_v)
        else:
            note = (
                f"{'.'.join(path)}: yaml 里是 {raw_v}, 库里是一个写死的值, "
                f"且跟该变量当前的值对不上 —— 可能是 .env 后来改过 (那么库里这份是"
                f"过期的、且改 .env 不会生效), 也可能是有人在界面上手填的。"
                f"请人工确认一次。"
            )
    return out, note
