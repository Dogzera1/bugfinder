"""Testa quais endpoints ML aceitam app token + viabilidade de scraping HTML."""
import httpx
from bugfinder.auth import MLOAuthClient
from bugfinder.config import CONFIG, PROJECT_ROOT

oauth = MLOAuthClient(
    CONFIG.ml_client_id, CONFIG.ml_client_secret,
    cache_path=PROJECT_ROOT / "data" / ".ml_token.json",
)
token = oauth.get_access_token()
H_API = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

print("=== API endpoints com app token ===")
for path in [
    "/sites/MLB/search?q=iphone&limit=2",
    "/sites/MLB/categories",
    "/items/MLB1234567890",   # id genérico, pode ser inválido mas mostra resposta
    "/products/MLB14589090",  # catálogo
    "/sites/MLB",
    "/users/me",              # endpoint de quem é o token
    "/categories/MLB1051",
    "/highlights/MLB/MLB1051",
]:
    try:
        r = httpx.get(f"https://api.mercadolibre.com{path}",
                      headers=H_API, timeout=10)
        snippet = r.text[:120].replace("\n", " ")
        print(f"  {r.status_code}  {path}")
        if r.status_code != 200 and len(snippet) > 0:
            print(f"          {snippet}")
    except Exception as e:
        print(f"  ERR  {path}: {e}")

# ---- Scraping HTML como alternativa ----
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
H_HTML = {"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9"}

print("\n=== HTML scraping de páginas públicas ML ===")
for url in [
    "https://lista.mercadolivre.com.br/iphone-15",
    "https://lista.mercadolivre.com.br/iphone-15-256gb",
    "https://www.mercadolivre.com.br/jms/mlb/lk/cliffside?q=iphone+15",
    "https://www.mercadolivre.com.br/iphone-15-256gb/_NoIndex_True",
]:
    try:
        r = httpx.get(url, headers=H_HTML, timeout=15, follow_redirects=True)
        size = len(r.text)
        # tenta achar preços no HTML
        import re
        prices = re.findall(r'"price":\s*(\d+(?:\.\d+)?)', r.text)[:5]
        print(f"  {r.status_code}  size={size:>7}  prices_found={len(prices)} sample={prices}  {url[:55]}")
    except Exception as e:
        print(f"  ERR  {url}: {e}")
