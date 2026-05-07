"""
Interface comum a todos os sources.

Convenções:
- Toda source produz `Offer` (modelo genérico) — nada de tipos próprios vazando.
- Cada source é responsável por seu próprio rate limiting, parsing e User-Agent.
- Erros transitórios são repropagados como SourceError pra o orchestrator decidir.
"""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..models import Offer


DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
DEFAULT_HEADERS = {
    "User-Agent": DEFAULT_UA,
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.5",
    "Accept": "text/html,application/xhtml+xml,application/json,application/xml;q=0.9,*/*;q=0.8",
}


class SourceError(RuntimeError):
    """Erro recuperável (rede, parsing) — o orchestrator pode pular esta source."""


class Source(ABC):
    """Interface base. Cada source implementa `fetch()`."""

    name: str = ""           # 'promobit', 'kabum', ...
    display_name: str = ""

    def __init__(self, timeout: float = 20.0,
                 headers: dict[str, str] | None = None) -> None:
        self._client = httpx.Client(
            timeout=timeout,
            headers=headers or DEFAULT_HEADERS,
            follow_redirects=True,
            http2=False,
        )

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self) -> None:
        self._client.close()

    @abstractmethod
    def fetch(self, *, query: str | None = None,
              category: str | None = None,
              max_items: int = 100) -> Iterator[Offer]:
        """Itera ofertas. query/category são opcionais e source-dependentes."""
        ...

    # ---- helpers compartilhados ----

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=15),
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        reraise=True,
    )
    def _get(self, url: str, **params: Any) -> httpx.Response:
        params = {k: v for k, v in params.items() if v is not None}
        r = self._client.get(url, params=params)
        if r.status_code == 429 or r.status_code >= 500:
            r.raise_for_status()  # vai retentar
        if r.status_code >= 400:
            raise SourceError(
                f"{self.name}: GET {url} retornou {r.status_code}: "
                f"{r.text[:200]}"
            )
        return r

    @staticmethod
    def extract_next_data(html: str) -> dict[str, Any]:
        """Extrai e parseia o JSON do <script id='__NEXT_DATA__'> de páginas NextJS."""
        m = re.search(
            r'<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)</script>',
            html,
        )
        if not m:
            raise SourceError("__NEXT_DATA__ não encontrado na página")
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError as e:
            raise SourceError(f"__NEXT_DATA__ JSON inválido: {e}") from e
