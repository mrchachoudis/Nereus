"""Connector protocol plus shared HTTP plumbing: rate limit, cache, backoff.

Every source is one connector. A connector takes a :class:`ResearchPlan` and
returns typed records, or ``[]`` when the source has nothing — it never
fabricates a record (DESIGN_PROMPT §8). Cross-cutting concerns (token-bucket
rate limiting, on-disk response caching keyed by request hash, retry with
exponential backoff on transient errors) live in :class:`HttpConnector` so
individual connectors stay small.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import httpx
from pydantic import BaseModel

from fra.models import ResearchPlan

logger = logging.getLogger(__name__)


class ConnectorError(RuntimeError):
    """Raised when a source fails in a way the orchestrator should log as a gap."""


@dataclass
class ConnectorConfig:
    """Resolved configuration for one connector (from ``connectors.yaml``)."""

    name: str
    domain: str
    enabled: bool = True
    base_url: str = ""
    rate_limit_per_s: float = 2.0
    options: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Connector(Protocol):
    """The interface every source connector implements."""

    name: str
    domain: str

    async def fetch(self, plan: ResearchPlan) -> Sequence[BaseModel]:
        """Return typed records for ``plan`` (possibly empty), never fabricated."""
        ...


class TokenBucket:
    """Simple async token-bucket rate limiter (``rate`` tokens/second, burst=rate)."""

    def __init__(self, rate_per_s: float) -> None:
        self._rate = max(rate_per_s, 0.001)
        self._capacity = max(rate_per_s, 1.0)
        self._tokens = self._capacity
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(
                    self._capacity, self._tokens + (now - self._updated) * self._rate
                )
                self._updated = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self._rate
                await asyncio.sleep(wait)


class DiskCache:
    """Content-addressed JSON response cache so re-runs are cheap and reproducible."""

    def __init__(self, cache_dir: str | Path, namespace: str) -> None:
        self._dir = Path(cache_dir) / "connectors" / namespace
        self._dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _key(url: str, params: dict[str, Any] | None) -> str:
        blob = json.dumps({"url": url, "params": params or {}}, sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]

    def get(self, url: str, params: dict[str, Any] | None) -> Any | None:
        path = self._dir / f"{self._key(url, params)}.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                logger.warning("corrupt cache entry %s; ignoring", path)
        return None

    def put(self, url: str, params: dict[str, Any] | None, value: Any) -> None:
        path = self._dir / f"{self._key(url, params)}.json"
        path.write_text(json.dumps(value), encoding="utf-8")


class HttpConnector:
    """Base for HTTP-backed connectors.

    Subclasses implement :meth:`fetch`. They call :meth:`_get_json` for network
    access, which applies rate limiting, disk caching, and backoff. The httpx
    client is injectable so tests can supply a ``MockTransport``.
    """

    domain: str = "unknown"

    def __init__(
        self,
        config: ConnectorConfig,
        *,
        cache_dir: str | Path = ".fra_cache",
        client: httpx.AsyncClient | None = None,
        max_retries: int = 3,
    ) -> None:
        self.name = config.name
        self.domain = config.domain
        self.config = config
        self._bucket = TokenBucket(config.rate_limit_per_s)
        self._cache = DiskCache(cache_dir, config.name)
        self._client = client
        self._own_client = client is None
        self._max_retries = max_retries

    async def _client_get(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
        return self._client

    async def aclose(self) -> None:
        if self._own_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        use_cache: bool = True,
    ) -> Any:
        """GET ``url`` and return parsed JSON, with cache + rate-limit + backoff."""
        if use_cache:
            cached = self._cache.get(url, params)
            if cached is not None:
                logger.debug("%s cache hit %s", self.name, url)
                return cached

        client = await self._client_get()
        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            await self._bucket.acquire()
            try:
                resp = await client.get(url, params=params, headers=headers)
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise httpx.HTTPStatusError(
                        f"transient {resp.status_code}", request=resp.request, response=resp
                    )
                resp.raise_for_status()
                data = resp.json()
                if use_cache:
                    self._cache.put(url, params, data)
                return data
            except (httpx.HTTPStatusError, httpx.TransportError, json.JSONDecodeError) as exc:
                last_exc = exc
                backoff = min(2.0**attempt * 0.5, 10.0)
                logger.warning(
                    "%s GET %s failed (attempt %d/%d): %s; backing off %.1fs",
                    self.name,
                    url,
                    attempt,
                    self._max_retries,
                    exc,
                    backoff,
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(backoff)
        raise ConnectorError(f"{self.name}: all retries failed for {url}: {last_exc}")

    async def fetch(self, plan: ResearchPlan) -> Sequence[BaseModel]:  # pragma: no cover
        raise NotImplementedError
