"""
Source: Promobit — agregador brasileiro de ofertas curadas pela comunidade.

Estratégia: scrape do __NEXT_DATA__ embutido na página HTML (NextJS SSR).
Estrutura mapeada em scripts/probe_promobit.py:
  pageProps.serverFeaturedOffers : list[~4]
  pageProps.serverOffers.offers  : list[~14]
  pageProps.serverOffers.after   : cursor de paginação

Cada offer traz:
  offerId, offerTitle, offerSlug, offerPrice, offerOldPrice,
  offerDiscontPercentage, offerCoupon, offerLikes, ratings.{good,bad,great,amazing,all},
  storeName, storeDomain, categoryName, categorySlug
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ..models import Offer
from .base import Source, SourceError


class PromobitSource(Source):
    name = "promobit"
    display_name = "Promobit"

    BASE = "https://www.promobit.com.br"

    def fetch(self, *, query: str | None = None,
              category: str | None = None,
              max_items: int = 100) -> Iterator[Offer]:
        """
        Estratégias suportadas:
          - sem args         -> homepage (recentes)
          - category="X"     -> /categoria/X
          - query="X"        -> /busca/X
        Pagina via cursor `after` retornado pelo Promobit, até max_items.
        """
        if query and category:
            raise SourceError("promobit: passe apenas query OU category, não ambos")

        url = self._build_url(query=query, category=category)
        cursor: str | None = None
        emitted = 0

        while emitted < max_items:
            params = {"after": cursor} if cursor else {}
            r = self._get(url, **params)
            data = self.extract_next_data(r.text)
            page_props = data.get("props", {}).get("pageProps", {})

            featured = page_props.get("serverFeaturedOffers") or []
            offers = (page_props.get("serverOffers") or {}).get("offers") or []
            cursor = (page_props.get("serverOffers") or {}).get("after")

            # Featured só na primeira página
            if cursor is None or emitted == 0:
                for raw in featured:
                    if emitted >= max_items:
                        break
                    parsed = self._parse_offer(raw, featured=True)
                    if parsed:
                        yield parsed
                        emitted += 1

            for raw in offers:
                if emitted >= max_items:
                    break
                parsed = self._parse_offer(raw, featured=False)
                if parsed:
                    yield parsed
                    emitted += 1

            if not cursor or not offers:
                break

    # ---- helpers ----

    def _build_url(self, *, query: str | None, category: str | None) -> str:
        if query:
            slug = query.strip().replace(" ", "-").lower()
            return f"{self.BASE}/busca/{slug}"
        if category:
            return f"{self.BASE}/categoria/{category.strip('/')}"
        return f"{self.BASE}/"

    def _parse_offer(self, raw: dict[str, Any], *, featured: bool) -> Offer | None:
        try:
            offer_id = raw["offerId"]
            title = raw["offerTitle"]
            slug = raw.get("offerSlug") or ""
            price = float(raw["offerPrice"])
        except (KeyError, TypeError, ValueError):
            return None

        # Preços inválidos típicos: STARTING_AT com offerOldPrice=0.01 são posts editoriais
        price_type = raw.get("offerPriceType", "NORMAL")
        if price_type != "NORMAL":
            return None

        old_price_raw = raw.get("offerOldPrice")
        old_price = float(old_price_raw) if old_price_raw else None
        if old_price is not None and old_price <= 0:
            old_price = None

        url = f"{self.BASE}/oferta/{slug}" if slug else f"{self.BASE}/oferta/{offer_id}"

        photo = raw.get("offerPhoto")
        if photo and not photo.startswith("http"):
            photo = f"https://i.promobit.com.br{photo}"

        # rating_score a partir do agregado de votos
        ratings = raw.get("ratings") or {}
        total = int(ratings.get("all") or 0)
        positive = int(ratings.get("great", 0)) + int(ratings.get("amazing", 0)) \
                   + int(ratings.get("good", 0))
        rating_score = (positive / total) if total > 0 else None

        likes = int(raw.get("offerLikes") or 0)
        clicks = int(raw.get("offerClicks") or 0)

        category = raw.get("categoryName") or raw.get("subcategoryName")
        store_name = raw.get("storeName")
        store_domain = raw.get("storeDomain")

        if store_domain == "promobit.com.br":
            # post editorial / "melhores ofertas" — pula
            return None

        meta = {
            "offerId": offer_id,
            "featured": featured,
            "ratings_breakdown": ratings,
            "clicks": clicks,
            "userTypeName": raw.get("userTypeName"),
            "categorySlug": raw.get("categorySlug"),
            "subcategorySlug": raw.get("subcategorySlug"),
            "discount_pct_promobit": raw.get("offerDiscontPercentage"),
            "coupon": raw.get("offerCoupon"),
            "publishedAt": raw.get("offerPublished"),
        }

        return Offer(
            source=self.name,
            external_id=str(offer_id),
            title=title,
            url=url,
            price=price,
            old_price=old_price,
            currency="BRL",
            store_name=store_name,
            store_domain=store_domain,
            category=category,
            image=photo,
            coupon_code=raw.get("offerCoupon") or None,
            rating_score=rating_score,
            rating_count=total,
            popularity=likes,
            available=raw.get("offerStatusName") == "APPROVED",
            metadata=meta,
        )
