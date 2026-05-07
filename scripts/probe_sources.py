"""Reconnaissance script — descobre estrutura das páginas de cada source."""
import re
import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9"}


def probe(name, url):
    print(f"=== {name} ===")
    try:
        r = httpx.get(url, headers=HEADERS, timeout=20, follow_redirects=True)
    except Exception as e:
        print(f"  ERR {type(e).__name__}: {e}")
        return None
    html = r.text
    print(f"  status={r.status_code}  size={len(html)}b")

    patterns = [
        ("__NEXT_DATA__",            r'<script id="__NEXT_DATA__"[^>]*>'),
        ("INITIAL_STATE",            r'window\.__INITIAL_STATE__\s*='),
        ("ld+json count",            r'application/ld\+json'),
        ("data-asin (Amazon)",       r'data-asin="[A-Z0-9]{10}"'),
        ("a-price-whole (Amazon)",   r'class="a-price-whole"'),
        ("data-testid offer card",   r'data-testid="[^"]*offer'),
        ('class*="OfferCard"',       r'class="[^"]*OfferCard'),
        ("class*=\"offer\"",         r'class="[^"]*offer'),
    ]
    for label, pat in patterns:
        n = len(re.findall(pat, html))
        if n:
            print(f"  {label}: {n}")

    m = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)</script>', html
    )
    if m:
        snippet = m.group(1)
        print(f"  __NEXT_DATA__ size: {len(snippet)}b")
        print(f"  __NEXT_DATA__ first 400ch: {snippet[:400]}")
    return html


if __name__ == "__main__":
    probe("PROMOBIT_HOME",
          "https://www.promobit.com.br/")
    probe("PROMOBIT_OFERTAS",
          "https://www.promobit.com.br/ofertas/")
    probe("AMAZON_SEARCH",
          "https://www.amazon.com.br/s?k=fone+bluetooth")
    probe("KABUM_SEARCH",
          "https://www.kabum.com.br/busca/fone-bluetooth")
    probe("PELANDO_HOME",
          "https://www.pelando.com.br/")
