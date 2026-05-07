"""Sources: cada arquivo aqui implementa a interface Source pra um site."""
from .base import Source, SourceError
from .kabum import KabumSource
from .promobit import PromobitSource

REGISTRY: dict[str, type[Source]] = {
    "promobit": PromobitSource,
    "kabum": KabumSource,
}


def get_source(name: str) -> Source:
    """Instancia uma source pelo nome curto."""
    cls = REGISTRY.get(name.lower())
    if not cls:
        raise SourceError(
            f"source desconhecida: {name!r}. "
            f"disponíveis: {', '.join(sorted(REGISTRY))}"
        )
    return cls()


__all__ = [
    "Source", "SourceError",
    "PromobitSource", "KabumSource",
    "REGISTRY", "get_source",
]
