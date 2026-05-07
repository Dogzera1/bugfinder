"""Mapeia o __NEXT_DATA__ do Promobit pra entender onde estão as ofertas."""
import json
import re
import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9"}


def get_next_data(url):
    r = httpx.get(url, headers=HEADERS, timeout=20, follow_redirects=True)
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)</script>', r.text)
    if not m:
        raise RuntimeError("__NEXT_DATA__ não encontrado")
    return json.loads(m.group(1))


def explore(node, prefix="", depth=0, max_depth=4):
    if depth > max_depth:
        return
    if isinstance(node, dict):
        for k, v in node.items():
            kind = type(v).__name__
            if isinstance(v, list):
                print(f"{prefix}{k}: list[{len(v)}]"
                      + (f" of {type(v[0]).__name__}" if v else ""))
                if v and isinstance(v[0], dict) and depth < max_depth:
                    print(f"{prefix}  [0].keys: {list(v[0].keys())[:15]}")
                    if any(kw in str(v[0]).lower() for kw in ["price", "preco", "offer", "oferta"]):
                        print(f"{prefix}  [0] sample: {json.dumps(v[0], ensure_ascii=False)[:400]}")
            elif isinstance(v, dict):
                print(f"{prefix}{k}: dict({len(v)} keys)")
                explore(v, prefix + "  ", depth + 1, max_depth)
            else:
                s = str(v)[:60]
                print(f"{prefix}{k}: {kind} = {s}")


if __name__ == "__main__":
    data = get_next_data("https://www.promobit.com.br/")
    pp = data["props"]["pageProps"]
    print("=== pageProps top keys ===")
    for k in pp.keys():
        v = pp[k]
        kind = type(v).__name__
        n = len(v) if hasattr(v, "__len__") else "-"
        print(f"  {k}: {kind} (len={n})")
    print()
    print("=== serverFeaturedOffers ===")
    sfo = pp.get("serverFeaturedOffers", [])
    if sfo:
        print(f"  len={len(sfo)}, keys: {list(sfo[0].keys())}")
        print(f"  sample[0]: {json.dumps(sfo[0], ensure_ascii=False, indent=2)[:1500]}")

    print("\n=== serverOffers ===")
    so = pp.get("serverOffers", {})
    print(f"  keys: {list(so.keys())}")
    for k, v in so.items():
        if isinstance(v, list):
            print(f"    {k}: list[{len(v)}]")
            if v and isinstance(v[0], dict):
                print(f"      [0].keys: {list(v[0].keys())}")
                print(f"      [0] sample: {json.dumps(v[0], ensure_ascii=False)[:1200]}")
                break
        elif isinstance(v, dict):
            print(f"    {k}: dict, keys={list(v.keys())[:10]}")
