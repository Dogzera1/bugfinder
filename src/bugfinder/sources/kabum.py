"""
Source: Kabum — varejo brasileiro especializado em informática/eletrônicos.

Estratégia: scrape de __NEXT_DATA__ na página de busca/categoria.
URL pattern:
  /busca/<termo>           — busca livre (ex: /busca/notebook)
  /<categoria-path>        — categorias com produtos (ex: /hardware,
                              /computadores, /celular-smartphone)

Campos do produto (catalogServer.data[]):
  code, name, friendlyName, category (str path),
  price (preço normal), priceWithDiscount (preço final),
  oldPrice, discountPercentage, available,
  rating (0-5), ratingCount, manufacturer{id,name},
  image, images, photos, sellerName, offer{name,...}
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from ..models import Offer
from .base import Source, SourceError


# Categorias com layout que retorna catalogServer.data (validadas em probe_kabum.py).
# Páginas como /eletronicos, /games, /promocao são "landing pages" sem catálogo direto.
DEFAULT_CATEGORIES = [
    "hardware",
    "computadores",
    "celular-smartphone",
    "perifericos",
    "tv-video-e-foto",
]


class KabumSource(Source):
    name = "kabum"
    display_name = "Kabum"

    BASE = "https://www.kabum.com.br"

    def fetch(self, *, query: str | None = None,
              category: str | None = None,
              max_items: int = 100) -> Iterator[Offer]:
        # decide quais URLs varrer
        if query:
            slug = query.strip().replace(" ", "-").lower()
            urls = [f"{self.BASE}/busca/{slug}"]
        elif category:
            urls = [f"{self.BASE}/{category.strip('/')}"]
        else:
            urls = [f"{self.BASE}/{c}" for c in DEFAULT_CATEGORIES]

        # divide o orçamento entre as URLs para não comer tudo na primeira
        per_url_budget = max(20, max_items // len(urls)) if len(urls) > 1 else max_items

        emitted = 0
        for url in urls:
            if emitted >= max_items:
                break
            for offer in self._fetch_one(url, max_items=per_url_budget):
                if emitted >= max_items:
                    break
                yield offer
                emitted += 1

    def _fetch_one(self, url: str, *, max_items: int) -> Iterator[Offer]:
        try:
            r = self._get(url)
        except SourceError:
            return
        try:
            data = self.extract_next_data(r.text)
        except SourceError:
            return

        pp = data.get("props", {}).get("pageProps", {})
        inner = pp.get("data")
        if isinstance(inner, str):
            try:
                inner = json.loads(inner)
            except json.JSONDecodeError:
                return
        if not isinstance(inner, dict):
            return

        cs = inner.get("catalogServer") or {}
        products = cs.get("data") or []
        if not isinstance(products, list):
            return

        emitted = 0
        for p in products:
            if emitted >= max_items:
                break
            parsed = self._parse_product(p)
            if parsed:
                yield parsed
                emitted += 1

    @staticmethod
    def _parse_product(p: dict[str, Any]) -> Offer | None:
        try:
            sku = str(p.get("code") or "")
            title = p.get("name")
            if not sku or not title:
                return None

            price_normal = p.get("price")
            price_final = p.get("priceWithDiscount") or price_normal
            if price_final is None:
                return None
            price = float(price_final)
            old_price: float | None = None
            if price_normal is not None and float(price_normal) > price:
                old_price = float(price_normal)
        except (TypeError, ValueError):
            return None

        if not float(price) > 0:
            return None

        # URL canônica
        friendly = p.get("friendlyName") or sku
        link = f"https://www.kabum.com.br/produto/{sku}/{friendly}"

        # imagem
        image = p.get("image") or (
            (p.get("images") or [None])[0]
            if isinstance(p.get("images"), list) else None
        )

        # rating: Kabum usa float 0-5 + ratingCount
        rating_raw = p.get("rating")
        rating_score: float | None = None
        if isinstance(rating_raw, (int, float)) and rating_raw > 0:
            rating_score = max(0.0, min(1.0, float(rating_raw) / 5.0))

        # categoria como path
        cat_str = p.get("category") or ""
        category_path = [c for c in cat_str.split("/") if c]
        category_leaf = category_path[-1] if category_path else None

        # metadata
        manufacturer = p.get("manufacturer") or {}
        offer_meta = p.get("offer") or {}
        meta = {
            "manufacturer": manufacturer.get("name"),
            "sellerName": p.get("sellerName"),
            "discount_pct_kabum": p.get("discountPercentage"),
            "offer_name": offer_meta.get("name"),
            "offer_id": offer_meta.get("id"),
        }

        return Offer(
            source="kabum",
            external_id=sku,
            title=title,
            url=link,
            price=price,
            old_price=old_price,
            currency="BRL",
            store_name="KaBuM!",
            store_domain="kabum.com.br",
            category=category_leaf,
            category_path=category_path,
            image=image,
            rating_score=rating_score,
            rating_count=int(p.get("ratingCount") or 0),
            popularity=int(p.get("ratingCount") or 0),  # proxy: nº de avaliações
            available=bool(p.get("available", True)),
            metadata=meta,
        )
