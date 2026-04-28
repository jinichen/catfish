"""multimodal_guard 单测.

覆盖:
  - has_multimodal_content: 各种 messages 形态 (str / list / 含图 / 不含图)
  - pick_vision_alternative: 同 tier / 跨 tier / 找不到
  - route_to_vision_if_needed: 含图+非vision 自动 reroute / 含图+vision 不动 / 不含图不动
"""
from __future__ import annotations

from catfish_gateway.config import Config, ModelConfig, UpstreamConfig
from catfish_gateway.multimodal_guard import (
    has_multimodal_content,
    pick_vision_alternative,
    route_to_vision_if_needed,
)


def _make_model(
    name: str,
    tier: str = "private",
    supports_vision: bool = False,
    mode: str = "chat",
) -> ModelConfig:
    return ModelConfig(
        name=name,
        tier=tier,
        display_name=name,
        mode=mode,
        upstream=UpstreamConfig(
            provider="openai",
            model=name,
            base_url="http://example.com",
            api_key_env="DUMMY",
        ),
        supports_vision=supports_vision,
    )


def _make_config(*models: ModelConfig) -> Config:
    return Config(version=1, models=list(models))


# ============================================================
# has_multimodal_content
# ============================================================


class TestHasMultimodalContent:
    def test_empty(self) -> None:
        assert not has_multimodal_content([])
        assert not has_multimodal_content(None)

    def test_text_only_string_content(self) -> None:
        msgs = [{"role": "user", "content": "你好"}]
        assert not has_multimodal_content(msgs)

    def test_text_only_list_content(self) -> None:
        msgs = [
            {
                "role": "user",
                "content": [{"type": "text", "text": "你好"}],
            }
        ]
        assert not has_multimodal_content(msgs)

    def test_image_url(self) -> None:
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "看图"},
                    {"type": "image_url", "image_url": {"url": "data:..."}},
                ],
            }
        ]
        assert has_multimodal_content(msgs)

    def test_image_data_alt_name(self) -> None:
        """Gemini 原生用 image_data, 也得能识别"""
        msgs = [
            {
                "role": "user",
                "content": [{"type": "image_data", "data": "..."}],
            }
        ]
        assert has_multimodal_content(msgs)

    def test_image_short_name(self) -> None:
        """有些 provider 直接 type=image"""
        msgs = [
            {
                "role": "user",
                "content": [{"type": "image", "image": {"url": "..."}}],
            }
        ]
        assert has_multimodal_content(msgs)

    def test_input_image_anthropic_style(self) -> None:
        msgs = [
            {
                "role": "user",
                "content": [{"type": "input_image", "source": {}}],
            }
        ]
        assert has_multimodal_content(msgs)

    def test_image_in_assistant_msg_also_counts(self) -> None:
        """assistant 把图返还也算 — 上游一样会撞"""
        msgs = [
            {"role": "user", "content": "看图"},
            {
                "role": "assistant",
                "content": [{"type": "image_url", "image_url": {"url": "..."}}],
            },
        ]
        assert has_multimodal_content(msgs)

    def test_invalid_message_safe(self) -> None:
        """msg 不是 dict 不应抛"""
        msgs = ["not a dict", {"role": "user"}, {"role": "user", "content": None}]
        assert not has_multimodal_content(msgs)


# ============================================================
# pick_vision_alternative
# ============================================================


class TestPickVisionAlternative:
    def test_same_tier_priority(self) -> None:
        """同 tier 优先 (private 数据不该走 public)"""
        main = _make_model("main", tier="private", supports_vision=False)
        priv_vision = _make_model("priv-v", tier="private", supports_vision=True)
        pub_vision = _make_model("pub-v", tier="public", supports_vision=True)
        cfg = _make_config(main, pub_vision, priv_vision)

        alt = pick_vision_alternative(cfg, main)
        assert alt is not None
        assert alt.name == "priv-v"

    def test_cross_tier_fallback(self) -> None:
        """同 tier 没 vision → 跨 tier 兜底"""
        main = _make_model("main", tier="private", supports_vision=False)
        pub_vision = _make_model("pub-v", tier="public", supports_vision=True)
        cfg = _make_config(main, pub_vision)

        alt = pick_vision_alternative(cfg, main)
        assert alt is not None
        assert alt.name == "pub-v"

    def test_no_vision_at_all(self) -> None:
        """catalog 里没 vision 模型 → None (上层 fallback 到不 reroute)"""
        main = _make_model("main", supports_vision=False)
        other = _make_model("other", supports_vision=False)
        cfg = _make_config(main, other)

        assert pick_vision_alternative(cfg, main) is None

    def test_skip_self(self) -> None:
        """current 是 vision 模型自己时不应推荐自己"""
        v = _make_model("v", supports_vision=True)
        cfg = _make_config(v)
        # 假设 caller 还是问 (虽然不会触发), 不返回自己
        assert pick_vision_alternative(cfg, v) is None

    def test_skip_embedding_models(self) -> None:
        """embedding 模型 supports_vision=True 也不能用 (mode 不对)"""
        main = _make_model("main", supports_vision=False)
        emb_with_vision = _make_model(
            "emb", supports_vision=True, mode="embedding"
        )
        cfg = _make_config(main, emb_with_vision)

        assert pick_vision_alternative(cfg, main) is None


# ============================================================
# route_to_vision_if_needed (主入口)
# ============================================================


class TestRouteToVisionIfNeeded:
    def _multimodal_body(self) -> dict:
        return {
            "model": "main",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "看屏幕"},
                        {"type": "image_url", "image_url": {"url": "data:..."}},
                    ],
                }
            ],
        }

    def _text_body(self) -> dict:
        return {
            "model": "main",
            "messages": [{"role": "user", "content": "今天天气怎么样"}],
        }

    def test_text_only_no_reroute(self) -> None:
        main = _make_model("main", supports_vision=False)
        v = _make_model("v", supports_vision=True)
        cfg = _make_config(main, v)
        body = self._text_body()

        new_model, hint = route_to_vision_if_needed(body, cfg, main)
        assert new_model is None
        assert hint is None
        assert body["model"] == "main"  # 不动

    def test_image_with_vision_model_no_reroute(self) -> None:
        """已经在 vision 模型了, 不动"""
        v = _make_model("v", supports_vision=True)
        cfg = _make_config(v)
        body = self._multimodal_body()
        body["model"] = "v"

        new_model, hint = route_to_vision_if_needed(body, cfg, v)
        assert new_model is None

    def test_image_with_main_reroutes(self) -> None:
        """含图 + 主力非 vision → 自动 reroute"""
        main = _make_model("main", tier="private", supports_vision=False)
        priv_v = _make_model("priv-v", tier="private", supports_vision=True)
        cfg = _make_config(main, priv_v)
        body = self._multimodal_body()

        new_model, hint = route_to_vision_if_needed(body, cfg, main)
        assert new_model is not None
        assert new_model.name == "priv-v"
        assert body["model"] == "priv-v"  # in-place 改了
        assert hint is not None
        assert "priv-v" in hint
        assert "vision" in hint.lower() or "图" in hint

    def test_image_no_alternative_no_reroute(self) -> None:
        """含图 + 找不到 vision 替代 → 不动 (让上游自己 400)"""
        main = _make_model("main", supports_vision=False)
        cfg = _make_config(main)
        body = self._multimodal_body()

        new_model, hint = route_to_vision_if_needed(body, cfg, main)
        assert new_model is None
        assert body["model"] == "main"

    def test_reroute_prefers_same_tier(self) -> None:
        """private 主力 → reroute 优先 private vision (不要 public, 隐私)"""
        main = _make_model("main", tier="private", supports_vision=False)
        priv_v = _make_model("priv-v", tier="private", supports_vision=True)
        pub_v = _make_model("pub-v", tier="public", supports_vision=True)
        cfg = _make_config(main, pub_v, priv_v)
        body = self._multimodal_body()

        new_model, hint = route_to_vision_if_needed(body, cfg, main)
        assert new_model is not None
        assert new_model.name == "priv-v"  # 同 tier 优先
        assert new_model.tier == "private"
