"""开发方案 §4.5：敏感数据分级路由。

secret（精确配比）只发私有模型；无私有模型时拒绝，绝不静默外发。
"""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.services import llm_router as router
from app.services.llm_router import SensitiveDataRefusal, classify_payload


class TestClassifyPayload:
    def test_exact_weight_pct_is_secret(self):
        assert classify_payload({"ingredients": [{"name": "二甲苯", "weight_pct": 30}]}) == "secret"

    def test_range_weight_pct_is_internal(self):
        assert classify_payload({"ingredients": [{"name": "二甲苯", "weight_pct": "10-15"}]}) == "internal"
        assert classify_payload({"ingredients": [{"name": "二甲苯", "weight_pct": "10～20"}]}) == "internal"
        assert classify_payload({"ingredients": [{"name": "二甲苯", "weight_pct": "<1"}]}) == "internal"

    def test_ingredient_names_without_exact_is_internal(self):
        assert classify_payload({"ingredients": [{"name": "二甲苯"}]}) == "internal"
        assert classify_payload({"formulation_name": "FX-100"}) == "internal"

    def test_unrelated_payload_is_public(self):
        assert classify_payload({"topic": "化学"}) == "public"
        assert classify_payload(None) == "public"

    def test_prompt_with_exact_percentage_is_secret(self):
        assert classify_payload("配方：二甲苯 30%，水 70%") == "secret"
        assert classify_payload("formulation: xylene 30 wt%, water 70 wt%") == "secret"

    def test_prompt_with_names_only_is_internal(self):
        assert classify_payload("配方：二甲苯、水") == "internal"

    def test_prompt_without_formulation_context_is_public(self):
        assert classify_payload("这个方案成功率 95%") == "public"
        assert classify_payload("解释一下 GB 30000 分类") == "public"


class TestResolveRoute:
    def test_secret_without_private_model_refuses(self):
        assert not router.private_llm_configured()
        with pytest.raises(SensitiveDataRefusal):
            router.resolve_route("secret")

    def test_public_and_internal_go_external(self):
        assert router.resolve_route("public")[0] == "external"
        assert router.resolve_route("internal")[0] == "external"

    def test_secret_with_private_model_goes_private(self, monkeypatch):
        monkeypatch.setenv("FORMUNEX_PRIVATE_LLM_ENDPOINT", "http://127.0.0.1:8080/v1")
        monkeypatch.setenv("FORMUNEX_PRIVATE_LLM_API_KEY", "test-key")
        get_settings.cache_clear()
        try:
            assert router.private_llm_configured()
            assert router.resolve_route("secret")[0] == "private"
        finally:
            get_settings.cache_clear()


class TestCallLlmRouting:
    def test_call_llm_secret_without_private_refuses(self):
        from app.services.llm import _call_llm

        assert not router.private_llm_configured()
        with pytest.raises(SensitiveDataRefusal):
            _call_llm("unused", payload={"ingredients": [{"name": "二甲苯", "weight_pct": 30}]})

    def test_call_llm_secret_prompt_without_private_refuses(self):
        from app.services.llm import _call_llm

        with pytest.raises(SensitiveDataRefusal):
            _call_llm("配方：二甲苯 30%，水 70%")

    def test_call_llm_secret_with_private_uses_private_endpoint(self, monkeypatch):
        from app.services import llm as llm_module

        calls: list[dict] = []

        def fake_private(prompt, max_tokens, *, model=None, settings=None):
            calls.append({"prompt": prompt, "model": model})
            return "private-answer"

        monkeypatch.setenv("FORMUNEX_PRIVATE_LLM_ENDPOINT", "http://127.0.0.1:8080/v1")
        monkeypatch.setenv("FORMUNEX_PRIVATE_LLM_API_KEY", "test-key")
        monkeypatch.setattr(router, "call_private_llm", fake_private)
        get_settings.cache_clear()
        try:
            out = llm_module._call_llm(
                "配方：二甲苯 30%，水 70%",
                payload={"ingredients": [{"name": "二甲苯", "weight_pct": 30}]},
            )
            assert out == "private-answer"
            assert len(calls) == 1
        finally:
            get_settings.cache_clear()

    def test_call_private_llm_posts_to_endpoint(self, monkeypatch):
        import httpx

        captured: dict = {}

        class FakeResp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": [{"message": {"content": "ok"}}]}

        def fake_post(url, *, headers, json, timeout):
            captured["url"] = url
            captured["auth"] = headers["Authorization"]
            captured["model"] = json["model"]
            return FakeResp()

        monkeypatch.setattr(httpx, "post", fake_post)
        monkeypatch.setenv("FORMUNEX_PRIVATE_LLM_ENDPOINT", "http://127.0.0.1:8080/v1")
        monkeypatch.setenv("FORMUNEX_PRIVATE_LLM_API_KEY", "test-key")
        monkeypatch.setenv("FORMUNEX_PRIVATE_LLM_MODEL", "qwen3-local")
        get_settings.cache_clear()
        try:
            out = router.call_private_llm("hello")
            assert out == "ok"
            assert captured["url"] == "http://127.0.0.1:8080/v1/chat/completions"
            assert captured["auth"] == "Bearer test-key"
            assert captured["model"] == "qwen3-local"
        finally:
            get_settings.cache_clear()
