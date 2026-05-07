"""
Matcher: dado uma Offer (de Promobit/Kabum), busca produto correspondente no ML
e devolve uma `MarketReference` com preço de revenda estimado.

Estratégia:
  1. Limpa o título da oferta (remove ruído de varejo: cores excessivas,
     parênteses, marketing, código de loja).
  2. Pega 5–7 tokens-chave (marca + modelo + capacidade quando relevante).
  3. Busca no ML.
  4. Calcula referência de preço (mediana de top N ativos).

Não tenta matching exato — a UI mostra o link da busca pro usuário validar.
"""
from __future__ import annotations

import re

from .models import MarketReference, Offer
from .sources.mercadolivre import MercadoLivreReference


# Tokens descartados — barulho de marketing/ retail
_NOISE_TOKENS = {
    "promoção", "promocao", "oferta", "ofertas", "imperdível", "imperdivel",
    "lançamento", "lancamento", "novo", "nova", "novos", "novas",
    "exclusivo", "exclusiva", "original", "originais", "lacrado", "lacrada",
    "garantia", "anatel", "homologado", "nfe", "nf-e", "frete", "grátis",
    "gratis", "envio", "rápido", "rapido", "imediato", "pronto", "pronta",
    "entrega", "loja", "oficial", "outlet", "barato", "barata",
    "*", "+", "/", "|",
}

_PARENS_RE = re.compile(r"[\(\[\{].*?[\)\]\}]")
_NON_TITLE_RE = re.compile(r"[^\w\s\-\.,áéíóúâêôãõçÁÉÍÓÚÂÊÔÃÕÇ]")
_MULTI_SPACE_RE = re.compile(r"\s+")


def clean_title(raw: str) -> str:
    """Reduz um título ruidoso a uma query enxuta."""
    s = _PARENS_RE.sub(" ", raw)
    s = _NON_TITLE_RE.sub(" ", s)
    s = _MULTI_SPACE_RE.sub(" ", s).strip().lower()

    tokens: list[str] = []
    for tok in s.split():
        if not tok:
            continue
        if tok in _NOISE_TOKENS:
            continue
        if len(tok) <= 1 and not tok.isdigit():
            continue
        tokens.append(tok)

    # mantém marca + modelo + 1-2 specs principais (capacidade, polegadas)
    return " ".join(tokens[:7])


def find_ml_reference(
    offer: Offer,
    ml: MercadoLivreReference,
    *,
    top_n: int = 15,
    min_sold: int = 0,
) -> MarketReference | None:
    """
    Devolve `MarketReference` ou None se não der pra calcular.
    Não levanta exceções: erros viram None.
    """
    query = clean_title(offer.title)
    if not query:
        return None
    try:
        stats = ml.reference_price(query, top_n=top_n, min_sold=min_sold)
    except Exception:
        return None
    if not stats:
        return None
    search_url = (
        f"https://lista.mercadolivre.com.br/{query.replace(' ', '-')}"
    )
    return MarketReference(
        query_used=query,
        median=stats["median"],
        p25=stats["p25"],
        p75=stats["p75"],
        min=stats["min"],
        max=stats["max"],
        count=stats["count"],
        sample_links=stats["sample_links"],
        search_url=search_url,
    )
