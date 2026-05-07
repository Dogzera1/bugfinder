"""Debug do search ML autenticado."""
from bugfinder.auth import MLOAuthClient
from bugfinder.config import CONFIG, PROJECT_ROOT
from bugfinder.sources.mercadolivre import MercadoLivreReference
from bugfinder.matcher import clean_title

oauth = MLOAuthClient(
    CONFIG.ml_client_id, CONFIG.ml_client_secret,
    cache_path=PROJECT_ROOT / "data" / ".ml_token.json",
)

print("token prefix:", oauth.get_access_token()[:15])

with MercadoLivreReference(oauth, site_id="MLB") as ml:
    # Caso 1: query trivial
    print("\n=== query: 'iphone 15' ===")
    results = ml.search("iphone 15", limit=5)
    print(f"  resultados: {len(results)}")
    for r in results[:3]:
        print(f"  - {r.get('title','??')[:60]}  R$ {r.get('price')}  sold={r.get('sold_quantity')}")

    # Caso 2: usando clean_title de uma oferta real
    real_title = "iPhone 17 Apple 256GB, Câmera Dupla Fusion 48MP, Tela 6.3\" Super Retina XDR, Preto"
    cleaned = clean_title(real_title)
    print(f"\n=== clean_title -> {cleaned!r} ===")
    results = ml.search(cleaned, limit=5)
    print(f"  resultados: {len(results)}")
    for r in results[:3]:
        print(f"  - {r.get('title','??')[:60]}  R$ {r.get('price')}  sold={r.get('sold_quantity')}")

    # Caso 3: reference_price direto
    print("\n=== reference_price('iphone 15') ===")
    stats = ml.reference_price("iphone 15", top_n=10, condition="new", min_sold=0)
    print(f"  stats: {stats}")
