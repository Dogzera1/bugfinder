"""
Detector multi-source.

Modelo de pontuação:
  - Filtros duros excluem ofertas (desconto mínimo, faixa de preço, rating ruim).
  - Score = w_discount * discount_score + w_rating * rating_score + w_pop * pop_score
  - Candidato = score >= MIN_SCORE

Nesta Fase 1 ainda não temos preço de referência de revenda no ML — então
o "score" reflete qualidade da oferta no próprio source. Em Fase 2, o detector
vai chamar ML pra estimar margem de revenda real.
"""
from __future__ import annotations

import math
from collections.abc import Iterable

from .config import Config
from .models import Candidate, Offer


def _discount_score(pct: float) -> float:
    """Mapeia desconto (0..100%) pra 0..1, saturando em ~50%."""
    return min(1.0, max(0.0, pct) / 50.0)


def _popularity_score(pop: int) -> float:
    """log10(1+pop) / 3 — satura em ~1000 likes."""
    if pop <= 0:
        return 0.0
    return min(1.0, math.log10(1 + pop) / 3.0)


def _rating_or_neutral(rating: float | None) -> float:
    """Sem rating = neutro (0.5), não penaliza."""
    return 0.5 if rating is None else max(0.0, min(1.0, rating))


def detect_candidates(offers: Iterable[Offer], cfg: Config) -> list[Candidate]:
    out: list[Candidate] = []
    for o in offers:
        # ---- filtros duros ----
        if not o.available:
            continue
        if o.price < cfg.min_price_brl or o.price > cfg.max_price_brl:
            continue
        # exige old_price pra ter desconto verificável
        if not o.has_old_price:
            continue
        if o.discount_pct < cfg.min_discount_pct:
            continue
        # rating: se existe, exige mínimo; se não existe, passa
        if o.rating_score is not None and o.rating_score < cfg.min_rating_score:
            continue

        # ---- score ----
        d = _discount_score(o.discount_pct)
        r = _rating_or_neutral(o.rating_score)
        p = _popularity_score(o.popularity)
        score = (cfg.w_discount * d
                 + cfg.w_rating * r
                 + cfg.w_popularity * p)

        if score < cfg.min_score:
            continue

        reasons = [
            f"desconto {o.discount_pct:.1f}% "
            f"(de R$ {o.old_price:,.2f} por R$ {o.price:,.2f})"
            .replace(",", "X").replace(".", ",").replace("X", "."),
        ]
        if o.rating_score is not None:
            reasons.append(
                f"rating {o.rating_score*100:.0f}% positivo "
                f"({o.rating_count} avaliações)"
            )
        if o.popularity >= 50:
            reasons.append(f"{o.popularity} likes/votos")
        if o.coupon_code:
            reasons.append(f"cupom: {o.coupon_code}")

        out.append(Candidate(offer=o, score=score, reasons=reasons))

    out.sort(key=lambda c: c.score, reverse=True)
    return out
