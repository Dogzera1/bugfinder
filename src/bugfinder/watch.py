r"""
Loop de scans periódicos com notificação Telegram.

Roda foreground (Ctrl+C pra parar). Pra manter em background no Windows
use Start-Process ou crie tarefa no Agendador de Tarefas:
  pwsh -c "& '.\.venv\Scripts\python.exe' -m bugfinder watch"
"""
from __future__ import annotations

import signal
import time
import traceback
from dataclasses import dataclass

from rich.console import Console

from .config import CONFIG
from .notifier import TelegramConfigError, TelegramNotifier, drain_callbacks
from .scanner import run_scan
from .storage import Storage


console = Console()


@dataclass
class WatchOptions:
    interval_min: int = 30
    sources: list[str] | None = None
    query: str | None = None
    category: str | None = None
    max_items_per_source: int = 80
    min_roi_pct: float = 15.0
    min_match_confidence: float = 0.5
    notify_only_with_roi: bool = True
    max_notifications_per_cycle: int = 20


_stop_requested = False


def _install_sigint():
    def _handler(signum, frame):
        global _stop_requested
        _stop_requested = True
        console.print("\n[yellow]Parando após o ciclo atual...[/yellow]")
    signal.signal(signal.SIGINT, _handler)


def watch(opts: WatchOptions) -> None:
    """Loop principal de monitoramento."""
    _install_sigint()

    storage = Storage(CONFIG.db_full_path)
    try:
        notifier = TelegramNotifier(
            bot_token=CONFIG.telegram_bot_token,
            chat_id=CONFIG.telegram_chat_id,
        )
    except TelegramConfigError as e:
        console.print(f"[red]Telegram não configurado:[/red] {e}")
        console.print("Configure TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID no .env "
                      "e tente de novo.")
        return

    console.print(f"[bold]watch[/bold] iniciado — intervalo {opts.interval_min}min, "
                  f"ROI mínimo {opts.min_roi_pct:.0f}%, "
                  f"match ≥ {opts.min_match_confidence:.0%}")

    offset_path = CONFIG.data_dir / ".tg_offset.json"

    def _on_callback(stage, info):
        if stage == "callback_applied":
            console.print(
                f"  📲 callback: candidate#{info['candidate_id']} → "
                f"[bold]{info['status']}[/bold]"
            )
        elif stage == "error":
            console.print(
                f"  [red]callback erro:[/red] candidate#{info['candidate_id']}: "
                f"{info['error']}"
            )

    cycle = 0
    try:
        while not _stop_requested:
            cycle += 1
            t0 = time.time()
            console.print(
                f"\n[cyan]ciclo {cycle}[/cyan] — "
                f"{time.strftime('%Y-%m-%d %H:%M:%S')}"
            )
            # Drena callbacks acumulados desde o último ciclo
            try:
                n = drain_callbacks(
                    notifier=notifier, storage=storage,
                    offset_path=offset_path, long_poll_timeout=0,
                    on_event=_on_callback,
                )
                if n:
                    console.print(f"  {n} interaç{'ão' if n == 1 else 'ões'} aplicada{'' if n == 1 else 's'}")
            except Exception as e:
                console.print(f"  [yellow]drain callbacks falhou:[/yellow] {e}")

            try:
                _do_cycle(opts, storage, notifier)
            except Exception:
                console.print("[red]erro no ciclo:[/red]")
                console.print(traceback.format_exc())

            elapsed = time.time() - t0
            wait = max(0, opts.interval_min * 60 - elapsed)
            console.print(f"  ciclo terminou em {elapsed:.0f}s, "
                          f"próximo em {wait:.0f}s")
            # Espera reativa: a cada 30s drena callbacks pra UX < 30s,
            # respondendo rápido ao Ctrl+C também.
            slept = 0
            while slept < wait and not _stop_requested:
                time.sleep(1)
                slept += 1
                if slept % 30 == 0:
                    try:
                        n = drain_callbacks(
                            notifier=notifier, storage=storage,
                            offset_path=offset_path, long_poll_timeout=0,
                            on_event=_on_callback,
                        )
                        if n:
                            console.print(f"  {n} interaç{'ão' if n == 1 else 'ões'} aplicada{'' if n == 1 else 's'} (entre ciclos)")
                    except Exception:
                        pass
    finally:
        notifier.close()
        console.print("[bold]watch encerrado.[/bold]")


def _do_cycle(opts: WatchOptions, storage: Storage,
              notifier: TelegramNotifier) -> None:
    # callback que loga eventos importantes (não tudo)
    def progress(stage: str, info: dict):
        if stage == "source_done" and info.get("error"):
            console.print(f"  [red]✗[/red] {info['source']}: {info['error']}")
        elif stage == "enrich_started" and not info.get("active"):
            console.print(
                f"  [yellow]⚠ ML lookup inativo:[/yellow] "
                f"{info.get('init_error') or 'desconhecido'}"
            )

    result = run_scan(
        sources=opts.sources,
        query=opts.query,
        category=opts.category,
        max_items_per_source=opts.max_items_per_source,
        enrich_ml=True,
        config=CONFIG,
        storage=storage,
        on_progress=progress,
    )
    es = result.enrich_stats or {}
    console.print(
        f"  scan #{result.scan_id}: {len(result.offers)} ofertas, "
        f"{len(result.candidates)} candidatos | "
        f"ML: {es.get('n_with_ref', 0)} ref, "
        f"{es.get('n_profitable', 0)} lucrativos, "
        f"{es.get('errors', 0)} erros"
        + (f" | skip: {es['skipped_reason']}"
           if es.get("skipped_reason") else "")
    )

    # Em modo "ML desligado", filtra por discount em vez de ROI
    if not CONFIG.enable_ml_lookup:
        rows = storage.list_unnotified(
            min_discount_pct=CONFIG.min_discount_pct_notify,
            require_viability=False,
            min_roi_pct=None,
            min_match_confidence=None,
            limit=opts.max_notifications_per_cycle,
        )
    else:
        rows = storage.list_unnotified(
            min_roi_pct=opts.min_roi_pct,
            min_match_confidence=opts.min_match_confidence,
            require_viability=opts.notify_only_with_roi,
            limit=opts.max_notifications_per_cycle,
        )
    if not rows:
        console.print("  nada novo pra notificar.")
        return

    console.print(f"  enviando [bold green]{len(rows)}[/bold green] notificações ...")
    sent_ids: list[int] = []
    for r in rows:
        try:
            notifier.send_candidate(r)
            sent_ids.append(r["id"])
        except Exception as e:
            console.print(f"  [red]falhou[/red] candidate#{r['id']}: {e}")

    storage.mark_notified(sent_ids)
    console.print(f"  ✓ {len(sent_ids)} notificadas e marcadas.")
