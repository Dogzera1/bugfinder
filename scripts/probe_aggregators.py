"""Compara estrutura de Bondfaro e Buscape — escolher o de melhor cobertura."""
import json
import re
import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
H = {"User-Agent": UA, "Accept-Language": "pt-BR"}


def get_next(url):
    r = httpx.get(url, headers=H, timeout=20, follow_redirects=True)
    print(f"\n=== {url} ===")
    print(f"  HTTP {r.status_code}, {len(r.text)} bytes")
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)</script>', r.text)
    if not m:
        print("  sem __NEXT_DATA__")
        return
    data = json.loads(m.group(1))
    pp = data.get("props", {}).get("pageProps", {})
    print(f"  pageProps top: {list(pp.keys())[:25]}")
    # Tenta achar arrays grandes
    def walk(node, path="", depth=0, max_depth=5):
        if depth > max_depth:
            return
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, list) and len(v) >= 3 and v and isinstance(v[0], dict):
                    keys = list(v[0].keys())
                    if any(kw in str(keys).lower() for kw in
                           ["price", "preco", "title", "name", "url"]):
                        print(f"  {path}.{k}: list[{len(v)}], "
                              f"keys[:15]={keys[:15]}")
                        sample = json.dumps(v[0], ensure_ascii=False)[:600]
                        print(f"    sample: {sample}")
                elif isinstance(v, dict):
                    walk(v, path + "." + k, depth + 1, max_depth)
                elif isinstance(v, list) and v and isinstance(v[0], dict):
                    walk({"_item": v[0]}, path + "." + k + "[]",
                         depth + 1, max_depth)
    walk(pp, "pageProps")


# Páginas com listagem de ofertas — testa rotas comuns
for base, paths in [
    ("https://www.bondfaro.com.br", [
        "/ofertas/",
        "/ofertas/eletronicos/",
        "/o/promocoes/",
    ]),
    ("https://www.buscape.com.br", [
        "/promocoes",
        "/cupons",
        "/categoria/eletronicos",
    ]),
]:
    for p in paths:
        try:
            get_next(base + p)
        except Exception as e:
            print(f"\n=== {base}{p} ===")
            print(f"  erro: {type(e).__name__}: {e}")
