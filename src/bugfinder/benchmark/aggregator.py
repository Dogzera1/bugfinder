"""
Orquestra busca de benchmark cross-loja com cache 24h em SQLite.

Hoje só consulta Kabum; quando adicionarmos mais sources (Magalu via
Playwright, p.ex.), entra a paralelização aqui sem mudar o caller.
"""
from __future__ import annotations

import hashlib
import json
import statistics
from datetime import datetime, timedelta, timezone

from ..matcher import clean_title
from ..models import BenchmarkReference
from ..sources.ml_browser import _overlap, _tokenize
from . import kabum_lookup


CACHE_TTL_HOURS = 24


def _query_hash(query: str) -> str:
    return hashlib.sha1(query.encode("utf-8")).hexdigest()[:16]


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


def benchmark_lookup(
    title: str,
    *,
    offer_price: float | None = None,
    storage=None,
    skip_source: str | None = None,
    min_overlap: float = 0.5,
    min_matches: int = 2,
) -> BenchmarkReference | None:
    """
    Busca preços do produto em N lojas externas, aplica overlap-filter, calcula
    percentis. Cache 24h por título normalizado.

    skip_source: nome do source da oferta original. Se for 'kabum', pula Kabum
    (não faz sentido comparar oferta da Kabum contra a própria Kabum).

    Devolve None se:
      - título vazio/curto
      - nenhuma loja retornou ≥ min_matches produtos com overlap ≥ min_overlap
    """
    query = clean_title(title)
    if not query or len(query.split()) < 2:
        return None

    cached = _read_cache(storage, query) if storage else None
    if cached is not None:
        return _attach_real_discount(cached, offer_price)

    # === lookups ===
    sources_used: list[str] = []
    all_prices: list[float] = []
    overlap_sum = 0.0
    overlap_count = 0
    query_tokens = _tokenize(query)

    if (skip_source or "").lower() != "kabum":
        hits = kabum_lookup.search(query)
        # filtra por overlap de tokens (mesma heurística do ml_browser)
        good = []
        for h in hits:
            ov = _overlap(query_tokens, h.name)
            if ov >= min_overlap:
                good.append((h.price, ov))
        if good:
            sources_used.append("kabum")
            for price, ov in good:
                all_prices.append(price)
                overlap_sum += ov
                overlap_count += 1

    if len(all_prices) < min_matches:
        # Persiste miss pra evitar re-buscar a mesma query inutilmente nas próximas 24h
        if storage:
            _write_cache(storage, query, None)
        return None

    all_prices.sort()
    ref = BenchmarkReference(
        query_used=query,
        median_brl=float(statistics.median(all_prices)),
        p25_brl=_percentile(all_prices, 0.25),
        p75_brl=_percentile(all_prices, 0.75),
        count=len(all_prices),
        sources_used=sources_used,
        match_confidence=overlap_sum / overlap_count if overlap_count else 0.0,
    )

    if storage:
        _write_cache(storage, query, ref)

    return _attach_real_discount(ref, offer_price)


def _attach_real_discount(
    ref: BenchmarkReference, offer_price: float | None
) -> BenchmarkReference:
    """Anota real_discount_pct calculado contra a mediana."""
    if offer_price is None or offer_price <= 0 or ref.median_brl <= 0:
        return ref
    rd = (ref.median_brl - offer_price) / ref.median_brl * 100.0
    return ref.model_copy(update={"real_discount_pct": rd})


def classify_real_discount(real_discount_pct: float | None) -> str:
    """Badge categórico pro Telegram. None = sem dados cross-loja."""
    if real_discount_pct is None:
        return "unknown"
    if real_discount_pct >= 25.0:
        return "real_deal"     # 🔥 desconto real
    if real_discount_pct >= 5.0:
        return "soft"          # ⚠ old_price levemente inflado
    return "inflated"          # ❌ old_price totalmente inflado


# ---- cache ----

def _read_cache(storage, query: str) -> BenchmarkReference | None:
    """Le cache se válido (< CACHE_TTL_HOURS); retorna None se miss/expirado."""
    qh = _query_hash(query)
    cutoff = (datetime.now(timezone.utc)
              - timedelta(hours=CACHE_TTL_HOURS)).isoformat(timespec="seconds")
    row = storage.read_benchmark_cache(qh, cutoff)
    if row is None:
        return None
    # Cache de "miss" (count=0) também é válido — evita retentar
    if row["count"] == 0:
        return None
    return BenchmarkReference(
        query_used=row["query_used"],
        median_brl=row["median_brl"],
        p25_brl=row["p25_brl"],
        p75_brl=row["p75_brl"],
        count=row["count"],
        sources_used=json.loads(row["sources_json"]) if row["sources_json"] else [],
        match_confidence=row["match_confidence"] or 0.0,
    )


def _write_cache(storage, query: str, ref: BenchmarkReference | None) -> None:
    qh = _query_hash(query)
    if ref is None:
        # Miss explícito — count=0 marca "tentei e não achou"
        storage.write_benchmark_cache(
            query_hash=qh, query_used=query,
            median=None, p25=None, p75=None, count=0,
            sources=[], match_confidence=0.0,
        )
    else:
        storage.write_benchmark_cache(
            query_hash=qh, query_used=query,
            median=ref.median_brl, p25=ref.p25_brl, p75=ref.p75_brl,
            count=ref.count, sources=ref.sources_used,
            match_confidence=ref.match_confidence,
        )
