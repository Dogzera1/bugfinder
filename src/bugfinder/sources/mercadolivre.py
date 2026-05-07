"""
Source: Mercado Livre — usado primariamente como REFERÊNCIA de preço de revenda
para candidatos descobertos em outros sources.

Requer OAuth2 (vide auth/ml_oauth.py). Sem credenciais o sistema continua mas
o enrichment ML é pulado.

Endpoints usados:
  GET /sites/{site}/search?q=... — busca de listings ativos
  GET /items/{id}                — detalhes de um item específico
  GET /products/{prod_id}/items  — competidores no catálogo (quando aplicável)
"""
from __future__ import annotations

import statistics
from collections.abc import Iterator
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..auth import MLOAuthClient
from ..models import Offer
from .base import Source, SourceError


ML_API = "https://api.mercadolibre.com"


class MercadoLivreReference:
    """
    Cliente ML *autenticado* para lookup de referência (não é um Source ABC
    pleno porque o uso é diferente: dado um produto, devolva preço de revenda).
    """

    def __init__(self, oauth: MLOAuthClient, site_id: str = "MLB",
                 timeout: float = 20.0) -> None:
        self.oauth = oauth
        self.site_id = site_id
        self._client = httpx.Client(
            base_url=ML_API,
            timeout=timeout,
            headers={"Accept": "application/json", "User-Agent": "bugfinder/0.1"},
        )

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._client.close()

    def close(self):
        self._client.close()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        reraise=True,
    )
    def _get(self, path: str, **params: Any) -> dict[str, Any]:
        token = self.oauth.get_access_token()
        params = {k: v for k, v in params.items() if v is not None}
        r = self._client.get(
            path,
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )
        if r.status_code in (401, 403):
            raise SourceError(
                f"ML negou acesso ({r.status_code}) — token pode ter sido "
                f"revogado. Resposta: {r.text[:200]}"
            )
        if r.status_code == 429 or r.status_code >= 500:
            r.raise_for_status()
        if r.status_code >= 400:
            raise SourceError(
                f"ML API GET {path} retornou {r.status_code}: {r.text[:200]}"
            )
        return r.json()

    # ---- lookups ----

    def search(self, query: str, *, limit: int = 20,
               extra_filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"q": query, "limit": min(limit, 50)}
        if extra_filters:
            params.update(extra_filters)
        data = self._get(f"/sites/{self.site_id}/search", **params)
        return data.get("results") or []

    def item(self, item_id: str) -> dict[str, Any]:
        return self._get(f"/items/{item_id}")

    def reference_price(self, query: str, *, top_n: int = 15,
                        condition: str = "new",
                        min_sold: int = 0) -> dict[str, Any] | None:
        """
        Busca query no ML, filtra por estado/sold_quantity, devolve estatísticas:
          { 'median', 'p25', 'p75', 'min', 'max', 'count', 'sample_links': [...] }
        Devolve None se não houver listings suficientes pra estimativa confiável.
        """
        results = self.search(query, limit=top_n,
                              extra_filters={"condition": condition} if condition else None)
        prices: list[float] = []
        sample_links: list[dict[str, Any]] = []
        for r in results:
            price = r.get("price")
            sold = int(r.get("sold_quantity") or 0)
            if price is None or sold < min_sold:
                continue
            try:
                prices.append(float(price))
            except (TypeError, ValueError):
                continue
            if len(sample_links) < 5:
                sample_links.append({
                    "id": r.get("id"),
                    "title": r.get("title"),
                    "price": float(price),
                    "sold_quantity": sold,
                    "permalink": r.get("permalink"),
                })

        if len(prices) < 3:
            return None

        prices_sorted = sorted(prices)
        return {
            "median": float(statistics.median(prices_sorted)),
            "p25": _percentile(prices_sorted, 0.25),
            "p75": _percentile(prices_sorted, 0.75),
            "min": prices_sorted[0],
            "max": prices_sorted[-1],
            "count": len(prices),
            "sample_links": sample_links,
        }


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (len(sorted_values) - 1) * pct
    lo = int(rank)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = rank - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


# ----- Stub Source ABC (não usado em discovery enquanto API exige OAuth) -----

class MercadoLivreSource(Source):
    """
    Implementação Source ABC. Atualmente desabilitada na descoberta porque a API
    pública passou a exigir auth (PolicyAgent). Mantida como placeholder pra
    futuras estratégias (ex: scrape de página pública ou uso de OAuth na busca).
    """
    name = "mercadolivre"
    display_name = "Mercado Livre"

    def fetch(self, *, query=None, category=None, max_items=100) -> Iterator[Offer]:
        raise SourceError(
            "ML como source de descoberta está desabilitado — use como reference "
            "via MercadoLivreReference."
        )
