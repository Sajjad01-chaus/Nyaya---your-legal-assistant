"""LLM providers behind one interface, swapped by environment variable.

Generation is the only place a hosted API is permitted; retrieval and reranking
run locally on open weights. The Ollama adapter exists because the brief
requires the system to be evaluable without our API key.

Every provider streams. A legal answer is long enough that spinner-then-wall-of-
text is a materially worse experience, and time-to-first-token is the number
users actually feel.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator

import httpx

logger = logging.getLogger(__name__)

# Fallback pause when a provider rate-limits without a Retry-After header.
_RETRY_SECONDS = 20.0


@dataclass(slots=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def cost_usd(self, per_1m_in: float, per_1m_out: float) -> float:
        """Cost per query, which the brief asks us to track and display."""
        return (
            self.prompt_tokens * per_1m_in + self.completion_tokens * per_1m_out
        ) / 1_000_000


@dataclass(slots=True)
class Chunk:
    """One streamed delta. ``usage`` arrives only on the final chunk."""

    text: str = ""
    done: bool = False
    usage: Usage | None = None


class LLMProvider(ABC):
    name: str

    @abstractmethod
    def stream(
        self, system: str, user: str, *, max_tokens: int, temperature: float
    ) -> AsyncIterator[Chunk]: ...

    @abstractmethod
    async def healthy(self) -> bool: ...


class OpenAICompatibleProvider(LLMProvider):
    """Groq, OpenRouter and any other /v1/chat/completions endpoint."""

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 60.0,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.extra_headers = extra_headers or {}

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", **self.extra_headers}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def stream(
        self, system: str, user: str, *, max_tokens: int, temperature: float
    ) -> AsyncIterator[Chunk]:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
            ) as response:
                if response.status_code == 429:
                    # Free tiers rate-limit aggressively, and the evaluation
                    # harness runs dozens of questions back to back. Honour the
                    # provider's own Retry-After rather than guessing.
                    retry_after = response.headers.get("retry-after")
                    delay = float(retry_after) if retry_after else _RETRY_SECONDS
                    body = (await response.aread()).decode("utf-8", "replace")[:200]
                    raise RateLimited(
                        f"{self.name} rate limited; retry in {delay:.0f}s: {body}",
                        retry_after=delay,
                    )
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "replace")[:400]
                    raise LLMError(f"{self.name} returned {response.status_code}: {body}")

                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    if usage := event.get("usage"):
                        yield Chunk(
                            done=True,
                            usage=Usage(
                                prompt_tokens=usage.get("prompt_tokens", 0),
                                completion_tokens=usage.get("completion_tokens", 0),
                            ),
                        )
                        continue
                    for choice in event.get("choices", []):
                        if text := choice.get("delta", {}).get("content"):
                            yield Chunk(text=text)
        yield Chunk(done=True)

    async def healthy(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.get(
                    f"{self.base_url}/models", headers=self._headers()
                )
            return response.status_code < 500
        except Exception:  # noqa: BLE001 - readiness must not raise
            return False


class OllamaProvider(LLMProvider):
    """Local models, so reviewers can run the system without any API key."""

    name = "ollama"

    def __init__(self, *, base_url: str, model: str, timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    async def stream(
        self, system: str, user: str, *, max_tokens: int, temperature: float
    ) -> AsyncIterator[Chunk]:
        payload = {
            "model": self.model,
            "prompt": user,
            "system": system,
            "stream": True,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST", f"{self.base_url}/api/generate", json=payload
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "replace")[:400]
                    raise LLMError(f"ollama returned {response.status_code}: {body}")
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if text := event.get("response"):
                        yield Chunk(text=text)
                    if event.get("done"):
                        yield Chunk(
                            done=True,
                            usage=Usage(
                                prompt_tokens=event.get("prompt_eval_count", 0),
                                completion_tokens=event.get("eval_count", 0),
                            ),
                        )

    async def healthy(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.get(f"{self.base_url}/api/tags")
            return response.status_code == 200
        except Exception:  # noqa: BLE001
            return False


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, *, api_key: str, model: str, timeout: float = 60.0) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.base_url = "https://generativelanguage.googleapis.com/v1beta"

    async def stream(
        self, system: str, user: str, *, max_tokens: int, temperature: float
    ) -> AsyncIterator[Chunk]:
        url = (
            f"{self.base_url}/models/{self.model}:streamGenerateContent"
            f"?alt=sse&key={self.api_key}"
        )
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream("POST", url, json=payload) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "replace")[:400]
                    raise LLMError(f"gemini returned {response.status_code}: {body}")
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        event = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    for candidate in event.get("candidates", []):
                        for part in candidate.get("content", {}).get("parts", []):
                            if text := part.get("text"):
                                yield Chunk(text=text)
                    if meta := event.get("usageMetadata"):
                        yield Chunk(
                            done=True,
                            usage=Usage(
                                prompt_tokens=meta.get("promptTokenCount", 0),
                                completion_tokens=meta.get("candidatesTokenCount", 0),
                            ),
                        )
        yield Chunk(done=True)

    async def healthy(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.get(f"{self.base_url}/models?key={self.api_key}")
            return response.status_code < 500
        except Exception:  # noqa: BLE001
            return False


class LLMError(RuntimeError):
    """Surfaced to the caller as a useful error state, never as a fake answer."""


class RateLimited(LLMError):
    """Distinguishable from a real failure, because it is worth retrying."""

    def __init__(self, message: str, *, retry_after: float) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def build_provider(settings) -> LLMProvider:  # noqa: ANN001 - avoids a circular import
    """Construct the provider named by NYAYA_LLM_PROVIDER."""
    provider = settings.llm_provider

    if provider == "ollama":
        return OllamaProvider(
            base_url=settings.llm_base_url or settings.ollama_base_url,
            model=settings.ollama_model,
            timeout=settings.llm_timeout_s,
        )
    if provider == "gemini":
        return GeminiProvider(
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            timeout=settings.llm_timeout_s,
        )
    if provider == "openrouter":
        return OpenAICompatibleProvider(
            name="openrouter",
            base_url=settings.llm_base_url or "https://openrouter.ai/api/v1",
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            timeout=settings.llm_timeout_s,
            extra_headers={"HTTP-Referer": "https://github.com/", "X-Title": "Nyaya"},
        )
    return OpenAICompatibleProvider(
        name="groq",
        base_url=settings.llm_base_url or "https://api.groq.com/openai/v1",
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        timeout=settings.llm_timeout_s,
    )
