"""Metadata-only logging. NEVER log prompt/completion content."""

from __future__ import annotations

import json
import logging
import time

logger = logging.getLogger("catfish.metrics")


def log_request_metadata(
    *,
    user: str,
    model: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    latency_ms: float = 0.0,
    status: str = "ok",
    error: str = "",
) -> None:
    """Emit a single structured log line.

    Includes ONLY:
      - who (user id)
      - what model
      - token counts
      - latency
      - status / error code

    Explicitly EXCLUDES:
      - prompt content
      - completion content
      - tool call arguments
      - raw headers
    """
    record = {
        "ts": int(time.time()),
        "type": "llm_request",
        "user": user,
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "latency_ms": round(latency_ms, 1),
        "status": status,
    }
    if error:
        # Truncate to avoid accidentally leaking upstream prompt echoes in errors
        record["error"] = error[:200]

    logger.info(json.dumps(record, ensure_ascii=False))
