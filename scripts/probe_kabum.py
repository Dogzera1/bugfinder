"""Encontra o caminho dos produtos no __NEXT_DATA__ do Kabum."""
import json
import re
import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9"}


def get_next_data(url):
    r = httpx.get(url, headers=HEADERS, timeout=20, follow_redirects=True)
    print(f"GET {url} -> {r.status_code}, {len(r.text)}b")
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)</script>', r.text)
    if not m:
        return None
    return json.loads(m.group(1))


def find_products_paths(node, path="$"):
    """Procura listas que parecem produtos."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from find_products_paths(v, f"{path}.{k}")
    elif isinstance(node, list):
        if node and isinstance(node[0], dict):
            keys = set(node[0].keys())
            score = sum(k in keys for k in ("price", "name", "code", "sku",
                                              "priceWithDiscount", "title", "offer"))
            if score >= 2:
                print(f"  ★ {path}  (len={len(node)}, sample keys: {sorted(keys)[:12]})")
        for i, item in enumerate(node[:1]):
            yield from find_products_paths(item, f"{path}[{i}]")


def explore(url):
    print(f"\n=== {url} ===")
    data = get_next_data(url)
    if not data:
        print("  sem __NEXT_DATA__")
        return
    pp = data.get("props", {}).get("pageProps", {})
    inner = pp.get("data")
    if isinstance(inner, str):
        try:
            inner = json.loads(inner)
        except Exception:
            inner = None
    if isinstance(inner, dict):
        cs = inner.get("catalogServer")
        if cs:
            print(f"  catalogServer keys: {list(cs.keys())[:15]}")
            for k, v in cs.items():
                if isinstance(v, list):
                    print(f"    {k}: list[{len(v)}]")
                    if v and isinstance(v[0], dict):
                        print(f"      [0].keys: {sorted(v[0].keys())[:20]}")
                        if any(kw in str(v[0]).lower() for kw in ("price", "name")):
                            sample = json.dumps(v[0], ensure_ascii=False)[:600]
                            print(f"      [0] sample: {sample}")
                elif isinstance(v, dict):
                    print(f"    {k}: dict, keys={list(v.keys())[:10]}")


def dump_product_fields(url):
    print(f"\n=== fields in {url} ===")
    data = get_next_data(url)
    pp = data["props"]["pageProps"]
    inner = pp["data"]
    if isinstance(inner, str):
        inner = json.loads(inner)
    products = inner["catalogServer"]["data"]
    p = products[0]
    print(f"FULL keys ({len(p)}): {sorted(p.keys())}")
    # filter keys that look like price/discount
    for k in sorted(p.keys()):
        v = p[k]
        if any(kw in k.lower() for kw in ("price", "discount", "available", "url",
                                           "link", "name", "code", "freight",
                                           "image", "rating", "stock", "offer",
                                           "categ", "manufactu")):
            short = repr(v)[:140]
            print(f"  {k}: {short}")


def has_products(url):
    try:
        data = get_next_data(url)
        if not data:
            return 0
        pp = data.get("props", {}).get("pageProps", {})
        inner = pp.get("data")
        if isinstance(inner, str):
            inner = json.loads(inner)
        if isinstance(inner, dict):
            cs = inner.get("catalogServer")
            if cs and isinstance(cs.get("data"), list):
                return len(cs["data"])
    except Exception as e:
        return f"err:{e}"
    return 0


if __name__ == "__main__":
    candidates = [
        "https://www.kabum.com.br/eletronicos",
        "https://www.kabum.com.br/hardware",
        "https://www.kabum.com.br/computadores",
        "https://www.kabum.com.br/celular-smartphone",
        "https://www.kabum.com.br/games",
        "https://www.kabum.com.br/busca/oferta",
        "https://www.kabum.com.br/busca/promocao",
        "https://www.kabum.com.br/busca/desconto",
        "https://www.kabum.com.br/busca/notebook",
        "https://www.kabum.com.br/busca/ofertas",
    ]
    for u in candidates:
        n = has_products(u)
        print(f"  {n!s:>5} products :: {u}")
