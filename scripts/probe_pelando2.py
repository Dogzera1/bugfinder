"""Procura URLs de API e endpoints GraphQL embutidos no HTML do Pelando."""
import re
import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
H = {"User-Agent": UA, "Accept-Language": "pt-BR"}

r = httpx.get("https://www.pelando.com.br/", headers=H, timeout=20,
              follow_redirects=True)

# URLs de pelando.com.br
pat = re.compile(r"https?://[a-z0-9.-]+pelando\.com\.br/[\w/\-_.?=&]*", re.I)
urls = sorted(set(pat.findall(r.text)))
print("=== URLs do domínio ===")
for u in urls[:50]:
    print(u)

# Procura GraphQL endpoints
print("\n=== GraphQL hints ===")
for kw in ["graphql", "/api/", "apollo", "/v1/", "/v2/"]:
    for m in re.finditer(rf"['\"]([^'\"]*{kw}[^'\"]*)['\"]", r.text, re.I):
        snippet = m.group(1)
        if len(snippet) < 200 and "://" in snippet or snippet.startswith("/"):
            print(f"  {kw}: {snippet[:120]}")

# Procura tags <article ... data-...>
print("\n=== Atributos data-* ===")
for m in re.finditer(r"<(article|div|li|a)[^>]*data-[\w-]+=[^>]+>", r.text):
    snippet = m.group(0)[:200]
    if any(k in snippet.lower() for k in
           ["deal", "offer", "promo", "product", "price"]):
        print(snippet)
        print("---")

# Procura JSON inline com price/deal
print("\n=== JSON inline ===")
for m in re.finditer(r'\{[^\{\}]{0,300}"(price|offerPrice|deal_id|dealId|productId)"', r.text):
    print(m.group(0)[:300])
    print("---")
