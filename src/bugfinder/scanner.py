"""Orquestra um scan multi-source: coleta -> detecção -> enrichment ML -> persist."""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import CONFIG, Config
from .detector import detect_candidates
from .enricher import Enricher
from .models import Candidate, Offer
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
        storage.upsert_offers(all_offers)

    print(f"[scan] coletadas {len(all_offers)} ofertas, "
          f"detectando candidatos...", flush=True)
    candidates = detect_candidates(all_offers, config)
    print(f"[scan] {len(candidates)} candidatos detectados", flush=True)

    # Enrichment ML (Fase 2)
    enrich_stats: dict = {}
    if enrich_ml and candidates:
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
