"""chat/completions 请求准备流水线 —— 从 app.py 拆出 (8/15)。

`chat_completions` 是一条**线性**流水线: 45 条顶层语句, 逐段加工 `body`,
最后二选一分发到 stream / non-stream。这里搬出其中三段最大的:

    prepare_messages   消息数组的解包与规整 (unwrap_tool_images 等)
    enforce_quota      配额阻断 (超了 raise 429)

# 为什么敢搬 (拆之前逐条查过, 不是看着像就动)

  · **读写集**: 用 AST 算出每块读入哪些局部、写出哪些后面还要用。
    prepare_messages 读 3 写 body;
    enforce_quota 读 5, 写出的东西后面**一个都不用** (纯守卫)。
  · **没有 return**: 三块里一条 return 都没有。这一条最要紧 ——
    块里若有 return, 外提之后就变成"从 helper 返回", 调用方继续往下跑,
    控制流静默改变而测试多半不红。raise 不受影响 (照常向上抛)。
  · **模块级依赖极少**: 每块只用 logger 加一两个 import。

# 一个曾经的误报

Catfish 不负责语义上下文压缩。Hermes 会话路径由 Hermes 的 ContextEngine
负责压缩；网关这里只做确定性的消息准备、工具结果裁剪和最终上下文校验。
"""
from __future__ import annotations

import logging

from . import quota as _quota_module
from .multimodal_tool_unwrap import unwrap_tool_images
from fastapi import HTTPException

# 跟 app.py 同名 —— logging.getLogger 同名返回同一对象, 日志出处不变
logger = logging.getLogger("catfish.gateway")



def prepare_messages(body, model, model_name, effective_user_email):
    """消息数组解包/规整。返回加工后的 body。"""
    if body.get("messages"):
        # 5/8 BL-FIX2 debug: 跑前 dump 一下 messages 概况, 真撞 400 时能定位
        # 是不是 tool message 含图 (该 unwrap) / 还是别的格式问题.
        msgs_in = body["messages"]
        n_total = len(msgs_in) if isinstance(msgs_in, list) else 0
        n_tool = sum(
            1 for m in msgs_in
            if isinstance(m, dict) and m.get("role") == "tool"
        )
        n_tool_with_image_marker = sum(
            1 for m in msgs_in
            if isinstance(m, dict)
            and m.get("role") == "tool"
            and isinstance(m.get("content"), str)
            and ("data:image/" in m.get("content", "") or '"data_uri"' in m.get("content", ""))
        )
        n_user_multipart = sum(
            1 for m in msgs_in
            if isinstance(m, dict)
            and m.get("role") == "user"
            and isinstance(m.get("content"), list)
        )
        logger.info(
            "BL-FIX2 pre-unwrap: total=%d, tool_msgs=%d, tool_with_image_marker=%d, user_multipart=%d",
            n_total, n_tool, n_tool_with_image_marker, n_user_multipart,
        )
        body["messages"] = unwrap_tool_images(body["messages"])

        # BL-FIX42 (5/11): 历史截图折叠 — 防多张截图累积 prompt 5-10MB 让上游
        # 122b 推理 60-120s, 客户端 idle timeout abort. 真根因诊断:
        # tool_with_image_marker=4 → 重组 4 条 → user multipart base64 累积 →
        # latency 108s status=ok 但 Companion fetch idle 超时早已 abort.
        # 修法: 保留最近 1 张图 (LLM 当前必须看), 老图替成文本占位.
        from .tool_archive.image_folder import (  # noqa: PLC0415
            fold_history_images,
            is_folding_enabled,
        )
        if is_folding_enabled():
            body["messages"] = fold_history_images(body["messages"])

        # tool message 长内容兜底: 走 FIX41 硬切 (truncate) — 永远不写 PG.
        #
        # 历史:
        #   - 5/11 BL-Q3-ARCHIVE v1: gateway PG archive + summary worker
        #   - 5/22 BL-CENTRAL-EDGE-TOOL-ARCHIVE Phase 6a: edge 端 (tool-bridge
        #     tool_archive_local.py) 接管, gateway PG 路径 default 禁用
        #   - 5/26 BL-BOUNDARY: db.py 砍 PG, content 100% 员工本机
        #   - 6/7 BL-CATFISH-MANIFESTO + CLEAN-DEAD: 删 env=1 PG 回滚后门 + rm
        #     6 个 dead module (router/db/reader/prompts/features/summary_worker),
        #     archiver.py 缩到只剩 prepare_tool_messages truncate-only wrapper
        #
        # 现在 prepare_tool_messages 物理上**只能 truncate** — 跟 manifesto 公理 4
        # "API surface 物理无能"一致, 没任何回滚到 PG archive 的能力.
        #
        # 真 archive 在 edge 端 tool-bridge tool_archive_local.py 做, gateway 看不到.
        from .tool_archive import prepare_tool_messages  # noqa: PLC0415
        # P3.3.30 (6/12): 传 model_context_window + origin_model → 走动态截,
        #   真要爆 context 才截. 真因 BL-FIX41 老 2K 无脑截破坏多轮 xlsx 修改
        #   (鸿波 6/11 周报反复改 6-7 轮才修对的原因). 异常 fallback 静态截.
        body["messages"] = prepare_tool_messages(
            body["messages"],
            user_email=effective_user_email,  # X-Catfish-User on-behalf-of 模式覆盖
            model_context_window=getattr(model, "context_window", None),
            origin_model=model_name,
        )
    return body


    # ── Quota 阻断 (BL-D9 完整 ship, 5/2 收尾) ────────────────
    # 真实接 chat: 在 LLM 调用前查 quota, 超了直接 429 + friendly message.
    # 估算用 quota.estimate_tokens(prompt 文本拼接), 4 字符 ≈ 1 token, 至少 1000.
    # check_quota 任一维度超 (user_minute / user_day / model_day / dept_day) 即拒.
    #
    # BL-F17 (5/5): X-Catfish-Internal: true → 跳 quota check.
    # 内部 housekeeping (summarizer / proactive / a2a) 是后台总结/起话题/辅助 任务,
    # 不该消耗员工 quota. 员工 1M/天 预算应该给员工**主对话**用, 不是给后台总结烧.
    # 鸿波 5/5 凌晨 explicit: "summarizer 不要去限制用户的 quota 这才是合理的".
    # 注: 不跳 audit log (透明仍要记, 只标 internal=true 区分).
    #
    # BL-PLUGIN-AUTH-FIX (7/27 鸿波): **删了这里的重复赋值**, 直接用函数早期
    # (BL-F17 那处) 判定好的 is_internal_call.
    #
    # 老代码在这里重新 `is_internal_call = request.headers.get(...)`, 注释说是
    # "defensive 冗余, 同值无害". 但**不是同值** — 它只看 header, 丢了 5/21
    # BL-CORS-DEBT-FIX 加的 `?catfish_internal=1` query param fallback. 走 query
    # param 的 caller 在早期判定拿到 True, 到这里被覆盖成 False → quota 照扣.
    # 现在再加 scope 校验, 覆盖会把授权结果一起丢掉, 必须删.
    #
    # 安全性: 早期判定在函数入口后不远 (BL-F17 段), 中间无分支能跳过, local var
    # 不会消失. 万一将来有人删了那处赋值, 这里 NameError 立即暴露 —— 比静默
    # 退化成 False (员工被莫名扣 quota, 没人发现) 好得多. fail-loud.
    return body


def enforce_quota(body, user, model_name, effective_user_email, is_internal_call):
    """配额阻断。超了直接 raise HTTPException(429), 不返回值。"""
    if is_internal_call:
        logger.info(
            "internal call: user=%s model=%s 跳 quota check (X-Catfish-Internal)",
            effective_user_email, model_name,
        )
    else:
        try:
            prompt_text = "\n".join(
                (m.get("content") or "") if isinstance(m.get("content"), str)
                else "" for m in (body.get("messages") or [])
                if isinstance(m, dict)
            )
            estimated = _quota_module.estimate_tokens(prompt_text)
            # BL-AUTH-DECOUPLE-A1 (5/19): quota 归账给 effective_user_email.
            # 普通 user JWT: 跟 user.sub 一致, 行为不变.
            # service token on-behalf-of (hermes-cli): quota 归到 X-Catfish-User
            # 指定的员工, 不归到 client:hermes-cli (后者会让所有员工共享一个 quota).
            qc = _quota_module.check_quota(
                user_email=effective_user_email,
                department=user.department,
                model=model_name,
                est_tokens=estimated,
                role=user.role,  # BL-FIX39 (5/11): admin / sysadmin 跳 quota
            )
            if not qc.allowed:
                friendly = _quota_module.friendly_quota_message(
                    qc, effective_user_email, model_name,
                )
                logger.info(
                    "quota deny: user=%s model=%s dimension=%s current=%d limit=%d",
                    effective_user_email, model_name, qc.dimension, qc.current, qc.limit,
                )
                raise HTTPException(
                    status_code=429,
                    detail={
                        "error": "quota_exceeded",
                        "message": friendly,
                        "dimension": qc.dimension,
                        "current": qc.current,
                        "limit": qc.limit,
                        "reset_at": qc.reset_at,
                        "model": model_name,
                    },
                )
        except HTTPException:
            raise  # 上面 429 直接抛
        except Exception as e:
            # quota 子系统挂了不该影响主流程, 不阻断 chat
            logger.warning("check_quota 调用炸 (allow chat): %s", e)

    # 注意: 这里传 body (而不是预构建的 params) 给 _invoke / _stream
    # 因为 fallback 时换模型, params 里的 api_base / api_key / 等都得重新构建
