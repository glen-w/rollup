"""Tests for LiteLLM/Ollama LLM client adapters."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from rollup.llm_client import (
    CompletionRequest,
    LLMClientError,
    LiteLLMClient,
    LlmExtraMissingError,
    OllamaClient,
    ProviderAvailabilityCache,
    _consume_litellm_stream,
    _ollama_model_matches,
    _parse_litellm_non_stream,
    check_ollama_available,
    fetch_ollama_model_names,
    get_llm_client,
    is_local_ollama,
    is_loopback_api_base,
    list_ollama_models,
    validate_llm_api_base,
    validate_ollama_url,
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


def test_get_llm_client_routes_providers() -> None:
    ollama = get_llm_client(
        "ollama",
        ollama_url="http://localhost:11434/api/generate",
        allow_remote=False,
    )
    assert isinstance(ollama, OllamaClient)
    litellm = get_llm_client(
        "litellm",
        ollama_url="http://localhost:11434/api/generate",
        allow_remote=False,
        llm_api_base="http://127.0.0.1:4000",
    )
    assert isinstance(litellm, LiteLLMClient)
    assert litellm.api_base == "http://127.0.0.1:4000"
    with pytest.raises(LLMClientError, match="Unsupported provider"):
        get_llm_client(
            "openai",
            ollama_url="http://localhost:11434/api/generate",
            allow_remote=False,
        )


def test_validate_ollama_url_rejects_remote_and_bad_scheme() -> None:
    with pytest.raises(LLMClientError, match="scheme"):
        validate_ollama_url("ftp://localhost/api/generate", allow_remote=False)
    with pytest.raises(LLMClientError, match="hostname"):
        validate_ollama_url("http:///api/generate", allow_remote=False)
    with pytest.raises(LLMClientError, match="not local"):
        validate_ollama_url(
            "http://ollama.example:11434/api/generate", allow_remote=False
        )
    validate_ollama_url(
        "http://ollama.example:11434/api/generate", allow_remote=True
    )


def test_is_local_ollama_and_loopback_api_base() -> None:
    assert is_local_ollama("http://127.0.0.1:11434/api/generate") is True
    assert is_local_ollama("http://ollama.example/api/generate") is False
    assert is_loopback_api_base(None) is None
    assert is_loopback_api_base("http://localhost:4000/v1") is True
    assert is_loopback_api_base("http://api.example/v1") is False
    assert is_loopback_api_base("not-a-url") is None


def test_ollama_model_matches() -> None:
    assert _ollama_model_matches("llama3.2:3b", "llama3.2:3b") is True
    assert _ollama_model_matches("llama3.2", "llama3.2:3b") is True
    assert _ollama_model_matches("qwen2.5:7b", "llama3.2:3b") is False
    assert _ollama_model_matches("", "llama3.2:3b") is False


@patch("requests.get")
def test_check_ollama_available_mocked(mock_get: MagicMock) -> None:
    mock_get.return_value.json.return_value = {
        "models": [{"name": "llama3.2:3b"}, {"name": "qwen2.5:7b"}]
    }
    mock_get.return_value.raise_for_status = MagicMock()
    ok, msg = check_ollama_available(
        "http://localhost:11434/api/generate", "llama3.2"
    )
    assert ok is True
    assert msg == "ok"
    missing, err = check_ollama_available(
        "http://localhost:11434/api/generate", "missing:1"
    )
    assert missing is False
    assert "not found" in err


def test_fetch_ollama_model_names_rejects_bad_url() -> None:
    names, err = fetch_ollama_model_names("ftp://localhost/api/generate")
    assert names == []
    assert err


def test_provider_availability_cache() -> None:
    cache = ProviderAvailabilityCache(
        ollama_url="http://localhost:11434/api/generate",
        allow_remote=False,
        llm_api_base=None,
    )
    with patch.object(
        cache._clients["ollama"], "check_config", return_value=(True, "ok")
    ) as mock_check:
        assert cache.check("ollama", "llama3.2:3b") == (True, "ok")
        assert cache.check("ollama", "llama3.2:3b") == (True, "ok")
        mock_check.assert_called_once_with("llama3.2:3b")
    ok, err = cache.check("unknown", "m")
    assert ok is False
    assert "Unsupported provider" in err
    with pytest.raises(LLMClientError, match="Unsupported provider"):
        cache.get_client("unknown")
    assert isinstance(cache.get_client("litellm"), LiteLLMClient)


def test_litellm_client_check_config_missing_extra() -> None:
    client = LiteLLMClient()
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "litellm":
            raise ImportError("no litellm")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fake_import):
        ok, msg = client.check_config("openai/gpt-4o")
    assert ok is False
    assert "not installed" in msg


def test_litellm_complete_non_stream_mocked() -> None:
    import sys

    fake = MagicMock()
    fake.completion.return_value = {
        "choices": [{"message": {"content": "hello world"}, "finish_reason": "stop"}],
        "usage": {"completion_tokens": 2},
    }
    with patch.dict(sys.modules, {"litellm": fake}):
        result = LiteLLMClient().complete(
            CompletionRequest(
                model="openai/gpt-4o",
                prompt="hi",
                stream=False,
                timeout_seconds=10.0,
            ),
            max_output_chars=100,
        )
    assert result.text == "hello world"
    assert result.stop_reason == "done"
    assert result.eval_count == 2


def test_ollama_complete_truncates_at_char_cap() -> None:
    client = OllamaClient(
        ollama_url="http://localhost:11434/api/generate", allow_remote=False
    )
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "response": "abcdef",
        "done_reason": "stop",
        "eval_count": 4,
    }
    with patch("requests.post", return_value=mock_resp):
        result = client.complete(
            CompletionRequest(
                model="m",
                prompt="p",
                stream=False,
                timeout_seconds=10.0,
                num_ctx=2048,
                max_tokens=32,
            ),
            max_output_chars=3,
        )
    assert result.text == "abc"
    assert result.stop_reason == "local_char_cap"
    assert result.eval_count == 4

