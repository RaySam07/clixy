"""The brain: the exact request the Claude SDK sends for each kind of work,
captured at the HTTP layer (no key, no network)."""
from __future__ import annotations

import json

import pytest
from PIL import Image

import ai


def _request_body(client: ai.AnthropicClient, **kwargs) -> dict:
    seen = {}

    try:  # the SDK's own HTTP library (httpx2 in current SDKs, httpx before)
        import httpx2 as http
    except ImportError:
        import httpx as http

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["url"] = str(request.url)
        return http.Response(400, json={"type": "error", "error": {"type": "invalid_request_error", "message": "test"}})

    import anthropic
    client.client = anthropic.Anthropic(api_key="sk-ant-test", http_client=http.Client(transport=http.MockTransport(handler)),
                                        max_retries=0, base_url=client.client.base_url)
    with pytest.raises(Exception):
        with client.ask_stream(images=[(Image.new("RGB", (64, 40)), "screen 1")], transcript="hi", history=[], **kwargs) as s:
            list(s.text_deltas())
    return seen["body"]


def test_answers_run_on_sonnet_with_thinking_off_for_a_fast_first_word():
    body = _request_body(ai.AnthropicClient(api_key="sk-ant-test", model_id="anthropic/claude-sonnet-5-5"))
    assert body["model"] == "claude-sonnet-5-5"
    assert body["thinking"] == {"type": "between_tools"}
    assert "output_config" not in body
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_haiku_stays_available_and_fast():
    body = _request_body(ai.AnthropicClient(api_key="sk-ant-test", model_id="anthropic/claude-haiku-5-5"))
    assert body["model"] == "claude-haiku-5-5" and body["thinking"] == {"type": "disabled"}


def test_planning_thinks_a_little_first():
    fast = ai.AnthropicClient(api_key="sk-ant-test", model_id="anthropic/claude-haiku-5-5")
    body = _request_body(ai.thinking_variant(fast), system_prompt="plan")
    assert body["model"] == "claude-sonnet-5-5"
    assert "thinking" not in body, "adaptive thinking is the default; no 'disabled' or 'between_tools'"
    assert body["output_config"] == {"effort": "low"}


def test_through_openrouter_the_effort_field_is_left_out():
    fast = ai.AnthropicClient(api_key="sk-or-v1-test", model_id="anthropic/claude-haiku-5-5",
                              base_url="https://openrouter.ai/api")
    smart = ai.thinking_variant(fast)
    assert smart.model_id == "anthropic/claude-sonnet-5-5" and smart.effort is None


def test_guide_and_sketch_prompts_are_what_the_model_gets(monkeypatch):
    import apps
    import guide
    import sketch
    monkeypatch.setattr(apps, "_apps", {"Spotify": "x"})
    prompt = guide.system_prompt()
    assert "[OPEN:" in prompt and "ms-settings:display" in prompt and "Spotify" in prompt
    assert "never invent a menu" in prompt
    for layout in ("steps", "cycle", "compare", "timeline", "mindmap", "points", "function", "axes3d"):
        assert f'"type": "{layout}"' in sketch.PROMPT
