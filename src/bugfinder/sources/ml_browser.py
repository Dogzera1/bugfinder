"""
Lookup de preço de referência no Mercado Livre via Playwright (browser headless).

A API oficial /sites/MLB/search é bloqueada pra apps não-certificadas (403),
e fetch HTTP simples retorna stub de "baixe o app". Playwright contorna.

Usado pelo enricher (Fase 2) pra calcular ROI esperado de revenda.
"""
from __future__ import annotations

import re
import statistics
import unicodedata
from typing import Any

from playwright.sync_api import sync_playwright, Browser, BrowserContext


UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

CARD_SELECTOR = ".poly-card, .ui-search-layout__item"
TITLE_SELECTORS = [
    ".poly-component__title-wrapper a",
    ".poly-component__title",
    ".ui-search-item__title",
    "h3",
]
PRICE_SELECTORS = [
    ".poly-price__current .andes-money-amount__fraction",
    ".andes-money-amount__fraction",
    ".ui-search-price__second-line .andes-money-amount__fraction",
]


def _parse_brl_price(s: str) -> float | None:
    """'4.898' (parte inteira da exibição BRL) -> 4898.0"""
    s = (s or "").strip().replace("\xa0", "").replace(" ", "")
    s = s.replace(".", "")  # remove milhar
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (len(sorted_values) - 1) * pct
    lo = int(rank)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = rank - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


_PUNCT_RE = re.compile(r"[^\w\s]")
_GENERIC_TOKENS = {
    # tokens muito genéricos que não diferenciam produto
    "de", "da", "do", "com", "para", "por", "em", "e", "o", "a", "os", "as",
    "no", "na", "tela", "preto", "branco", "azul", "vermelho", "verde",
    "amarelo", "rosa", "cinza", "cor", "cores", "tamanho", "modelo", "novo",
    "novos", "nova", "novas", "the", "of", "for", "with",
}


def _normalize(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower()


def _tokenize(s: str) -> set[str]:
    s = _normalize(s)
    s = _PUNCT_RE.sub(" ", s)
    out = set()
    for tok in s.split():
        tok = tok.strip()
        if len(tok) <= 1:
            continue
        if tok in _GENERIC_TOKENS:
            continue
        out.add(tok)
    return out


def _overlap(query_tokens: set[str], candidate_title: str) -> float:
    """Fração dos tokens-chave da query que aparecem no título candidato."""
    if not query_tokens:
        return 0.0
    cand = _tokenize(candidate_title)
    return len(query_tokens & cand) / len(query_tokens)


class MercadoLivreBrowser:
    """
    Reusa um único browser/contexto entre múltiplos lookups num scan.
    Use como context manager.
    """

    def __init__(self, *, headless: bool = True, timeout_ms: int = 15000,
                 viewport: tuple[int, int] = (1280, 900)) -> None:
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.viewport = viewport
        self._pw = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    def __enter__(self) -> "MercadoLivreBrowser":
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self.headless,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-gpu",
            ],
        )
        # Localização forçada pra Brasil — ML faz geo-redirect em IPs cloud.
        self._context = self._browser.new_context(
            viewport={"width": self.viewport[0], "height": self.viewport[1]},
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
            geolocation={"latitude": -23.5505, "longitude": -46.6333},
            permissions=["geolocation"],
            user_agent=UA,
            extra_http_headers={
                "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.5",
            },
        )
        # Warmup: visita homepage MLB pra setar os cookies de país.
        # Sem isso, lista.mercadolivre.com.br pode servir a versão internacional
        # (title="Mercado Libre") em vez da brasileira ("Mercado Livre").
        self._do_warmup()
        return self

    def _do_warmup(self) -> None:
        page = self._context.new_page()
        try:
            page.goto("https://www.mercadolivre.com.br/",
                      wait_until="domcontentloaded", timeout=20000)
            # se aparecer interstitial de seleção de país, clica em Brasil
            try:
                btn = page.locator(
                    "a[href*='mercadolivre.com.br'], "
                    "button:has-text('Brasil'), a:has-text('Brasil')"
                ).first
                if btn.count():
                    btn.click(timeout=2000)
                    page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass
            t = page.title() or ""
            print(f"[ml_browser] warmup title={t!r}", flush=True)
        except Exception as e:
            print(f"[ml_browser] warmup falhou: {type(e).__name__}: {e}",
                  flush=True)
        finally:
            page.close()

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        try:
            if self._context:
                self._context.close()
        except Exception:
            pass
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._context = None
        self._browser = None
        self._pw = None

    # ---- API pública ----

    def reference_price(
        self,
        query: str,
        *,
        validate_against: str | None = None,
        top_n: int = 25,
        min_overlap: float = 0.5,
        min_matches: int = 3,
    ) -> dict[str, Any] | None:
        """
        Estatísticas de preço pra um query no ML.

        Se `validate_against` é dado (tipicamente o título original da oferta),
        filtra resultados ML por overlap de tokens — descarta matches em que
        marca/modelo principal não bate. Isso evita falso positivo (Asics
        matched as Qix, etc).

        Devolve None se restarem < min_matches resultados válidos.
        """
        if not self._context:
            raise RuntimeError("MercadoLivreBrowser não foi inicializado "
                               "(use como context manager)")

        slug = query.strip().replace(" ", "-").lower()
        url = f"https://lista.mercadolivre.com.br/{slug}"

        page = self._context.new_page()
        try:
            try:
                page.goto(url, wait_until="domcontentloaded",
                          timeout=self.timeout_ms)
            except Exception as e:
                print(f"[ml_browser] goto fail q={query!r}: {e}", flush=True)
                return None

            cards_found = False
            try:
                page.wait_for_selector(CARD_SELECTOR, timeout=self.timeout_ms)
                cards_found = True
            except Exception:
                pass

            if not cards_found:
                try:
                    p_title = page.title()
                    body = page.content()
                except Exception:
                    p_title, body = "?", ""
                stub = ("micro-landing" in body) or (len(body) < 8000)
                # 'Mercado Libre' (sem v) = versão internacional/AR/MX.
                # 'Mercado Livre' (com v) = BR.
                is_intl = "mercado libre" in p_title.lower() \
                          and "livre" not in p_title.lower()
                # log primeiro snippet do body pra entender o que veio
                body_head = body[:400].replace("\n", " ").replace("  ", " ")
                print(
                    f"[ml_browser] no cards q={query!r} "
                    f"title={p_title!r} body_len={len(body)} "
                    f"stub={stub} intl_redirect={is_intl}\n"
                    f"  body_head={body_head!r}",
                    flush=True,
                )
                return None

            cards = page.locator(CARD_SELECTOR).all()[:top_n]
            raw_results: list[dict[str, Any]] = []

            for c in cards:
                title = ""
                for ts in TITLE_SELECTORS:
                    t = c.locator(ts).first
                    if t.count():
                        title = (t.text_content() or "").strip()
                        if title:
                            break
                price_str = ""
                for ps in PRICE_SELECTORS:
                    pe = c.locator(ps).first
                    if pe.count():
                        price_str = (pe.text_content() or "").strip()
                        if price_str:
                            break
                price = _parse_brl_price(price_str)
                href = ""
                a = c.locator("a").first
                if a.count():
                    href = a.get_attribute("href") or ""

                if price is None or price <= 0 or not title:
                    continue
                raw_results.append({
                    "title": title, "price": price, "url": href,
                })

            # validação por overlap de tokens
            if validate_against:
                key_tokens = _tokenize(validate_against)
                # também tira tokens muito frequentes pra não inflar overlap
                # (já filtrados em _GENERIC_TOKENS)
                filtered: list[dict[str, Any]] = []
                for r in raw_results:
                    ov = _overlap(key_tokens, r["title"])
                    r["overlap"] = ov
                    if ov >= min_overlap:
                        filtered.append(r)
                accepted = filtered
            else:
                for r in raw_results:
                    r["overlap"] = 1.0
                accepted = raw_results

            if len(accepted) < min_matches:
                # diagnóstico: cards existem mas validação cortou tudo
                sample_titles = [r["title"][:50] for r in raw_results[:3]]
                print(
                    f"[ml_browser] q={query!r}: "
                    f"raw={len(raw_results)} accepted={len(accepted)} "
                    f"sample={sample_titles}",
                    flush=True,
                )
                return None

            prices_sorted = sorted(r["price"] for r in accepted)
            avg_overlap = sum(r["overlap"] for r in accepted) / len(accepted)
            samples = sorted(accepted, key=lambda r: r["price"])[:5]

            return {
                "query_used": query,
                "median": float(statistics.median(prices_sorted)),
                "p25": _percentile(prices_sorted, 0.25),
                "p75": _percentile(prices_sorted, 0.75),
                "min": prices_sorted[0],
                "max": prices_sorted[-1],
                "count": len(prices_sorted),
                "sample_links": samples,
                "search_url": url,
                "match_confidence": avg_overlap,
            }
        finally:
            page.close()
