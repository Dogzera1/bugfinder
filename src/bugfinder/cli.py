"""CLI do bug finder.

Comandos:
  bugfinder scan       [--sources promobit,kabum] [--query "..."] [--category "..."]
                       [--max-items 100] [--min-discount 20] [--csv out.csv] [--top 30]
  bugfinder candidates [--top 30] [--scan-id N] [--source promobit] [--status new]
  bugfinder scans      [--top 20]
  bugfinder mark <id> <new|seen|bought|ignored>
  bugfinder sources
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from . import __version__
from .config import CONFIG, PROJECT_ROOT
from .scanner import run_scan
from .sources import REGISTRY
from .storage import Storage

console = Console()


def _fmt_brl(v: float | None) -> str:
    if v is None:
        return "—"
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


# ---------- comandos ----------

def cmd_sources(_args) -> int:
    t = Table(show_header=True, header_style="bold")
    t.add_column("Nome")
    t.add_column("Display")
    for name, cls in sorted(REGISTRY.items()):
        t.add_row(name, getattr(cls, "display_name", name))
    console.print(t)
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    sources = (
        [s.strip() for s in args.sources.split(",") if s.strip()]
        if args.sources else list(REGISTRY.keys())
    )
    unknown = [s for s in sources if s not in REGISTRY]
    if unknown:
        console.print(f"[red]source(s) inválida(s):[/red] {', '.join(unknown)}")
        console.print(f"disponíveis: {', '.join(sorted(REGISTRY))}")
        return 2

    cfg = CONFIG
    if args.min_discount is not None:
        cfg = replace(cfg, min_discount_pct=args.min_discount)

    head = f"[bold]Scan[/bold] sources={','.join(sources)}"
    if args.query:    head += f"  query={args.query!r}"
    if args.category: head += f"  category={args.category!r}"
    head += f"  max-items/src={args.max_items}  min-disc={cfg.min_discount_pct:.0f}%"
    if args.no_ml:
        head += "  [dim](ml-lookup desabilitado)[/dim]"
    console.print(head)

    def progress(stage: str, info: dict):
        if stage == "source_started":
            console.print(f"  → {info['source']} ...", end="\r")
        elif stage == "source_progress":
            console.print(f"  → {info['source']}: {info['count']} ofertas ...",
                          end="\r")
        elif stage == "source_done":
            err = info.get("error")
            if err:
                console.print(f"  [red]✗[/red] {info['source']}: {err}")
            else:
                console.print(f"  [green]✓[/green] {info['source']}: "
                              f"{info['count']} ofertas")
        elif stage == "enrich_started":
            if not info.get("active"):
                console.print(
                    f"  [yellow]⚠ ML lookup pulado:[/yellow] "
                    f"{info.get('init_error') or 'inativo'}"
                )
            else:
                console.print(f"  → enriquecendo {info['n_candidates']} "
                              f"candidatos com ML ...")
        elif stage == "enrich_done":
            with_ref = info.get("n_with_ref", 0)
            profit = info.get("n_profitable", 0)
            errs = info.get("errors", 0)
            console.print(
                f"  [green]✓[/green] enrichment: {with_ref} com referência ML, "
                f"{profit} lucrativos"
                + (f", {errs} erros" if errs else "")
            )
        elif stage == "done":
            console.print(f"\n[bold]Total:[/bold] {info['n_offers']} ofertas, "
                          f"[bold green]{info['n_candidates']}[/bold green] candidatos")

    result = run_scan(
        sources=sources,
        query=args.query,
        category=args.category,
        max_items_per_source=args.max_items,
        enrich_ml=not args.no_ml,
        config=cfg,
        on_progress=progress,
    )

    if not result.candidates:
        console.print("\n[yellow]Nenhum candidato passou os filtros.[/yellow]")
        console.print("Ajustes possíveis:")
        console.print("  • Baixar [cyan]--min-discount[/cyan] (atual: "
                      f"{cfg.min_discount_pct:.0f}%)")
        console.print("  • Subir [cyan]--max-items[/cyan]")
        console.print("  • Em .env: relaxar MIN_RATING_SCORE / MIN_SCORE")
        return 0

    _print_candidates_table(result.candidates[: args.top])

    if args.csv:
        out_path = Path(args.csv)
        if not out_path.is_absolute():
            out_path = PROJECT_ROOT / out_path
        _export_csv(out_path, result.candidates)
        console.print(f"\n[green]CSV salvo em[/green] {out_path}")

    console.print(
        f"\nScan [bold]#{result.scan_id}[/bold] persistido. "
        f"Use [cyan]bugfinder candidates --scan-id {result.scan_id}[/cyan] pra revisar."
    )
    return 0


def cmd_candidates(args: argparse.Namespace) -> int:
    storage = Storage(CONFIG.db_full_path)
    rows = storage.list_candidates(
        scan_id=args.scan_id, status=args.status,
        source=args.source, top=args.top,
    )
    if not rows:
        console.print("[yellow]Nenhum candidato encontrado.[/yellow]")
        return 0
    _print_db_candidates_table(rows)
    return 0


def cmd_scans(args: argparse.Namespace) -> int:
    storage = Storage(CONFIG.db_full_path)
    rows = storage.list_scans(top=args.top)
    if not rows:
        console.print("[yellow]Nenhum scan ainda.[/yellow]")
        return 0
    t = Table(show_header=True, header_style="bold")
    t.add_column("#", justify="right")
    t.add_column("Quando")
    t.add_column("Sources")
    t.add_column("Query/Cat")
    t.add_column("Ofertas", justify="right")
    t.add_column("Candidatos", justify="right", style="bold green")
    for r in rows:
        ts = r["ts"]
        try:
            ts = datetime.fromisoformat(ts).astimezone().strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass
        qc = r["query"] or r["category"] or "—"
        t.add_row(str(r["id"]), ts, r["sources"], _truncate(qc, 25),
                  str(r["n_offers"]), str(r["n_candidates"]))
    console.print(t)
    return 0


def cmd_ml_token_info(args: argparse.Namespace) -> int:
    """Imprime info do token cacheado — útil pra copiar refresh_token pro Railway."""
    import json
    if not CONFIG.ml_token_cache_path.exists():
        console.print(f"[red]Cache não existe em[/red] {CONFIG.ml_token_cache_path}")
        console.print("Rode [cyan]bugfinder ml-auth[/cyan] primeiro.")
        return 1
    data = json.loads(CONFIG.ml_token_cache_path.read_text(encoding="utf-8"))
    console.print(f"[bold]Cache:[/bold] {CONFIG.ml_token_cache_path}")
    console.print(f"  user_id      : {data.get('user_id')}")
    console.print(f"  auth_method  : {data.get('auth_method')}")
    console.print(f"  scope        : {data.get('scope')}")
    console.print(f"  expires_at   : {data.get('expires_at')}")
    rt = data.get("refresh_token")
    if rt:
        console.print(
            f"\n[bold]Refresh token (pra Railway/cloud):[/bold]\n"
            f"[cyan]{rt}[/cyan]\n"
        )
        console.print(
            "[dim]Copie acima e configure como env var [/dim]"
            "[bold]ML_REFRESH_TOKEN_SEED[/bold] no Railway.\n"
            "[dim]Esse seed é usado UMA vez no primeiro start; depois o "
            "container persiste o cache renovado no volume.[/dim]"
        )
    else:
        console.print("\n[yellow]Sem refresh_token[/yellow] — sua app ML "
                      "não tem 'Refresh Token' habilitado.")
    return 0


def cmd_telegram_test(args: argparse.Namespace) -> int:
    from .notifier import TelegramConfigError, TelegramNotifier
    try:
        with TelegramNotifier(CONFIG.telegram_bot_token,
                              CONFIG.telegram_chat_id) as tg:
            tg.send_test()
    except TelegramConfigError as e:
        console.print(f"[red]Erro de config:[/red] {e}")
        return 1
    except Exception as e:
        console.print(f"[red]Falha ao enviar:[/red] {e}")
        return 1
    console.print("[green]✓ mensagem enviada[/green] — confere o seu Telegram.")
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    from .watch import WatchOptions, watch
    sources = (
        [s.strip() for s in args.sources.split(",") if s.strip()]
        if args.sources else None
    )
    opts = WatchOptions(
        interval_min=args.interval,
        sources=sources,
        query=args.query,
        category=args.category,
        max_items_per_source=args.max_items,
        min_roi_pct=args.min_roi,
        min_match_confidence=args.min_match,
        notify_only_with_roi=not args.allow_no_roi,
        max_notifications_per_cycle=args.max_per_cycle,
    )
    watch(opts)
    return 0


def cmd_ml_auth(args: argparse.Namespace) -> int:
    """Fluxo authorization_code: usuário autentica no browser e cola o code."""
    import webbrowser
    from urllib.parse import parse_qs, urlparse

    from .auth import MLOAuthClient, NoMLCredentialsError

    redirect_uri = args.redirect_uri or CONFIG.ml_redirect_uri

    try:
        oauth = MLOAuthClient(
            client_id=CONFIG.ml_client_id,
            client_secret=CONFIG.ml_client_secret,
            cache_path=CONFIG.ml_token_cache_path,
            prefer_user_token=True,
        )
    except NoMLCredentialsError as e:
        console.print(f"[red]{e}[/red]")
        return 1

    auth_url = oauth.build_auth_url(redirect_uri=redirect_uri, scope="read")

    console.print("[bold]Fluxo de autenticação ML[/bold]\n")
    console.print("1. Vou abrir o link abaixo no seu browser. Faça login e clique em "
                  "[bold]'Permitir'[/bold].")
    console.print("2. Você será redirecionado para uma página que [yellow]vai dar erro[/yellow] "
                  "(é esperado — example.org não responde).")
    console.print("3. [bold]Copie a URL inteira[/bold] da barra do browser depois do "
                  "redirect e cole aqui abaixo.\n")
    console.print(f"[cyan]{auth_url}[/cyan]\n")

    if not args.no_browser:
        try:
            webbrowser.open(auth_url)
            console.print("[dim]Browser aberto.[/dim]\n")
        except Exception:
            pass

    console.print("Cole a URL completa do callback (com ?code=...) ou só o code:")
    user_input = input("> ").strip()

    code = user_input
    if "code=" in user_input:
        # extrai do URL completo
        parsed = urlparse(user_input)
        params = parse_qs(parsed.query)
        if "code" in params:
            code = params["code"][0]

    if not code:
        console.print("[red]Code vazio — abortado.[/red]")
        return 1

    console.print("\nTrocando code por tokens ...")
    try:
        state = oauth.exchange_code(code, redirect_uri=redirect_uri)
    except Exception as e:
        console.print(f"[red]Falhou:[/red] {e}")
        return 1

    console.print(f"[green]✓ autenticado[/green] (user_id={state.user_id}, "
                  f"scope={state.scope}, refresh_token="
                  f"{'sim' if state.refresh_token else 'não'}).")

    if not state.refresh_token:
        console.print(
            "\n[yellow]⚠ Atenção:[/yellow] sua app não tem [bold]Refresh Token[/bold] "
            "habilitado — token expira em ~6h e você vai precisar rodar "
            "[cyan]bugfinder ml-auth[/cyan] de novo. Pra deixar permanente:\n"
            "  1. Volta em https://developers.mercadolivre.com.br/devcenter\n"
            "  2. Edita a app, em 'Fluxos OAuth' marca [bold]Refresh Token[/bold]\n"
            "  3. Salva e roda [cyan]bugfinder ml-auth[/cyan] de novo"
        )
    return 0


def cmd_mark(args: argparse.Namespace) -> int:
    valid = {"new", "seen", "bought", "ignored"}
    if args.status not in valid:
        console.print(f"[red]status inválido.[/red] use: {', '.join(sorted(valid))}")
        return 2
    storage = Storage(CONFIG.db_full_path)
    storage.update_candidate_status(args.candidate_id, args.status)
    console.print(f"✔ candidato #{args.candidate_id} → [bold]{args.status}[/bold]")
    return 0


# ---------- output helpers ----------

def _linked_title(title: str, url: str, max_width: int = 70) -> str:
    """Título clicável (terminais modernos) + truncamento."""
    show = title if len(title) <= max_width else title[: max_width - 1] + "…"
    return f"[link={url}]{show}[/link]"


def _print_candidates_table(candidates) -> None:
    has_ml = any(c.market_reference for c in candidates)
    t = Table(show_header=True, header_style="bold", show_lines=False)
    t.add_column("Score",  justify="right", style="bold cyan")
    t.add_column("% off",  justify="right", style="green")
    t.add_column("Preço",  justify="right", style="bold")
    t.add_column("De",     justify="right", style="dim")
    if has_ml:
        t.add_column("ML p25",   justify="right", style="cyan")
        t.add_column("Margem",   justify="right")
        t.add_column("ROI",      justify="right", style="bold magenta")
    t.add_column("Loja",   max_width=14)
    t.add_column("Título (clique pra abrir)",
                 min_width=36, max_width=64, overflow="ellipsis")
    t.add_column("⭐",     justify="right")
    t.add_column("Cupom",  max_width=14)
    for c in candidates:
        o = c.offer
        row = [
            f"{c.score:.2f}",
            f"{o.discount_pct:.0f}%",
            _fmt_brl(o.price),
            _fmt_brl(o.old_price),
        ]
        if has_ml:
            mr = c.market_reference
            via = c.viability
            row.extend([
                _fmt_brl(mr.p25) if mr else "—",
                _color_margin(via.margin_brl) if via else "—",
                _color_roi(via.roi_pct) if via else "—",
            ])
        row.extend([
            o.store_name or "—",
            _linked_title(o.title, o.url),
            f"{o.rating_score*100:.0f}%" if o.rating_score is not None else "—",
            o.coupon_code or "",
        ])
        t.add_row(*row)
    console.print(t)
    console.print("[dim]Margem/ROI usam P25 da busca ML como preço de revenda "
                  "(conservador). Clique no título pra abrir.[/dim]")


def _color_margin(v: float) -> str:
    color = "green" if v > 0 else "red"
    return f"[{color}]{_fmt_brl(v)}[/{color}]"


def _color_roi(v: float) -> str:
    if v >= 30:   return f"[bold green]{v:.0f}%[/bold green]"
    if v >= 10:   return f"[green]{v:.0f}%[/green]"
    if v >= 0:    return f"[yellow]{v:.0f}%[/yellow]"
    return f"[red]{v:.0f}%[/red]"


def _print_db_candidates_table(rows) -> None:
    has_ml = any(r["via_roi_pct"] is not None for r in rows)
    t = Table(show_header=True, header_style="bold", show_lines=False)
    t.add_column("#",      justify="right")
    t.add_column("Score",  justify="right", style="bold cyan")
    t.add_column("% off",  justify="right", style="green")
    t.add_column("Preço",  justify="right", style="bold")
    t.add_column("De",     justify="right", style="dim")
    if has_ml:
        t.add_column("ML p25",  justify="right", style="cyan")
        t.add_column("Margem",  justify="right")
        t.add_column("ROI",     justify="right", style="bold magenta")
    t.add_column("Loja",   max_width=14)
    t.add_column("Título (clique)", min_width=36, max_width=56,
                 overflow="ellipsis")
    t.add_column("Source")
    t.add_column("Status")
    for r in rows:
        row = [
            str(r["id"]), f"{r['score']:.2f}",
            f"{r['discount_pct']:.0f}%",
            _fmt_brl(r["price"]), _fmt_brl(r["old_price"]),
        ]
        if has_ml:
            row.extend([
                _fmt_brl(r["ml_p25"]) if r["ml_p25"] else "—",
                _color_margin(r["via_margin_brl"])
                if r["via_margin_brl"] is not None else "—",
                _color_roi(r["via_roi_pct"])
                if r["via_roi_pct"] is not None else "—",
            ])
        row.extend([
            r["store_name"] or "—",
            _linked_title(r["title"], r["url"]),
            r["source"],
            r["status"],
        ])
        t.add_row(*row)
    console.print(t)
    console.print("[dim]Use [cyan]bugfinder mark <#> seen|bought|ignored[/cyan] "
                  "pra atualizar status.[/dim]")


def _export_csv(path: Path, candidates) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow([
            "score", "source", "title", "store",
            "price_brl", "old_price_brl", "discount_pct",
            "rating_score", "rating_count", "popularity",
            "category", "coupon_code", "url",
            "ml_query", "ml_median_brl", "ml_p25_brl", "ml_count",
            "ml_search_url",
            "via_sale_price_brl", "via_acquisition_brl",
            "via_margin_brl", "via_roi_pct",
            "fetched_at", "reasons",
        ])
        for c in candidates:
            o = c.offer
            mr = c.market_reference
            via = c.viability
            w.writerow([
                f"{c.score:.3f}", o.source, o.title, o.store_name or "",
                f"{o.price:.2f}",
                f"{o.old_price:.2f}" if o.old_price else "",
                f"{o.discount_pct:.1f}",
                f"{o.rating_score:.3f}" if o.rating_score is not None else "",
                o.rating_count, o.popularity,
                o.category or "", o.coupon_code or "",
                o.url,
                mr.query_used if mr else "",
                f"{mr.median:.2f}" if mr else "",
                f"{mr.p25:.2f}" if mr else "",
                mr.count if mr else "",
                mr.search_url if mr else "",
                f"{via.ml_sale_price:.2f}" if via else "",
                f"{via.acquisition_cost:.2f}" if via else "",
                f"{via.margin_brl:.2f}" if via else "",
                f"{via.roi_pct:.2f}" if via else "",
                o.fetched_at.isoformat(timespec="seconds"),
                " | ".join(c.reasons),
            ])


# ---------- arg parsing ----------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bugfinder",
        description="Encontra ofertas com alto desconto em sites BR pra revenda.",
    )
    p.add_argument("--version", action="version", version=f"bugfinder {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="executa um scan multi-source")
    s.add_argument("--sources",
                   help=f"csv. default: todas ({','.join(sorted(REGISTRY))})")
    s.add_argument("--query", help="termo de busca aplicado a cada source")
    s.add_argument("--category", help="categoria (interpretação varia por source)")
    s.add_argument("--max-items", type=int, default=80,
                   help="máximo de ofertas POR source (default 80)")
    s.add_argument("--min-discount", type=float,
                   help="override do MIN_DISCOUNT_PCT (em %%)")
    s.add_argument("--top", type=int, default=30, help="quantos candidatos exibir")
    s.add_argument("--no-ml", action="store_true",
                   help="pula enrichment ML (útil pra debug ou economizar quota)")
    s.add_argument("--csv", help="exporta candidatos pra CSV")
    s.set_defaults(func=cmd_scan)

    c = sub.add_parser("candidates", help="lista candidatos do banco")
    c.add_argument("--top", type=int, default=30)
    c.add_argument("--scan-id", type=int)
    c.add_argument("--source")
    c.add_argument("--status", choices=["new", "seen", "bought", "ignored"])
    c.set_defaults(func=cmd_candidates)

    sc = sub.add_parser("scans", help="histórico de scans")
    sc.add_argument("--top", type=int, default=20)
    sc.set_defaults(func=cmd_scans)

    m = sub.add_parser("mark", help="muda status de um candidato")
    m.add_argument("candidate_id", type=int)
    m.add_argument("status")
    m.set_defaults(func=cmd_mark)

    src = sub.add_parser("sources", help="lista sources disponíveis")
    src.set_defaults(func=cmd_sources)

    auth = sub.add_parser("ml-auth",
                          help="autentica no ML via browser (1× por instalação)")
    auth.add_argument("--redirect-uri",
                      help="redirect_uri cadastrado (default: do .env)")
    auth.add_argument("--no-browser", action="store_true",
                      help="não abre browser, só imprime a URL")
    auth.set_defaults(func=cmd_ml_auth)

    tt = sub.add_parser("telegram-test",
                        help="envia mensagem de teste pro seu Telegram")
    tt.set_defaults(func=cmd_telegram_test)

    ti = sub.add_parser("ml-token-info",
                        help="mostra refresh_token cacheado (pra seed Railway)")
    ti.set_defaults(func=cmd_ml_token_info)

    w = sub.add_parser("watch",
                       help="loop 24/7 — scans periódicos + alertas Telegram")
    w.add_argument("--interval", type=int, default=30,
                   help="intervalo entre scans em minutos (default 30)")
    w.add_argument("--sources",
                   help=f"csv. default: todas ({','.join(sorted(REGISTRY))})")
    w.add_argument("--query", help="termo de busca")
    w.add_argument("--category", help="categoria")
    w.add_argument("--max-items", type=int, default=80,
                   help="ofertas por source (default 80)")
    w.add_argument("--min-roi", type=float, default=15.0,
                   help="ROI mínimo pra notificar (default 15%%)")
    w.add_argument("--min-match", type=float, default=0.5,
                   help="confiança de match mínima (0..1, default 0.5)")
    w.add_argument("--allow-no-roi", action="store_true",
                   help="notifica candidatos sem ROI calculado")
    w.add_argument("--max-per-cycle", type=int, default=20,
                   help="máximo de notificações por ciclo (default 20)")
    w.set_defaults(func=cmd_watch)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args) or 0
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelado.[/yellow]")
        return 130


if __name__ == "__main__":
    sys.exit(main())
