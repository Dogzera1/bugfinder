"""Orquestra um scan multi-source: coleta -> detecção -> enrichment ML -> persist."""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import CONFIG, Config
from .detector import detect_candidates
from .enricher import Enricher
from .models import Candidate, Offer, PriceHistoryStats
from .sources import REGISTRY, get_source
from .sources.base import SourceError
from .storage import Storage


@dataclass
class ScanResult:
    scan_id: int
    offers: list[Offer]
    candidates: list[Candidate]
    errors: dict[str, str]
    enrich_stats: dict = field(default_factory=dict)


def run_scan(
    *,
    sources: list[str] | None = None,
    query: str | None = None,
    category: str | None = None,
    max_items_per_source: int = 100,
    enrich_ml: bool = True,
    config: Config = CONFIG,
    storage: Storage | None = None,
    on_progress=None,
) -> ScanResult:
    sources = sources or list(REGISTRY.keys())
    storage = storage or Storage(config.db_full_path)

    scan_id = storage.start_scan(
        sources=sources, query=query, category=category,
        params={
            "max_items_per_source": max_items_per_source,
            "min_discount_pct": config.min_discount_pct,
            "min_score": config.min_score,
            "enrich_ml": enrich_ml,
        },
    )
    if on_progress:
        on_progress("scan_started", {"scan_id": scan_id, "sources": sources})

    all_offers: list[Offer] = []
    errors: dict[str, str] = {}

    for src_name in sources:
        try:
            src = get_source(src_name)
        except SourceError as e:
            errors[src_name] = str(e)
            continue

        if on_progress:
            on_progress("source_started", {"source": src_name})

        count = 0
        try:
            with src:
                for offer in src.fetch(query=query, category=category,
                                       max_items=max_items_per_source):
                    all_offers.append(offer)
                    count += 1
                    if on_progress and count % 20 == 0:
                        on_progress("source_progress", {
                            "source": src_name, "count": count,
                        })
        except SourceError as e:
            errors[src_name] = str(e)
        except Exception as e:
            errors[src_name] = f"{type(e).__name__}: {e}"

        if on_progress:
            on_progress("source_done", {
                "source": src_name, "count": count,
                "error": errors.get(src_name),
            })

    if all_offers:
        storage.upsert_offers(all_offers, scan_id=scan_id)

    print(f"[scan] coletadas {len(all_offers)} ofertas, "
          f"detectando candidatos...", flush=True)
    candidates = detect_candidates(all_offers, config)
    print(f"[scan] {len(candidates)} candidatos detectados", flush=True)

    # Histórico de preço (Fase 4): anota stats no candidato + ajusta
    # score quando preço atual é outlier baixo do próprio histórico.
    if candidates:
        _enrich_with_history(candidates, storage, on_progress=on_progress)

    # Enrichment ML (Fase 2). Pode ser desligado via env var pra cloud
    # onde ML bloqueia bot — aí notificação vira discount-only.
    enrich_stats: dict = {}
    skip_ml = enrich_ml and not config.enable_ml_lookup
    if skip_ml:
        print("[scan] enrichment ML desabilitado via ENABLE_ML_LOOKUP=0",
              flush=True)
        enrich_stats = {"skipped_reason": "ENABLE_ML_LOOKUP=0",
                        "n_total": len(candidates)}
    elif enrich_ml and candidates:
        print(f"[scan] iniciando enrichment ML em "
              f"{len(candidates)} candidatos...", flush=True)
        try:
            with Enricher(config) as enr:
                if on_progress:
                    on_progress("enrich_started", {
                        "active": enr.is_active,
                        "init_error": enr.init_error,
                        "n_candidates": len(candidates),
                    })
                candidates, enrich_stats = enr.enrich(candidates)
                if on_progress:
                    on_progress("enrich_done", enrich_stats)
        except Exception as e:
            import traceback
            print(f"[scan] enrichment falhou: {type(e).__name__}: {e}",
                  flush=True)
            traceback.print_exc()
            enrich_stats = {"skipped_reason": f"crash: {e}"}
        print(f"[scan] enrichment concluído: {enrich_stats}", flush=True)

    # Filtro pós-enrichment por ROI mínimo (se configurado)
    if config.min_roi_pct > 0:
        before = len(candidates)
        candidates = [
            c for c in candidates
            if c.viability is None or c.viability.roi_pct >= config.min_roi_pct
        ]
        # Mantém candidatos sem viability (ML inativo) — só filtra os com cálculo
        if config.min_roi_pct > 0 and candidates and any(c.viability for c in candidates):
            candidates = [
                c for c in candidates
                if c.viability is not None and c.viability.roi_pct >= config.min_roi_pct
            ]
        enrich_stats["filtered_by_roi"] = before - len(candidates)

    if candidates:
        storage.insert_candidates(scan_id, candidates)

    storage.finish_scan(scan_id, n_offers=len(all_offers),
                        n_candidates=len(candidates))

    if on_progress:
        on_progress("done", {
            "n_offers": len(all_offers),
            "n_candidates": len(candidates),
            "errors": errors,
            "enrich": enrich_stats,
        })

    return ScanResult(
        scan_id=scan_id, offers=all_offers, candidates=candidates,
        errors=errors, enrich_stats=enrich_stats,
    )


def _enrich_with_history(
    candidates: list[Candidate],
    storage: Storage,
    *,
    days: int = 30,
    min_count_outlier: int = 5,
    on_progress=None,
) -> None:
    """
    Anota PriceHistoryStats no candidato e ajusta o score conforme:
      - Outlier baixo (preço atual <= P10 com count>=5): +0.10 e reason
      - Preço normalmente baixo (atual >= P50 com count>=5): -0.05 e reason
        ("old_price provavelmente inflado")
    """
    items = [(c.offer.source, c.offer.external_id) for c in candidates]
    try:
        stats_map = storage.get_price_stats_bulk(items, days=days)
    except Exception as e:
        print(f"[scan] history enrichment falhou: {e}", flush=True)
        return

    n_outlier = 0
    n_normal_low = 0
    for c in candidates:
        key = (c.offer.source, c.offer.external_id)
        s = stats_map.get(key)
        if not s:
            continue
        is_outlier = (s["count"] >= min_count_outlier
                      and c.offer.price <= s["p10"])
        c.history = PriceHistoryStats(
            count=s["count"],
            min=s["min"],
            max=s["max"],
            p10=s["p10"],
            p25=s["p25"],
            p50=s["p50"],
            p75=s["p75"],
            is_outlier=is_outlier,
        )
        if is_outlier:
            c.score = min(1.0, c.score + 0.10)
            c.reasons.append(
                f"outlier histórico: preço atual ≤ P10 dos últimos "
                f"{days}d (n={s['count']})"
            )
            n_outlier += 1
        elif (s["count"] >= min_count_outlier
              and c.offer.price >= s["p50"]):
            c.score = max(0.0, c.score - 0.05)
            c.reasons.append(
                f"preço atual já é mediano nos últimos {days}d "
                f"(old_price pode estar inflado)"
            )
            n_normal_low += 1

    candidates.sort(key=lambda c: c.score, reverse=True)
    if on_progress:
        on_progress("history_done", {
            "n_with_history": sum(1 for c in candidates if c.history),
            "n_outlier": n_outlier,
            "n_normal_low": n_normal_low,
        })
