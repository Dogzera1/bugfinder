"""
Kabum search lookup via httpx + JSON-LD.

Kabum entrega 3 blocos `application/ld+json` na página de busca (BreadcrumbList,
FAQPage, lista de Product). A lista de Product é o que importa: cada item tem
`name` e `offers.price`. Sem JS, sem proxy.
"""
from __future__ import annotations

import json
import re
from typing import NamedTuple

import httpx


_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_HEADERS = {
    "User-Agent": _UA,
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Accept-Encoding": "gzip, deflate",  # sem brotli — httpx default não descompacta
}

_LD_RE = re.compile(
    r'<script[^>]*application/ld\+json[^>]*>(.+?)</script>',
    re.S,
)


class ProductHit(NamedTuple):
    name: str
    price: float


def _slug(query: str) -> str:
    """Kabum aceita slug no path: /busca/echo-dot-5"""
    return query.strip().replace(" ", "-").lower()


def _extract_products(html: str) -> list[ProductHit]:
    """Parsa JSON-LD blocks; coleta todos Product com offers.price numérico."""
    out: list[ProductHit] = []
    for m in _LD_RE.finditer(html):
        raw = m.group(1).strip()
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        # Pode ser dict único ou list de produtos
        items = obj if isinstance(obj, list) else [obj]
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("@type") != "Product":
                continue
            name = item.get("name") or ""
            offers = item.get("offers") or {}
            price = None
            if isinstance(offers, dict):
                price = offers.get("price") or offers.get("lowPrice")
            elif isinstance(offers, list) and offers:
                # AggregateOffer pode vir como list
                first = offers[0]
                if isinstance(first, dict):
                    price = first.get("price") or first.get("lowPrice")
            if not name or price is None:
                continue
            try:
                price_f = float(price)
            except (TypeError, ValueError):
                continue
            if price_f <= 0:
                continue
            out.append(ProductHit(name=name.strip(), price=price_f))
    return out


def _fetch_once(query: str, timeout: float) -> list[ProductHit]:
    url = f"https://www.kabum.com.br/busca/{_slug(query)}"
    try:
        with httpx.Client(headers=_HEADERS, timeout=timeout,
                          follow_redirects=True) as c:
            r = c.get(url)
        if r.status_code != 200:
            return []
        return _extract_products(r.text)
    except Exception:
        return []


def search(query: str, *, timeout: float = 15.0) -> list[ProductHit]:
    """
    Busca produtos no Kabum. Devolve lista vazia em qualquer erro
    (não-existência da rota, bot wall, parse fail, timeout) — fail silent.

    Se a query longa retorna 0 hits, tenta de novo com os 3 primeiros tokens
    (Kabum cadastra produtos com nome curto tipo "Monitor LG UltraGear",
    sem specs detalhadas como "32 Quad HD VA 165Hz" que o Promobit põe no
    título).
    """
    if not query or not query.strip():
        return []
    hits = _fetch_once(query, timeout)
    if hits:
        return hits
    tokens = query.split()
    if len(tokens) > 3:
        short = " ".join(tokens[:3])
        return _fetch_once(short, timeout)
    return []
