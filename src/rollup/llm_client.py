"""LLM transport clients: native Ollama and optional LiteLLM."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from time import monotonic, perf_counter
from typing import Any, Literal, Protocol
from urllib.parse import urlparse

from rollup.error_sanitize import sanitize_provider_message
from rollup.provider_errors import is_provider_call_error
from rollup.stream_result import StreamResult, StreamStopReason

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
PROGRESS_INTERVAL_CHARS = 500
PROGRESS_INTERVAL_SECONDS = 2.0
_CLEAR_EOL = "\033[K"

LLMProvider = Literal["ollama", "litellm"]


class LLMClientError(Exception):
    """Configuration or transport error for an LLM client."""


class LlmExtraMissingError(LLMClientError):
    """Raised when a LiteLLM job is configured but rollup[llm] is not installed."""


@dataclass(frozen=True)
class CompletionRequest:
    model: str
    prompt: str
    stream: bool
    timeout_seconds: float
    temperature: float = 0.2
    max_tokens: int | None = None
    num_ctx: int | None = None
    think: bool | str = False
    options: dict[str, object] | None = None
    response_format_json: bool = False


class LLMClient(Protocol):
    provider: str

    def check_config(self, model: str) -> tuple[bool, str]:
        """Transport/config readiness without a paid completion."""

    def complete(
        self,
        request: CompletionRequest,
        *,
        max_output_chars: int,
        max_wall_seconds: float | None = None,
        show_progress: bool = False,
    ) -> StreamResult: ...


def validate_ollama_url(url: str, allow_remote: bool) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise LLMClientError(
            f"Ollama URL scheme {parsed.scheme!r} is not supported; use http or https."
        )
    host = parsed.hostname
    if not host:
        raise LLMClientError("Ollama URL must include a hostname.")
    if host not in LOCAL_HOSTS and not allow_remote:
        raise LLMClientError(
            f"Ollama URL host {host!r} is not local. "
            "Pass --allow-remote-ollama to permit non-loopback endpoints."
        )


def validate_llm_api_base(url: str | None) -> None:
    if not url:
        return
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise LLMClientError(
            f"llm-api-base scheme {parsed.scheme!r} is not supported; use http or https."
        )
    if not parsed.netloc:
        raise LLMClientError("llm-api-base must include a hostname.")


def is_local_ollama(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host in LOCAL_HOSTS


def is_loopback_api_base(api_base: str | None) -> bool | None:
    if not api_base:
        return None
    host = urlparse(api_base.strip()).hostname or ""
    if not host:
        return None
    return host.lower() in LOCAL_HOSTS


def _ollama_model_matches(requested: str, available: str) -> bool:
    if not requested or not available:
        return False
    if available == requested:
        return True
    if ":" not in requested and available.startswith(f"{requested}:"):
        return True
    return False


def fetch_ollama_model_names(
    base_url: str, *, timeout: float = 10.0
) -> tuple[list[str], str | None]:
    """Return sorted unique `/api/tags` names, or `([], error)` on failure."""
    import requests

    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return [], "Ollama URL must use http/https with a hostname."
    tags_url = f"{parsed.scheme}://{parsed.netloc}/api/tags"
    try:
        resp = requests.get(tags_url, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        names: list[str] = []
        seen: set[str] = set()
        for row in data.get("models", []) or []:
            name = str(row.get("name") or "").strip()
            if name and name not in seen:
                seen.add(name)
                names.append(name)
        names.sort(key=str.lower)
        return names, None
    except Exception as exc:
        return [], sanitize_provider_message(str(exc))


def list_ollama_models(base_url: str, *, timeout: float = 2.0) -> list[str]:
    """Local Ollama tags for UI pickers. Empty on any error (never raises)."""
    names, _err = fetch_ollama_model_names(base_url, timeout=timeout)
    return names


def check_ollama_available(base_url: str, model: str) -> tuple[bool, str]:
    names, err = fetch_ollama_model_names(base_url, timeout=10)
    if err is not None:
        return False, err
    if not any(_ollama_model_matches(model, m) for m in names):
        return (
            False,
            f"Model {model!r} not found in Ollama. Available: {names[:5]}",
        )
    return True, "ok"


def _resolve_ollama_done_stop_reason(data: dict[str, object]) -> StreamStopReason:
    done_reason = str(data.get("done_reason", "") or "")
    if done_reason == "length":
        return "provider_length"
    return "done"


def _write_progress(total_chars: int, eval_count: int | None) -> None:
    if eval_count is None:
        sys.stderr.write(f"\r  generating… {total_chars} chars{_CLEAR_EOL}")
    else:
        suffix = f", {eval_count} tokens"
        sys.stderr.write(f"\r  generated{suffix}{_CLEAR_EOL}\n")
    sys.stderr.flush()


def consume_ollama_stream(
    resp,
    *,
    max_output_chars: int,
    max_wall_seconds: float | None = None,
    show_progress: bool = False,
    progress_interval_chars: int = PROGRESS_INTERVAL_CHARS,
    progress_interval_seconds: float = PROGRESS_INTERVAL_SECONDS,
    started_at: float | None = None,
) -> StreamResult:
    """Read an Ollama streaming response with client-side limits."""
    start = started_at if started_at is not None else perf_counter()
    deadline = monotonic() + max_wall_seconds if max_wall_seconds is not None else None
    parts: list[str] = []
    stop_reason: StreamStopReason = "eof_without_done"
    eval_count: int | None = None
    last_progress_chars = 0
    last_progress_at = monotonic()
    should_close = False

    try:
        for line in resp.iter_lines(decode_unicode=True):
            if deadline is not None and monotonic() >= deadline:
                stop_reason = "local_wall_timeout"
                should_close = True
                break
            if line is None or not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                stop_reason = "parse_error"
                should_close = True
                break
            if not isinstance(data, dict):
                stop_reason = "parse_error"
                should_close = True
                break
            if data.get("error"):
                stop_reason = "http_error"
                should_close = True
                break
            chunk = data.get("response", "")
            if chunk:
                parts.append(str(chunk))
                total_chars = sum(len(part) for part in parts)
                if show_progress:
                    now = monotonic()
                    if (
                        total_chars - last_progress_chars >= progress_interval_chars
                        or now - last_progress_at >= progress_interval_seconds
                    ):
                        _write_progress(total_chars, None)
                        last_progress_chars = total_chars
                        last_progress_at = now
                if total_chars >= max_output_chars:
                    stop_reason = "local_char_cap"
                    should_close = True
                    break
            if data.get("done"):
                stop_reason = _resolve_ollama_done_stop_reason(data)
                raw_eval = data.get("eval_count")
                eval_count = int(raw_eval) if raw_eval is not None else None
                if show_progress:
                    total_chars = sum(len(part) for part in parts)
                    _write_progress(total_chars, eval_count)
                break
        else:
            if stop_reason == "eof_without_done":
                should_close = True
    finally:
        if should_close:
            close = getattr(resp, "close", None)
            if callable(close):
                close()

    text = "".join(parts)
    if len(text) > max_output_chars:
        text = text[:max_output_chars]
    return StreamResult(
        text=text,
        stop_reason=stop_reason,
        output_chars=len(text),
        eval_count=eval_count,
        elapsed_seconds=perf_counter() - start,
    )


def _map_litellm_finish_reason(finish_reason: str | None) -> StreamStopReason:
    if finish_reason == "length":
        return "provider_length"
    if finish_reason in (None, ""):
        return "eof_without_done"
    return "done"


def _extract_litellm_usage(data: dict[str, Any]) -> int | None:
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return None
    for key in ("completion_tokens", "total_tokens"):
        raw = usage.get(key)
        if raw is not None:
            try:
                return int(raw)
            except (TypeError, ValueError):
                return None
    return None


def _consume_litellm_stream(
    stream,
    *,
    max_output_chars: int,
    max_wall_seconds: float | None,
    show_progress: bool,
    started_at: float,
) -> StreamResult:
    deadline = monotonic() + max_wall_seconds if max_wall_seconds is not None else None
    parts: list[str] = []
    stop_reason: StreamStopReason = "eof_without_done"
    eval_count: int | None = None
    last_progress_chars = 0
    last_progress_at = monotonic()

    for chunk in stream:
        if deadline is not None and monotonic() >= deadline:
            stop_reason = "local_wall_timeout"
            break
        if not isinstance(chunk, dict):
            stop_reason = "parse_error"
            break
        choices = chunk.get("choices")
        if not isinstance(choices, list) or not choices:
            usage = _extract_litellm_usage(chunk)
            if usage is not None:
                eval_count = usage
            continue
        first = choices[0] if choices else {}
        if not isinstance(first, dict):
            stop_reason = "parse_error"
            break
        delta = first.get("delta") or {}
        if isinstance(delta, dict):
            content = delta.get("content")
            if content:
                parts.append(str(content))
                total_chars = sum(len(part) for part in parts)
                if show_progress:
                    now = monotonic()
                    if (
                        total_chars - last_progress_chars >= PROGRESS_INTERVAL_CHARS
                        or now - last_progress_at >= PROGRESS_INTERVAL_SECONDS
                    ):
                        _write_progress(total_chars, None)
                        last_progress_chars = total_chars
                        last_progress_at = now
                if total_chars >= max_output_chars:
                    stop_reason = "local_char_cap"
                    break
        finish_reason = first.get("finish_reason")
        if finish_reason:
            stop_reason = _map_litellm_finish_reason(str(finish_reason))
        usage = _extract_litellm_usage(chunk)
        if usage is not None:
            eval_count = usage

    text = "".join(parts)
    if len(text) > max_output_chars:
        text = text[:max_output_chars]
    return StreamResult(
        text=text,
        stop_reason=stop_reason,
        output_chars=len(text),
        eval_count=eval_count,
        elapsed_seconds=perf_counter() - started_at,
    )


def _parse_litellm_non_stream(data: dict[str, Any]) -> tuple[str, StreamStopReason, int | None]:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return "", "parse_error", _extract_litellm_usage(data)
    first = choices[0]
    if not isinstance(first, dict):
        return "", "parse_error", _extract_litellm_usage(data)
    message = first.get("message") or {}
    content = ""
    if isinstance(message, dict):
        raw = message.get("content")
        if raw is not None:
            content = str(raw)
    finish_reason = _map_litellm_finish_reason(
        str(first.get("finish_reason") or "") or None
    )
    return content, finish_reason, _extract_litellm_usage(data)


@dataclass
class OllamaClient:
    ollama_url: str
    allow_remote: bool
    provider: str = "ollama"

    def check_config(self, model: str) -> tuple[bool, str]:
        validate_ollama_url(self.ollama_url, self.allow_remote)
        return check_ollama_available(self.ollama_url, model)

    def complete(
        self,
        request: CompletionRequest,
        *,
        max_output_chars: int,
        max_wall_seconds: float | None = None,
        show_progress: bool = False,
    ) -> StreamResult:
        import requests

        validate_ollama_url(self.ollama_url, self.allow_remote)
        started = perf_counter()
        payload_options: dict[str, object] = dict(request.options or {})
        payload_options.setdefault("temperature", request.temperature)
        if request.num_ctx is not None:
            payload_options.setdefault("num_ctx", request.num_ctx)
        if request.max_tokens is not None:
            payload_options.setdefault("num_predict", request.max_tokens)
        payload: dict[str, object] = {
            "model": request.model,
            "prompt": request.prompt,
            "stream": request.stream,
            "options": payload_options,
            "think": request.think,
        }
        if request.response_format_json:
            payload_options.setdefault("format", "json")
        resp = requests.post(
            self.ollama_url,
            json=payload,
            timeout=request.timeout_seconds,
            stream=request.stream,
        )
        resp.raise_for_status()
        wall = max_wall_seconds if max_wall_seconds is not None else request.timeout_seconds
        if request.stream:
            return consume_ollama_stream(
                resp,
                max_output_chars=max_output_chars,
                max_wall_seconds=float(wall),
                show_progress=show_progress,
                started_at=started,
            )
        data = resp.json()
        if data.get("error"):
            return StreamResult(
                text="",
                stop_reason="http_error",
                output_chars=0,
                eval_count=None,
                elapsed_seconds=perf_counter() - started,
            )
        done_reason = str(data.get("done_reason", "") or "")
        stop_reason: StreamStopReason = (
            "provider_length" if done_reason == "length" else "done"
        )
        text = str(data.get("response", ""))
        if len(text) > max_output_chars:
            text = text[:max_output_chars]
            stop_reason = "local_char_cap"
        raw_eval = data.get("eval_count")
        eval_count = int(raw_eval) if raw_eval is not None else None
        return StreamResult(
            text=text,
            stop_reason=stop_reason,
            output_chars=len(text),
            eval_count=eval_count,
            elapsed_seconds=perf_counter() - started,
        )


@dataclass
class LiteLLMClient:
    api_base: str | None = None
    provider: str = "litellm"

    def check_config(self, model: str) -> tuple[bool, str]:
        try:
            import litellm  # noqa: F401
        except ImportError:
            return False, "LiteLLM is not installed; pip install 'rollup[llm]'"
        validate_llm_api_base(self.api_base)
        return True, "ok"

    def complete(
        self,
        request: CompletionRequest,
        *,
        max_output_chars: int,
        max_wall_seconds: float | None = None,
        show_progress: bool = False,
    ) -> StreamResult:
        try:
            import litellm
        except ImportError as exc:
            raise LlmExtraMissingError(
                "LiteLLM is not installed; pip install 'rollup[llm]'"
            ) from exc

        litellm.callbacks = []
        started = perf_counter()
        kwargs: dict[str, object] = {
            "model": request.model,
            "messages": [{"role": "user", "content": request.prompt}],
            "stream": request.stream,
            "temperature": request.temperature,
            "timeout": request.timeout_seconds,
        }
        if request.max_tokens is not None:
            kwargs["max_tokens"] = request.max_tokens
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if request.response_format_json:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            response = litellm.completion(**kwargs)
        except Exception as exc:
            if is_provider_call_error(exc):
                raise
            raise

        wall = max_wall_seconds if max_wall_seconds is not None else request.timeout_seconds
        if request.stream:
            result = _consume_litellm_stream(
                response,
                max_output_chars=max_output_chars,
                max_wall_seconds=float(wall) if wall is not None else None,
                show_progress=show_progress,
                started_at=started,
            )
        else:
            if not isinstance(response, dict):
                return StreamResult(
                    text="",
                    stop_reason="parse_error",
                    output_chars=0,
                    eval_count=None,
                    elapsed_seconds=perf_counter() - started,
                )
            text, stop_reason, eval_count = _parse_litellm_non_stream(response)
            if len(text) > max_output_chars:
                text = text[:max_output_chars]
                stop_reason = "local_char_cap"
            result = StreamResult(
                text=text,
                stop_reason=stop_reason,
                output_chars=len(text),
                eval_count=eval_count,
                elapsed_seconds=perf_counter() - started,
            )
        return result


class ProviderAvailabilityCache:
    """Cache per-provider config/availability checks for one execution."""

    def __init__(
        self,
        *,
        ollama_url: str,
        allow_remote: bool,
        llm_api_base: str | None,
    ) -> None:
        self._clients: dict[str, LLMClient] = {
            "ollama": OllamaClient(ollama_url=ollama_url, allow_remote=allow_remote),
            "litellm": LiteLLMClient(api_base=llm_api_base),
        }
        self._results: dict[tuple[str, str], tuple[bool, str]] = {}

    def check(self, provider: str, model: str) -> tuple[bool, str]:
        key = (provider, model)
        if key not in self._results:
            client = self._clients.get(provider)
            if client is None:
                self._results[key] = (False, f"Unsupported provider {provider!r}")
            else:
                self._results[key] = client.check_config(model)
        return self._results[key]

    def get_client(self, provider: str) -> LLMClient:
        client = self._clients.get(provider)
        if client is None:
            raise LLMClientError(f"Unsupported provider {provider!r}")
        return client


def get_llm_client(
    provider: str,
    *,
    ollama_url: str,
    allow_remote: bool,
    llm_api_base: str | None = None,
) -> LLMClient:
    if provider == "ollama":
        return OllamaClient(ollama_url=ollama_url, allow_remote=allow_remote)
    if provider == "litellm":
        return LiteLLMClient(api_base=llm_api_base)
    raise LLMClientError(f"Unsupported provider {provider!r}")


# Backward-compatible alias used across the codebase.
OllamaError = LLMClientError
