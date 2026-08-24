"""Tests for LiteLLM/Ollama LLM client adapters."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from rollup.llm_client import (
    CompletionRequest,
    LiteLLMClient,
    LlmExtraMissingError,
    OllamaClient,
    _consume_litellm_stream,
    _parse_litellm_non_stream,
    fetch_ollama_model_names,
    list_ollama_models,
    validate_llm_api_base,
)
from rollup.provider_options import ProviderOptionsError, reject_litellm_ollama_model


def test_reject_litellm_ollama_model() -> None:
    with pytest.raises(ProviderOptionsError, match="routes Ollama through LiteLLM"):
        reject_litellm_ollama_model("ollama/llama3.2", context="test")


def test_validate_llm_api_base_rejects_bad_scheme() -> None:
    with pytest.raises(Exception, match="scheme"):
        validate_llm_api_base("ftp://example.com/v1")


def test_litellm_stream_maps_length_stop() -> None:
    chunks = [
        {"choices": [{"delta": {"content": "hello"}, "finish_reason": None}]},
        {"choices": [{"delta": {"content": None}, "finish_reason": "length"}]},
    ]
    result = _consume_litellm_stream(
        iter(chunks),
        max_output_chars=1000,
        max_wall_seconds=30.0,
        show_progress=False,
        started_at=0.0,
    )
    assert result.text == "hello"
    assert result.stop_reason == "provider_length"


def test_litellm_non_stream_parses_message() -> None:
    data = {
        "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        "usage": {"completion_tokens": 3},
    }
    text, stop, tokens = _parse_litellm_non_stream(data)
    assert text == "ok"
    assert stop == "done"
    assert tokens == 3


def test_ollama_client_non_stream_length_stop() -> None:
    client = OllamaClient(
        ollama_url="http://localhost:11434/api/generate", allow_remote=False
    )
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "response": "x" * 20,
        "done_reason": "length",
    }
    with patch("requests.post", return_value=mock_resp):
        result = client.complete(
            CompletionRequest(
                model="m",
                prompt="p",
                stream=False,
                timeout_seconds=10.0,
            ),
            max_output_chars=100,
        )
    assert result.stop_reason == "provider_length"


def test_litellm_client_missing_extra() -> None:
    client = LiteLLMClient()
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "litellm":
            raise ImportError("no litellm")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fake_import):
        with pytest.raises(LlmExtraMissingError, match="not installed"):
            client.complete(
                CompletionRequest(
                    model="openai/gpt-4o",
                    prompt="hi",
                    stream=False,
                    timeout_seconds=10.0,
                ),
                max_output_chars=100,
            )


@patch("requests.get")
def test_list_ollama_models_returns_sorted_names(mock_get: MagicMock) -> None:
    mock_get.return_value.json.return_value = {
        "models": [{"name": "qwen2.5:7b"}, {"name": "llama3.2:3b"}, {"name": "qwen2.5:7b"}]
    }
    mock_get.return_value.raise_for_status = MagicMock()
    names = list_ollama_models("http://localhost:11434/api/generate")
    assert names == ["llama3.2:3b", "qwen2.5:7b"]
    mock_get.assert_called_once()
    assert "/api/tags" in mock_get.call_args[0][0]


@patch("requests.get")
def test_list_ollama_models_empty_on_error(mock_get: MagicMock) -> None:
    mock_get.side_effect = ConnectionError("down")
    assert list_ollama_models("http://localhost:11434/api/generate") == []
    names, err = fetch_ollama_model_names("http://localhost:11434/api/generate")
    assert names == []
    assert err

