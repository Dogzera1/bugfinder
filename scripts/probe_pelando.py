"""Mapeia o __NEXT_DATA__ (ou estrutura equivalente) do Pelando."""
import json
import re
import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9"}


def fetch(url):
    return httpx.get(url, headers=HEADERS, timeout=20, follow_redirects=True)


def extract_next(html):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)</script>', html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def explore(node, prefix="", depth=0, max_depth=3):
    if depth > max_depth:
        return
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, list):
                print(f"{prefix}{k}: list[{len(v)}]"
                      + (f" of {type(v[0]).__name__}" if v else ""))
                if v and isinstance(v[0], dict) and depth < max_depth:
                    print(f"{prefix}  [0].keys: {list(v[0].keys())[:25]}")
            elif isinstance(v, dict):
                print(f"{prefix}{k}: dict({len(v)} keys)")
                if depth < max_depth:
                    explore(v, prefix + "  ", depth + 1, max_depth)
            else:
                s = str(v)[:80]
                print(f"{prefix}{k}: {type(v).__name__} = {s}")


if __name__ == "__main__":
    for url in [
        "https://www.pelando.com.br/",
        "https://www.pelando.com.br/em-alta",
        "https://www.pelando.com.br/novas",
    ]:
        print(f"\n=== {url} ===")
        try:
            r = fetch(url)
            print(f"  HTTP {r.status_code}, {len(r.text)} bytes")
            data = extract_next(r.text)
            if data:
                pp = data.get("props", {}).get("pageProps", {})
                print(f"  pageProps keys: {list(pp.keys())[:20]}")
                explore(pp, "  ", depth=0, max_depth=2)
            else:
                # Pode ser SPA — checa por __APOLLO_STATE__ ou window.__INITIAL_STATE__
                for marker in ["__APOLLO_STATE__", "__INITIAL_STATE__",
                               "__NUXT__", "window.__"]:
                    if marker in r.text:
                        idx = r.text.find(marker)
                        print(f"  marcador encontrado: {marker} @ {idx}")
                        print(f"  ...{r.text[idx:idx+200]}")
                        break
                else:
                    print("  nenhum estado SSR conhecido")
                    # Sample
                    print(f"  texto[:500]={r.text[:500]}")
        except Exception as e:
            print(f"  erro: {type(e).__name__}: {e}")
