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
import ssl as _ssl
from dataclasses import dataclass, field
from typing import Any, Callable, Final

import httpx

from core.app_config import ANTHROPIC_VERSION

LogFn = Callable[[str], None]

_MESSAGES_PATH: Final[str] = "/v1/messages"
_RETRY_STATUS: Final[frozenset[int]] = frozenset({429, 500, 502, 503, 529})


# ---------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------
class AnthropicError(RuntimeError):
    """Base class for all client errors."""


class AnthropicAuthError(AnthropicError):
    """401/403 - missing, invalid, or unauthorized API key. This is the
    signal the UI uses to offer manual mode."""


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


def provider_of(model_id: str) -> str:
    s = (model_id or "").strip().lower()
    if not s:
        return "Other"
    if "/" in s:
        slug = s.split("/", 1)[0]
        if slug in _PROVIDER_SLUGS:
            return _PROVIDER_SLUGS[slug]
        # fall through to hint detection on the remainder
        s = s.split("/", 1)[1]
    for keys, name in _PROVIDER_HINTS:
        if any(k in s for k in keys):
            return name
    return "Other"


def group_models_by_provider(
    models: list[ModelInfo],
) -> list[tuple[str, list[ModelInfo]]]:
    """Group models by provider and sort: Anthropic first, then other
    providers alphabetically, 'Other' last; models sorted by id within
    each provider."""
    groups: dict[str, list[ModelInfo]] = {}
    for m in models:
        groups.setdefault(provider_of(m.id), []).append(m)
    for items in groups.values():
        items.sort(key=lambda m: m.id.lower())

    def _pkey(p: str) -> tuple[int, str]:
        rank = 0 if p == "Anthropic" else (2 if p == "Other" else 1)
        return (rank, p.lower())

    return [(p, groups[p]) for p in sorted(groups, key=_pkey)]


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

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": self.version,
            "content-type": "application/json",
            "accept": "application/json",
        }

    def _url(self) -> str:
        base = (self.base_url or "").rstrip("/")
        return f"{base}{_MESSAGES_PATH}"

    def _log(self, msg: str) -> None:
        if self.on_log:
            try:
                self.on_log(msg)
            except Exception:
                pass

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
                "No LLM API key configured. Open Settings to add one, "
                "or switch to Manual Mode."
            )

        use_thinking = thinking_budget is not None and thinking_budget > 0
        effective_max = int(max_tokens)
        if use_thinking and int(thinking_budget) >= effective_max:
            effective_max = int(thinking_budget) + effective_max
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": effective_max,
            "temperature": 1.0 if use_thinking else float(temperature),
            "messages": [{"role": "user", "content": user}],
        }
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

        last_exc: Exception | None = None
        async with httpx.AsyncClient(
            headers=self._headers(),
            verify=self.ssl_verify,
            timeout=httpx.Timeout(effective_timeout),
        ) as client:
            for attempt in range(self.retry_count + 1):
                try:
                    resp = await client.post(self._url(), json=body)
                except httpx.ConnectError as e:
                    raise AnthropicConnectionError(
                        f"Cannot reach {self._url()} (DNS/firewall): {e!r}"
                    ) from e
                except (_ssl.SSLError, _ssl.SSLCertVerificationError) as e:
                    raise AnthropicConnectionError(
                        f"TLS error reaching the API (proxy interception?): "
                        f"{e!r}. Try Rebuild TLS in Settings."
                    ) from e
                except httpx.TimeoutException as e:
                    last_exc = e
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
                    if resp.status_code == 400 and use_thinking and \
                            "thinking" in detail.lower():
                        raise AnthropicAPIError(
                            f"HTTP 400: Extended thinking not supported by "
                            f"model '{model}'. {detail}"
                        )
                    raise AnthropicAPIError(
                        f"HTTP {resp.status_code}: {detail}"
                    )

                return self._parse(resp)

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
        except Exception:
            pass
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
                    except Exception:
                        pass
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
