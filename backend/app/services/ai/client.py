"""OpenAI-compatible chat client.

Works against Ollama locally and vLLM on a GPU host — same wire format, so the
only difference is two environment variables (§57).

This module knows nothing about Instagram, campaigns, or the database. It sends
text and returns text.
"""

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class AiUnavailable(Exception):
    """The model could not be reached or did not answer usefully.

    Deliberately not an SocialFlowError: AI is optional, and its failure must
    never surface as an HTTP error or stop the deterministic path (§57).
    """


@dataclass(frozen=True)
class Completion:
    text: str
    latency_ms: int
    model: str


class AiClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        timeout_seconds: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds))
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def complete(self, system: str, user: str, *, max_tokens: int = 120) -> Completion:
        """One turn. `user` is untrusted text and is never interpolated into
        `system` — keeping them in separate messages is what stops a comment
        rewriting its own instructions."""
        started = time.perf_counter()
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            # Deterministic: this is a classifier, not a writer.
            "temperature": 0,
            "max_tokens": max_tokens,
            "stream": False,
        }

        try:
            response = await self._client.post(f"{self._base_url}/chat/completions", json=payload)
        except httpx.HTTPError as exc:
            raise AiUnavailable(f"Could not reach the model: {type(exc).__name__}") from exc

        if response.status_code >= 400:
            raise AiUnavailable(f"Model returned HTTP {response.status_code}")

        try:
            body = response.json()
            text = body["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise AiUnavailable("Model response was not in the expected shape") from exc

        return Completion(
            text=str(text or ""),
            latency_ms=int((time.perf_counter() - started) * 1000),
            model=str(body.get("model") or self._model),
        )
