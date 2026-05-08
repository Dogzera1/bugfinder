"""Modelos de domínio. Genéricos por design — qualquer source produz Offer."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class Offer(BaseModel):
    """Uma oferta normalizada vinda de qualquer source."""
    source: str
    external_id: str
    title: str
    url: str
    price: float
    old_price: float | None = None
    currency: str = "BRL"

    store_name: str | None = None
    store_domain: str | None = None

    category: str | None = None
    category_path: list[str] = Field(default_factory=list)

    image: str | None = None
    coupon_code: str | None = None

    rating_score: float | None = None
    rating_count: int = 0
    popularity: int = 0

    available: bool = True

    metadata: dict[str, Any] = Field(default_factory=dict)
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def discount_pct(self) -> float:
        if not self.old_price or self.old_price <= self.price:
            return 0.0
        return (1.0 - self.price / self.old_price) * 100.0

    @property
    def savings_brl(self) -> float:
        return max(0.0, (self.old_price or 0) - self.price)

    @property
    def has_old_price(self) -> bool:
        return self.old_price is not None and self.old_price > self.price


class MarketReference(BaseModel):
    """Estatísticas de preço de revenda para um produto, derivadas do ML."""
    query_used: str
    median: float
    p25: float
    p75: float
    min: float
    max: float
    count: int
    sample_links: list[dict[str, Any]] = Field(default_factory=list)
    search_url: str
    match_confidence: float = 0.0  # 0..1, overlap médio dos tokens-chave


class Viability(BaseModel):
    """Resultado da calculadora de margem de revenda."""
    acquisition_cost: float
    ml_sale_price: float
    ml_fee_pct: float
    ml_fee_brl: float
    fixed_fee_brl: float
    freight_buy: float
    freight_sell: float
    net_revenue: float
    margin_brl: float
    roi_pct: float

    @property
    def is_profitable(self) -> bool:
        return self.margin_brl > 0


class PriceHistoryStats(BaseModel):
    """Resumo do histórico de preço do mesmo (source, external_id)."""
    count: int
    min: float
    max: float
    p10: float
    p25: float
    p50: float
    p75: float
    is_outlier: bool = False  # True se preço atual <= P10 e count >= 5


class Candidate(BaseModel):
    """Uma offer marcada como candidata pelo detector + enrichment opcional."""
    offer: Offer
    score: float
    reasons: list[str] = Field(default_factory=list)

    # Enrichment Fase 2 (opcional — None se ML não está configurado ou não achou match)
    market_reference: MarketReference | None = None
    viability: Viability | None = None

    # Enrichment histórico (opcional — None se < 2 pontos de histórico)
    history: PriceHistoryStats | None = None

    @property
    def discount_pct(self) -> float:
        return self.offer.discount_pct
