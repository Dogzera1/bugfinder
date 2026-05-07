"""Reconhecimento da estrutura da página de listagem do ML via browser."""
from playwright.sync_api import sync_playwright

URL = "https://lista.mercadolivre.com.br/iphone-15"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(
        viewport={"width": 1280, "height": 900},
        locale="pt-BR",
        user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"),
    )
    page = ctx.new_page()
    print(f"GET {URL} ...")
    page.goto(URL, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_load_state("networkidle", timeout=15000)

    # tenta múltiplos selectors de card de produto
    for sel in [".poly-card", ".ui-search-layout__item",
                "li.ui-search-layout__item", ".ui-search-result",
                "[data-testid='result-item']"]:
        n = page.locator(sel).count()
        print(f"  selector {sel!r}: {n}")

    # extrai 3 primeiros com varios price selectors
    print("\nExtraindo top 3:")
    cards = page.locator(".poly-card, .ui-search-layout__item").all()[:3]
    for i, c in enumerate(cards):
        title = ""
        price = ""
        for tsel in [".poly-component__title-wrapper a", ".poly-component__title",
                     ".ui-search-item__title", "h3"]:
            t = c.locator(tsel).first
            if t.count():
                title = t.text_content() or ""
                break
        for psel in [".poly-price__current .andes-money-amount__fraction",
                     ".andes-money-amount__fraction",
                     ".ui-search-price__second-line .andes-money-amount__fraction"]:
            pe = c.locator(psel).first
            if pe.count():
                price = pe.text_content() or ""
                break
        link = ""
        a = c.locator("a").first
        if a.count():
            link = a.get_attribute("href") or ""
        print(f"  [{i}] price={price!r}  title={title[:60]!r}  url={link[:60]!r}")

    browser.close()
