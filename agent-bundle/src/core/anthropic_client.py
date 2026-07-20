"""
anthropic_client.py
Minimal async LLM client built on httpx. Speaks the Messages API
protocol (compatible with any provider that implements it). Shares
the application's TLS handling (RuntimeConfig.build_ssl()) so that
enterprise proxies (Zscaler-style TLS interception) work seamlessly.

Endpoint:  POST {base_url}/v1/messages
Headers:   x-api-key, anthropic-version, content-type
Body:      {model, max_tokens, system, messages, temperature, ...}

Public API:
    LLMClient (alias: AnthropicClient) - the connection object
    Exceptions: LLMAuthError, LLMRateLimitError,
                LLMConnectionError, LLMAPIError
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl as _ssl
import threading
import time
from collections.abc import Generator
from dataclasses import dataclass, field
from typing import Any, Callable, Final

import httpx

from core.app_config import ANTHROPIC_VERSION
from core.openai_transport import (
    OpenAIStreamAccumulator,
    openai_message_to_blocks,
    to_openai_messages,
    to_openai_tools,
)

LogFn = Callable[[str], None]

_MESSAGES_PATH: Final[str] = "/v1/messages"
_CHAT_COMPLETIONS_PATH: Final[str] = "/chat/completions"
_RETRY_STATUS: Final[frozenset[int]] = frozenset({429, 500, 502, 503, 529})

# Newer models (e.g. bedrock.anthropic.claude-opus-4-8) reject an explicit
# `temperature` field with HTTP 400 "temperature is deprecated for this model".
# We discover which models reject it at runtime (from the 400) and remember it
# for the rest of the session so every subsequent request omits temperature.
_TEMPERATURE_UNSUPPORTED: set[str] = set()
_TEMP_LOCK = threading.Lock()

_log = logging.getLogger(__name__)


def _wants_temperature(model: str) -> bool:
    """Whether to send an explicit temperature for this model."""
    return model not in _TEMPERATURE_UNSUPPORTED


def _is_temperature_deprecated(detail: str) -> bool:
    """True when a 400 body indicates temperature is not accepted."""
    d = (detail or "").lower()
    return "temperature" in d and (
        "deprecat" in d or "not supported" in d or "unsupported" in d
        or "not allowed" in d or "cannot be" in d
    )

# Appended to the system prompt of the agentic chat so the assistant stays
# on-topic (testing / QA / project work items). Mirrors the desktop client.
_GUARDRAIL_SUFFIX: Final[str] = (
    "\n\n--- GUARDRAILS (STRICTLY ENFORCED) ---\n"
    "You are a specialized testing and quality assurance assistant.\n"
    "You MUST ONLY respond to queries directly related to:\n"
    "- Test case creation, review, or modification\n"
    "- Requirements analysis and test coverage\n"
    "- Bug/defect analysis and reporting\n"
    "- Quality assurance processes and methodologies\n"
    "- Project-specific work items, user stories, or features\n"
    "- Test data generation and test environment setup\n"
    "- Code review from a testing/quality perspective\n\n"
    "If the user asks ANYTHING outside these topics (general knowledge, "
    "coding unrelated to testing, weather, recipes, math problems, general "
    "programming puzzles, personal questions, etc.), respond EXACTLY with:\n"
    '"I can only assist with tasks related to your current project and '
    "testing activities. Please ask me something about your project "
    'requirements, test cases, or quality assurance work."\n\n'
    "Do NOT attempt to be helpful with off-topic requests. Do NOT explain "
    "why you cannot help. Simply give the refusal message above.\n"
    "--- END GUARDRAILS ---"
)


# ---------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------
class AnthropicError(RuntimeError):
    """Base class for all client errors."""


class AnthropicAuthError(AnthropicError):
    """401/403 from the centrally managed AI service."""


class AnthropicRateLimitError(AnthropicError):
    """429 after exhausting retries."""


class AnthropicConnectionError(AnthropicError):
    """DNS/firewall/TLS failure reaching the endpoint."""


class AnthropicAPIError(AnthropicError):
    """Any other non-2xx response, or a malformed body."""


# ---------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------
@dataclass(slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(slots=True)
class CompletionResult:
    text: str = ""
    stop_reason: str = ""
    usage: Usage = field(default_factory=Usage)


@dataclass(slots=True)
class StreamResult:
    """Result from stream_message_with_tools_async(). Holds the content
    blocks (text and/or tool_use) plus the stop_reason so the caller can
    drive the agentic loop."""
    content: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = ""

    @property
    def has_tool_use(self) -> bool:
        return any(b.get("type") == "tool_use" for b in self.content)

    @property
    def text(self) -> str:
        return "".join(
            b.get("text", "") for b in self.content
            if b.get("type") == "text"
        )

    @property
    def tool_calls(self) -> list[dict[str, Any]]:
        return [b for b in self.content if b.get("type") == "tool_use"]


@dataclass(slots=True)
class ModelInfo:
    id: str
    display_name: str = ""

    @property
    def label(self) -> str:
        return f"{self.display_name} ({self.id})" if self.display_name else self.id


# Map a model id to a provider name (for grouping the model dropdown).
_PROVIDER_SLUGS: dict[str, str] = {
    "anthropic": "Anthropic", "openai": "OpenAI", "azure": "Azure",
    "google": "Google", "vertex": "Google", "gemini": "Google",
    "meta": "Meta", "mistralai": "Mistral", "mistral": "Mistral",
    "cohere": "Cohere", "deepseek": "DeepSeek", "qwen": "Qwen",
    "xai": "xAI", "amazon": "Amazon", "bedrock": "Amazon",
    "microsoft": "Microsoft", "databricks": "Databricks",
}
_PROVIDER_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("claude",), "Anthropic"),
    (("gpt", "o1-", "o3-", "o4-", "chatgpt", "davinci", "text-embedding",
      "whisper", "dall-e"), "OpenAI"),
    (("gemini", "palm", "bison", "gecko"), "Google"),
    (("llama", "meta-"), "Meta"),
    (("mistral", "mixtral", "codestral", "ministral", "pixtral"), "Mistral"),
    (("command", "cohere", "rerank", "embed-"), "Cohere"),
    (("deepseek",), "DeepSeek"),
    (("qwen",), "Qwen"),
    (("grok",), "xAI"),
    (("nova", "titan"), "Amazon"),
    (("phi-",), "Microsoft"),
)



# ---------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------
@dataclass(slots=True)
class AnthropicClient:
    api_key: str
    base_url: str
    ssl_verify: Any = True          # value from RuntimeConfig.build_ssl()
    version: str = ANTHROPIC_VERSION
    timeout_sec: float = 120.0
    retry_count: int = 3
    retry_backoff_sec: float = 2.0
    on_log: LogFn | None = None
    # Wire protocol: "anthropic" (POST /v1/messages) or "openai"
    # (POST /chat/completions). Both are served by the GenAI gateway.
    provider_format: str = "anthropic"

    def _headers(self) -> dict[str, str]:
        # The GenAI gateway standardizes on Authorization: Bearer. x-api-key +
        # anthropic-version are additionally sent for the native Anthropic
        # /v1/messages route (harmless on the OpenAI route).
        return {
            "Authorization": f"Bearer {self.api_key}",
            "x-api-key": self.api_key,
            "anthropic-version": self.version,
            "content-type": "application/json",
            "accept": "application/json",
        }

    def _url(self) -> str:
        base = (self.base_url or "").rstrip("/")
        return f"{base}{_MESSAGES_PATH}"

    def _chat_url(self) -> str:
        base = (self.base_url or "").rstrip("/")
        return f"{base}{_CHAT_COMPLETIONS_PATH}"

    @property
    def _is_openai(self) -> bool:
        return (self.provider_format or "").strip().lower() == "openai"

    def _log(self, msg: str) -> None:
        if self.on_log:
            try:
                self.on_log(msg)
            except Exception as e:
                _log.debug("on_log callback failed: %s", e)

    @staticmethod
    def _report_nw_success() -> None:
        try:
            from core.network_status import report_success
            report_success()
        except Exception as e:
            _log.debug("report_success failed: %s", e)

    @staticmethod
    def _report_nw_failure() -> None:
        try:
            from core.network_status import report_failure
            report_failure()
        except Exception as e:
            _log.debug("report_failure failed: %s", e)

    async def complete_async(
        self,
        model: str,
        system: str,
        user: str,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        stop_sequences: list[str] | None = None,
        thinking_budget: int | None = None,
    ) -> CompletionResult:
        """Single-turn completion. Returns the concatenated text content.

        temperature defaults to 0.0 for the most repeatable output the
        API allows (the test-case prompt also enforces determinism).

        When thinking_budget is set (> 0), extended thinking is enabled:
        the model reasons internally before producing output. The API
        requires temperature=1.0 when thinking is active. Thinking
        content blocks are excluded from the returned text; only the
        final text output is returned."""
        if not self.api_key.strip():
            raise AnthropicAuthError(
                "The centrally managed AI service credential is unavailable. "
                "Contact the Testing Toolkit administrator."
            )

        if self._is_openai:
            return await self._complete_async_openai(
                model=model, system=system, user=user,
                max_tokens=max_tokens, temperature=temperature,
                stop_sequences=stop_sequences,
            )

        use_thinking = thinking_budget is not None and thinking_budget > 0
        effective_max = int(max_tokens)
        if use_thinking and int(thinking_budget) >= effective_max:
            effective_max = int(thinking_budget) + effective_max
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": effective_max,
            "messages": [{"role": "user", "content": user}],
        }
        # Omit temperature for models that reject it (see _TEMPERATURE_UNSUPPORTED).
        if _wants_temperature(model):
            body["temperature"] = 1.0 if use_thinking else float(temperature)
        if use_thinking:
            body["thinking"] = {
                "type": "enabled",
                "budget_tokens": int(thinking_budget),
            }
        if system.strip():
            body["system"] = system
        if stop_sequences:
            body["stop_sequences"] = stop_sequences

        effective_timeout = self.timeout_sec
        if use_thinking:
            effective_timeout = max(
                self.timeout_sec, 300.0 + int(thinking_budget) / 30.0
            )

        self._log(
            f"[DEBUG] LLM request -> {model}: "
            f"system={len(system)} chars, user={len(user)} chars, "
            f"max_tokens={effective_max}, "
            f"temp={body.get('temperature', 'omitted')}"
            + (f", thinking={thinking_budget}" if use_thinking else "")
        )
        _t0 = time.perf_counter()

        last_exc: Exception | None = None
        verify = self.ssl_verify
        async with httpx.AsyncClient(
            headers=self._headers(),
            verify=verify,
            timeout=httpx.Timeout(effective_timeout),
        ) as client:
            for attempt in range(self.retry_count + 1):
                try:
                    resp = await client.post(self._url(), json=body)
                except RecursionError:
                    # truststore on Windows/Python 3.12 can infinite-recurse
                    # in verify_mode.__set__. Fall back to default SSL context.
                    self._log(
                        "[WARN] TLS recursion (truststore bug); retrying "
                        "with default SSL context."
                    )
                    self.ssl_verify = _ssl.create_default_context()
                    verify = self.ssl_verify
                    async with httpx.AsyncClient(
                        headers=self._headers(),
                        verify=verify,
                        timeout=httpx.Timeout(effective_timeout),
                    ) as fallback_client:
                        resp = await fallback_client.post(
                            self._url(), json=body
                        )
                except httpx.ConnectError as e:
                    self._report_nw_failure()
                    raise AnthropicConnectionError(
                        f"Cannot reach {self._url()} (DNS/firewall): {e!r}"
                    ) from e
                except (_ssl.SSLError, _ssl.SSLCertVerificationError) as e:
                    self._report_nw_failure()
                    raise AnthropicConnectionError(
                        f"TLS error reaching the API (proxy interception?): "
                        f"{e!r}. Try Rebuild TLS in Settings."
                    ) from e
                except httpx.TimeoutException as e:
                    last_exc = e
                    self._report_nw_failure()
                    self._log(
                        f"[WARN] LLM API timeout after "
                        f"{effective_timeout:.0f}s (attempt "
                        f"{attempt + 1}/{self.retry_count + 1})"
                    )
                    await asyncio.sleep(
                        self.retry_backoff_sec * (attempt + 1)
                    )
                    continue

                if resp.status_code in (401, 403):
                    detail = self._error_detail(resp)
                    raise AnthropicAuthError(
                        f"HTTP {resp.status_code}: the API key was rejected. "
                        f"{detail}"
                    )
                if resp.status_code in _RETRY_STATUS:
                    self._report_nw_failure()
                    last_exc = AnthropicAPIError(
                        f"HTTP {resp.status_code}: {self._error_detail(resp)}"
                    )
                    retry_after = self._retry_after(resp, attempt)
                    self._log(
                        f"[WARN] LLM API HTTP {resp.status_code}; retrying "
                        f"in {retry_after:.1f}s "
                        f"({attempt + 1}/{self.retry_count})"
                    )
                    await asyncio.sleep(retry_after)
                    continue
                if resp.status_code != 200:
                    detail = self._error_detail(resp)
                    # Some models deprecate `temperature`. Learn it, drop the
                    # field, and retry the SAME attempt budget so generation
                    # succeeds instead of failing the whole run.
                    if (
                        resp.status_code == 400
                        and "temperature" in body
                        and _is_temperature_deprecated(detail)
                    ):
                        with _TEMP_LOCK:
                            _TEMPERATURE_UNSUPPORTED.add(model)
                        body.pop("temperature", None)
                        self._log(
                            f"[WARN] {model} rejects temperature; "
                            f"retrying without it."
                        )
                        continue
                    if resp.status_code == 400 and use_thinking and \
                            "thinking" in detail.lower():
                        raise AnthropicAPIError(
                            f"HTTP 400: Extended thinking not supported by "
                            f"model '{model}'. {detail}"
                        )
                    raise AnthropicAPIError(
                        f"HTTP {resp.status_code}: {detail}"
                    )

                self._report_nw_success()
                result = self._parse(resp)
                _elapsed = time.perf_counter() - _t0
                usage = getattr(result, "usage", None)
                _in = getattr(usage, "input_tokens", 0) if usage else 0
                _out = getattr(usage, "output_tokens", 0) if usage else 0
                self._log(
                    f"[DEBUG] LLM response <- {model}: "
                    f"{len(result.text)} chars in {_elapsed:.1f}s, "
                    f"stop={getattr(result, 'stop_reason', '?') or '?'}, "
                    f"tokens in/out={_in}/{_out}"
                    + (f" (attempt {attempt + 1})" if attempt else "")
                )
                return result

        if isinstance(last_exc, AnthropicAPIError) and "429" in str(last_exc):
            raise AnthropicRateLimitError(str(last_exc)) from last_exc
        raise AnthropicAPIError(
            f"Request failed after {self.retry_count} attempts: {last_exc!r}"
        )

    async def list_models_async(self, page_limit: int = 1000) -> list[ModelInfo]:
        """GET {base_url}/v1/models, following pagination. Works against
        the real API and any Anthropic-compatible gateway that implements
        model discovery. Raises the same typed errors as complete."""
        if not self.api_key.strip():
            raise AnthropicAuthError("No LLM API key configured.")
        base = (self.base_url or "").rstrip("/")
        url = f"{base}/v1/models"
        out: list[ModelInfo] = []
        seen: set[str] = set()
        params: dict[str, Any] = {"limit": min(1000, max(1, page_limit))}
        async with httpx.AsyncClient(
            headers=self._headers(), verify=self.ssl_verify,
            timeout=httpx.Timeout(self.timeout_sec),
        ) as client:
            for _ in range(20):  # generous page cap
                try:
                    resp = await client.get(url, params=params)
                except httpx.ConnectError as e:
                    raise AnthropicConnectionError(
                        f"Cannot reach {url} (DNS/firewall): {e!r}"
                    ) from e
                except (_ssl.SSLError, _ssl.SSLCertVerificationError) as e:
                    raise AnthropicConnectionError(
                        f"TLS error reaching the models endpoint: {e!r}. "
                        f"Try Rebuild TLS in Settings."
                    ) from e
                except httpx.TimeoutException as e:
                    raise AnthropicConnectionError(
                        f"Timed out listing models: {e!r}"
                    ) from e
                if resp.status_code in (401, 403):
                    raise AnthropicAuthError(
                        f"HTTP {resp.status_code}: {self._error_detail(resp)}"
                    )
                if resp.status_code != 200:
                    raise AnthropicAPIError(
                        f"HTTP {resp.status_code}: {self._error_detail(resp)}"
                    )
                try:
                    data = resp.json()
                except Exception as e:  # noqa: BLE001
                    raise AnthropicAPIError(
                        f"Malformed models response: {e!r}"
                    ) from e
                if not isinstance(data, dict):
                    raise AnthropicAPIError(
                        "Models response is not a JSON object"
                    )
                for m in data.get("data", []) or []:
                    mid = str(m.get("id", "")).strip()
                    if mid and mid not in seen:
                        seen.add(mid)
                        out.append(ModelInfo(
                            id=mid,
                            display_name=str(m.get("display_name", "")).strip(),
                        ))
                if data.get("has_more") and data.get("last_id"):
                    params["after_id"] = data["last_id"]
                    continue
                break
        return out

    def list_models(self, page_limit: int = 1000) -> list[ModelInfo]:
        return asyncio.run(self.list_models_async(page_limit=page_limit))

    async def verify_async(self, model: str) -> tuple[bool, str]:
        """Validate the API key + base URL with a 1-token completion.
        Returns (ok, detail)."""
        try:
            await self.complete_async(
                model=model,
                system="",
                user="ping",
                max_tokens=1,
                temperature=0.0,
            )
            return True, ""
        except AnthropicAuthError as e:
            return False, str(e)
        except AnthropicConnectionError as e:
            return False, str(e)
        except AnthropicError as e:
            # A non-auth error (e.g. unknown model) still proves the key
            # reached the service; report it but treat connectivity as ok.
            return False, str(e)

    # -- helpers --
    @staticmethod
    def _error_detail(resp: httpx.Response) -> str:
        try:
            data = resp.json()
            err = data.get("error") or {}
            msg = err.get("message") or data.get("message") or ""
            if msg:
                return str(msg)[:400]
        except Exception as e:
            _log.debug("error_detail JSON parse failed: %s", e)
        return (resp.text or "")[:400].replace("\n", " ")

    def _retry_after(self, resp: httpx.Response, attempt: int) -> float:
        try:
            return float(resp.headers.get("retry-after", ""))
        except (TypeError, ValueError):
            return self.retry_backoff_sec * (attempt + 1)

    def _parse(self, resp: httpx.Response) -> CompletionResult:
        try:
            data = resp.json()
        except Exception as e:
            raise AnthropicAPIError(f"Malformed JSON response: {e!r}") from e
        if not isinstance(data, dict):
            raise AnthropicAPIError("API response is not a JSON object")
        blocks = data.get("content") or []
        text_parts: list[str] = []
        for b in blocks:
            if isinstance(b, dict) and b.get("type") == "text":
                text_parts.append(str(b.get("text", "")))
        usage_raw = data.get("usage") or {}
        return CompletionResult(
            text="".join(text_parts),
            stop_reason=str(data.get("stop_reason", "")),
            usage=Usage(
                input_tokens=int(usage_raw.get("input_tokens", 0) or 0),
                output_tokens=int(usage_raw.get("output_tokens", 0) or 0),
            ),
        )

    # Sync convenience wrappers (UI runs these on a worker thread).
    def complete(self, **kwargs: Any) -> CompletionResult:
        return asyncio.run(self.complete_async(**kwargs))

    def verify(self, model: str) -> tuple[bool, str]:
        return asyncio.run(self.verify_async(model))

    def stream_message(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str = "",
        max_tokens: int = 8192,
        temperature: float = 0.7,
    ) -> Generator[str, None, None]:
        """Synchronous streaming completion. Yields text delta strings as
        they arrive from the SSE stream. Blocks the calling thread until
        the stream is exhausted or an error occurs.

        messages: list of {role, content} dicts (content may be a string
        or a list of content blocks for multi-modal input).
        """
        if not self.api_key.strip():
            raise AnthropicAuthError(
                "No LLM API key configured. Open Settings to add one."
            )

        # --- Input pre-filter (layer 1 guardrail) ---
        # Check the last user message; if obviously off-topic, refuse
        # without spending tokens on an API call.
        if messages:
            last_msg = messages[-1]
            if last_msg.get("role") == "user":
                last_content = last_msg.get("content", "")
                if isinstance(last_content, list):
                    text_parts = [
                        b.get("text", "")
                        for b in last_content
                        if isinstance(b, dict) and b.get("type") == "text"
                    ]
                    last_text = " ".join(text_parts)
                else:
                    last_text = str(last_content)
                try:
                    from core.guardrails import check_input_guardrail
                    refusal = check_input_guardrail(last_text)
                except Exception as e:
                    _log.debug("guardrail check failed: %s", e)
                    refusal = None
                if refusal is not None:
                    yield refusal
                    return

        body: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            **({"temperature": float(temperature)} if _wants_temperature(model) else {}),
            "messages": messages,
            "stream": True,
        }
        if system.strip():
            body["system"] = system + _GUARDRAIL_SUFFIX
        else:
            body["system"] = _GUARDRAIL_SUFFIX.lstrip()

        headers = self._headers()
        headers["accept"] = "text/event-stream"

        with httpx.Client(
            headers=headers,
            verify=self.ssl_verify,
            timeout=httpx.Timeout(self.timeout_sec, read=300.0),
        ) as client:
            try:
                stream_ctx = client.stream("POST", self._url(), json=body)
            except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                self._report_nw_failure()
                raise AnthropicConnectionError(
                    f"Cannot reach {self._url()}: {e!r}"
                ) from e
            except (_ssl.SSLError, _ssl.SSLCertVerificationError) as e:
                self._report_nw_failure()
                raise AnthropicConnectionError(
                    f"TLS error: {e!r}. Try Rebuild TLS in Settings."
                ) from e
            with stream_ctx as resp:
                if resp.status_code in (401, 403):
                    resp.read()
                    self._report_nw_failure()
                    raise AnthropicAuthError(
                        f"HTTP {resp.status_code}: API key rejected."
                    )
                if resp.status_code != 200:
                    resp.read()
                    self._report_nw_failure()
                    detail = ""
                    try:
                        data = resp.json()
                        detail = (
                            data.get("error", {}).get("message", "")
                            or resp.text[:400]
                        )
                    except Exception as e:
                        _log.debug("stream_message error-detail JSON parse failed: %s", e)
                        detail = resp.text[:400]
                    raise AnthropicAPIError(
                        f"HTTP {resp.status_code}: {detail}"
                    )

                self._report_nw_success()
                for line in resp.iter_lines():
                    if not line:
                        continue
                    if line.startswith("data: "):
                        payload = line[6:]
                        if payload.strip() == "[DONE]":
                            break
                        try:
                            event = json.loads(payload)
                        except Exception as e:
                            _log.debug("stream_message SSE JSON parse failed: %s", e)
                            continue
                        etype = event.get("type", "")
                        if etype == "content_block_delta":
                            delta = event.get("delta", {})
                            if delta.get("type") == "text_delta":
                                text = delta.get("text", "")
                                if text:
                                    yield text
                        elif etype == "message_stop":
                            break
                        elif etype == "error":
                            err_msg = (
                                event.get("error", {}).get("message", "")
                                or "Stream error"
                            )
                            raise AnthropicAPIError(
                                f"Stream error: {err_msg}"
                            )

    def stream_message_with_tools(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 8192,
        temperature: float = 0.7,
        on_text_delta: Callable[[str], None] | None = None,
    ) -> "StreamResult":
        """Synchronous streaming completion with tool_use support.

        Returns a StreamResult containing content blocks (text and/or
        tool_use). The caller implements the agentic loop:
          1. Call this method.
          2. If result has tool_use blocks, execute them, build
             tool_result messages, append to messages, call again.
          3. Repeat until result has no tool_use blocks (text-only).

        If on_text_delta is provided, it is called with each text chunk
        as it arrives (for live UI streaming).
        """
        if not self.api_key.strip():
            raise AnthropicAuthError(
                "No LLM API key configured. Open Settings to add one."
            )

        body: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            **({"temperature": float(temperature)} if _wants_temperature(model) else {}),
            "messages": messages,
            "stream": True,
        }
        if system.strip():
            body["system"] = system + _GUARDRAIL_SUFFIX
        else:
            body["system"] = _GUARDRAIL_SUFFIX.lstrip()
        if tools:
            body["tools"] = tools

        headers = self._headers()
        headers["accept"] = "text/event-stream"

        content_blocks: list[dict[str, Any]] = []
        current_block: dict[str, Any] | None = None
        text_accum = ""
        tool_input_json = ""
        stop_reason = ""

        with httpx.Client(
            headers=headers,
            verify=self.ssl_verify,
            timeout=httpx.Timeout(self.timeout_sec, read=300.0),
        ) as client:
            with client.stream("POST", self._url(), json=body) as resp:
                if resp.status_code in (401, 403):
                    resp.read()
                    self._report_nw_failure()
                    raise AnthropicAuthError(
                        f"HTTP {resp.status_code}: API key rejected."
                    )
                if resp.status_code != 200:
                    resp.read()
                    self._report_nw_failure()
                    detail = ""
                    try:
                        data = resp.json()
                        detail = (
                            data.get("error", {}).get("message", "")
                            or resp.text[:400]
                        )
                    except Exception as e:
                        _log.debug("stream_message_with_tools error-detail JSON parse failed: %s", e)
                        detail = resp.text[:400]
                    raise AnthropicAPIError(
                        f"HTTP {resp.status_code}: {detail}"
                    )

                self._report_nw_success()
                for line in resp.iter_lines():
                    if not line:
                        continue
                    if not line.startswith("data: "):
                        continue
                    payload = line[6:]
                    if payload.strip() == "[DONE]":
                        break
                    try:
                        event = json.loads(payload)
                    except Exception as e:
                        _log.debug("stream_message_with_tools SSE JSON parse failed: %s", e)
                        continue
                    etype = event.get("type", "")

                    if etype == "content_block_start":
                        cb = event.get("content_block", {})
                        cb_type = cb.get("type", "")
                        if cb_type == "text":
                            current_block = {"type": "text", "text": ""}
                            text_accum = ""
                        elif cb_type == "tool_use":
                            current_block = {
                                "type": "tool_use",
                                "id": cb.get("id", ""),
                                "name": cb.get("name", ""),
                                "input": {},
                            }
                            tool_input_json = ""

                    elif etype == "content_block_delta":
                        delta = event.get("delta", {})
                        dtype = delta.get("type", "")
                        if dtype == "text_delta" and current_block:
                            chunk = delta.get("text", "")
                            text_accum += chunk
                            if on_text_delta and chunk:
                                on_text_delta(chunk)
                        elif dtype == "input_json_delta" and current_block:
                            tool_input_json += delta.get(
                                "partial_json", ""
                            )

                    elif etype == "content_block_stop":
                        if current_block:
                            if current_block["type"] == "text":
                                current_block["text"] = text_accum
                            elif current_block["type"] == "tool_use":
                                try:
                                    current_block["input"] = json.loads(
                                        tool_input_json
                                    ) if tool_input_json else {}
                                except json.JSONDecodeError:
                                    current_block["input"] = {}
                            content_blocks.append(current_block)
                            current_block = None

                    elif etype == "message_delta":
                        delta = event.get("delta", {})
                        stop_reason = delta.get("stop_reason", stop_reason)

                    elif etype == "message_stop":
                        break
                    elif etype == "error":
                        err_msg = (
                            event.get("error", {}).get("message", "")
                            or "Stream error"
                        )
                        raise AnthropicAPIError(
                            f"Stream error: {err_msg}"
                        )

        return StreamResult(
            content=content_blocks,
            stop_reason=stop_reason,
        )

    async def stream_message_with_tools_async(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 8192,
        temperature: float = 0.7,
        on_text_delta: "Callable[[str], Any] | None" = None,
    ) -> "StreamResult":
        """Async streaming completion with tool_use support (SSE).

        Returns a StreamResult with content blocks (text and/or tool_use).
        The caller drives the agentic loop: if the result has tool_use
        blocks, execute them, append tool_result messages, and call again;
        repeat until the model returns text only.

        on_text_delta (sync or async) is invoked with each text chunk as it
        arrives so the route can forward tokens over SSE.
        """
        if not self.api_key.strip():
            raise AnthropicAuthError(
                "No LLM API key configured. Open Settings to add one."
            )

        if self._is_openai:
            return await self._stream_tools_async_openai(
                model=model, messages=messages, system=system, tools=tools,
                max_tokens=max_tokens, temperature=temperature,
                on_text_delta=on_text_delta,
            )

        body: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            **({"temperature": float(temperature)} if _wants_temperature(model) else {}),
            "messages": messages,
            "stream": True,
        }
        body["system"] = (
            (system + _GUARDRAIL_SUFFIX) if system.strip()
            else _GUARDRAIL_SUFFIX.lstrip()
        )
        if tools:
            body["tools"] = tools

        headers = self._headers()
        headers["accept"] = "text/event-stream"

        content_blocks: list[dict[str, Any]] = []
        current_block: dict[str, Any] | None = None
        text_accum = ""
        tool_input_json = ""
        stop_reason = ""

        async def _emit(chunk: str) -> None:
            if not (on_text_delta and chunk):
                return
            res = on_text_delta(chunk)
            if asyncio.iscoroutine(res):
                await res

        async with httpx.AsyncClient(
            headers=headers,
            verify=self.ssl_verify,
            timeout=httpx.Timeout(self.timeout_sec, read=300.0),
        ) as client:
            async with client.stream(
                "POST", self._url(), json=body
            ) as resp:
                if resp.status_code in (401, 403):
                    await resp.aread()
                    raise AnthropicAuthError(
                        f"HTTP {resp.status_code}: API key rejected."
                    )
                if resp.status_code != 200:
                    await resp.aread()
                    raise AnthropicAPIError(
                        f"HTTP {resp.status_code}: {self._error_detail(resp)}"
                    )

                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    payload = line[6:]
                    if payload.strip() == "[DONE]":
                        break
                    try:
                        event = json.loads(payload)
                    except Exception as e:
                        _log.debug("stream_message_with_tools_async SSE JSON parse failed: %s", e)
                        continue
                    etype = event.get("type", "")

                    if etype == "content_block_start":
                        cb = event.get("content_block", {})
                        cb_type = cb.get("type", "")
                        if cb_type == "text":
                            current_block = {"type": "text", "text": ""}
                            text_accum = ""
                        elif cb_type == "tool_use":
                            current_block = {
                                "type": "tool_use",
                                "id": cb.get("id", ""),
                                "name": cb.get("name", ""),
                                "input": {},
                            }
                            tool_input_json = ""

                    elif etype == "content_block_delta":
                        delta = event.get("delta", {})
                        dtype = delta.get("type", "")
                        if dtype == "text_delta" and current_block:
                            chunk = delta.get("text", "")
                            text_accum += chunk
                            await _emit(chunk)
                        elif dtype == "input_json_delta" and current_block:
                            tool_input_json += delta.get("partial_json", "")

                    elif etype == "content_block_stop":
                        if current_block:
                            if current_block["type"] == "text":
                                current_block["text"] = text_accum
                            elif current_block["type"] == "tool_use":
                                try:
                                    current_block["input"] = (
                                        json.loads(tool_input_json)
                                        if tool_input_json else {}
                                    )
                                except json.JSONDecodeError:
                                    current_block["input"] = {}
                            content_blocks.append(current_block)
                            current_block = None

                    elif etype == "message_delta":
                        delta = event.get("delta", {})
                        stop_reason = delta.get("stop_reason", stop_reason)

                    elif etype == "message_stop":
                        break
                    elif etype == "error":
                        err_msg = (
                            event.get("error", {}).get("message", "")
                            or "Stream error"
                        )
                        raise AnthropicAPIError(f"Stream error: {err_msg}")

        return StreamResult(content=content_blocks, stop_reason=stop_reason)

    # -----------------------------------------------------------------
    # OpenAI /chat/completions transport (used when provider_format ==
    # "openai"). Requests/responses are translated to/from the internal
    # Anthropic content-block shape so no consumer code changes.
    # -----------------------------------------------------------------
    async def _complete_async_openai(
        self,
        model: str,
        system: str,
        user: str,
        max_tokens: int,
        temperature: float,
        stop_sequences: list[str] | None,
    ) -> CompletionResult:
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": int(max_tokens),
            **({"temperature": float(temperature)} if _wants_temperature(model) else {}),
            "messages": to_openai_messages(
                system, [{"role": "user", "content": user}]
            ),
        }
        if stop_sequences:
            body["stop"] = stop_sequences

        async with httpx.AsyncClient(
            headers=self._headers(),
            verify=self.ssl_verify,
            timeout=httpx.Timeout(self.timeout_sec),
        ) as client:
            try:
                resp = await client.post(self._chat_url(), json=body)
            except RecursionError:
                self._log(
                    "[WARN] TLS recursion (truststore bug); retrying "
                    "with default SSL context."
                )
                self.ssl_verify = _ssl.create_default_context()
                async with httpx.AsyncClient(
                    headers=self._headers(),
                    verify=self.ssl_verify,
                    timeout=httpx.Timeout(self.timeout_sec),
                ) as fb:
                    resp = await fb.post(self._chat_url(), json=body)
            except httpx.ConnectError as e:
                self._report_nw_failure()
                raise AnthropicConnectionError(
                    f"Cannot reach {self._chat_url()} (DNS/firewall): {e!r}"
                ) from e
            except (_ssl.SSLError, _ssl.SSLCertVerificationError) as e:
                self._report_nw_failure()
                raise AnthropicConnectionError(
                    f"TLS error reaching the API (proxy interception?): {e!r}. "
                    f"Try Rebuild TLS in Settings."
                ) from e

            if resp.status_code in (401, 403):
                self._report_nw_failure()
                raise AnthropicAuthError(
                    f"HTTP {resp.status_code}: the API key was rejected. "
                    f"{self._error_detail(resp)}"
                )
            if resp.status_code != 200:
                self._report_nw_failure()
                raise AnthropicAPIError(
                    f"HTTP {resp.status_code}: {self._error_detail(resp)}"
                )

            self._report_nw_success()
            data = resp.json()
            choices = data.get("choices") or []
            text = ""
            if choices:
                text = (choices[0].get("message", {}) or {}).get("content", "") or ""
            usage_raw = data.get("usage", {}) or {}
            return CompletionResult(
                text=text,
                stop_reason=(
                    choices[0].get("finish_reason", "") if choices else ""
                ),
                usage=Usage(
                    input_tokens=int(usage_raw.get("prompt_tokens", 0) or 0),
                    output_tokens=int(
                        usage_raw.get("completion_tokens", 0) or 0
                    ),
                ),
            )

    async def _stream_tools_async_openai(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]] | None,
        max_tokens: int,
        temperature: float,
        on_text_delta: "Callable[[str], Any] | None",
    ) -> "StreamResult":
        sys_prompt = (
            (system + _GUARDRAIL_SUFFIX) if system.strip()
            else _GUARDRAIL_SUFFIX.lstrip()
        )
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": int(max_tokens),
            **({"temperature": float(temperature)} if _wants_temperature(model) else {}),
            "messages": to_openai_messages(sys_prompt, messages),
            "stream": True,
        }
        oa_tools = to_openai_tools(tools)
        if oa_tools:
            body["tools"] = oa_tools

        headers = self._headers()
        headers["accept"] = "text/event-stream"
        acc = OpenAIStreamAccumulator()

        async def _emit(chunk: str) -> None:
            if not (on_text_delta and chunk):
                return
            res = on_text_delta(chunk)
            if asyncio.iscoroutine(res):
                await res

        async with httpx.AsyncClient(
            headers=headers,
            verify=self.ssl_verify,
            timeout=httpx.Timeout(self.timeout_sec, read=300.0),
        ) as client:
            async with client.stream(
                "POST", self._chat_url(), json=body
            ) as resp:
                if resp.status_code in (401, 403):
                    await resp.aread()
                    self._report_nw_failure()
                    raise AnthropicAuthError(
                        f"HTTP {resp.status_code}: API key rejected."
                    )
                if resp.status_code != 200:
                    await resp.aread()
                    self._report_nw_failure()
                    raise AnthropicAPIError(
                        f"HTTP {resp.status_code}: {self._error_detail(resp)}"
                    )

                self._report_nw_success()
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    payload = line[6:]
                    if payload.strip() == "[DONE]":
                        break
                    try:
                        event = json.loads(payload)
                    except Exception as e:
                        _log.debug("_stream_tools_async_openai SSE JSON parse failed: %s", e)
                        continue
                    choices = event.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get("delta", {}) or {}
                    finish = choice.get("finish_reason", "") or ""
                    chunk = acc.add_delta(delta, finish)
                    if chunk:
                        await _emit(chunk)

        content_blocks, stop_reason = acc.finalize()
        return StreamResult(content=content_blocks, stop_reason=stop_reason)

    async def list_working_models_async(
        self,
        on_progress: Callable[[int, int], None] | None = None,
        concurrency: int = 5,
    ) -> list[ModelInfo]:
        """List the catalog, then probe each model with a 1-token request
        and keep ONLY those that return 200 OK (no auth/4xx/5xx error).
        Order is preserved. Probes run concurrently (bounded)."""
        models = await self.list_models_async()
        if not models:
            return []
        sem = asyncio.Semaphore(max(1, concurrency))
        done = 0
        total = len(models)

        async def _probe(i: int, m: ModelInfo) -> tuple[int, ModelInfo, bool]:
            nonlocal done
            async with sem:
                ok, _detail = await self.verify_async(m.id)
                done += 1
                if on_progress is not None:
                    try:
                        on_progress(done, total)
                    except Exception as e:
                        _log.debug("on_progress callback failed: %s", e)
                return i, m, ok

        gathered = await asyncio.gather(
            *[_probe(i, m) for i, m in enumerate(models)]
        )
        gathered.sort(key=lambda t: t[0])
        return [m for _i, m, ok in gathered if ok]

    def list_working_models(
        self, on_progress: Callable[[int, int], None] | None = None,
    ) -> list[ModelInfo]:
        return asyncio.run(self.list_working_models_async(on_progress))


# Generic aliases (provider-neutral public API)
LLMClient = AnthropicClient
LLMError = AnthropicError
LLMAuthError = AnthropicAuthError
LLMRateLimitError = AnthropicRateLimitError
LLMConnectionError = AnthropicConnectionError
LLMAPIError = AnthropicAPIError
