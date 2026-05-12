"""
Enrichment Fase 2: dado um Candidate, busca referência de preço no ML
via Playwright (lista.mercadolivre.com.br renderizado como browser real).

A API oficial bloqueia search pra apps não-certificadas (403), e HTTP simples
retorna stub. Playwright é o caminho que funciona.

Falha silenciosamente: se ML lookup falha pra um candidato, mantém o resto.

Bootstrap em produção (Railway): se a env var ML_REFRESH_TOKEN_SEED estiver
setada e não há cache local, faz um refresh imediato e popula o cache no volume.
"""
from __future__ import annotations

import os
from typing import Iterable

from .auth import MLOAuthClient, NoMLCredentialsError
from .benchmark import benchmark_lookup
from .config import Config
from .matcher import clean_title
from .models import Candidate, MarketReference
from .sources.ml_browser import MercadoLivreBrowser
from .storage import Storage
from .viability import compute_viability


def _maybe_bootstrap_ml_token(cfg: Config) -> None:
    """
    Em ambientes fresh (Railway primeira deploy), seeda o cache de token
    a partir de ML_REFRESH_TOKEN_SEED se ele estiver presente. No-op se
    o cache já existir ou se não há credenciais ML / seed.
    """
    if cfg.ml_token_cache_path.exists():
        return
    seed = os.getenv("ML_REFRESH_TOKEN_SEED")
    if not seed or not cfg.ml_client_id or not cfg.ml_client_secret:
        return
    try:
        MLOAuthClient(
            client_id=cfg.ml_client_id,
            client_secret=cfg.ml_client_secret,
            cache_path=cfg.ml_token_cache_path,
            prefer_user_token=True,
            seed_refresh_token=seed,
        )
    except NoMLCredentialsError:
        pass
    except Exception:
        pass


class Enricher:
    """Lookup ML + viabilidade. Reusa um único browser entre todos candidatos."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._ml: MercadoLivreBrowser | None = None
        self._init_error: str | None = None

        # bootstrap de token em produção fresh
        _maybe_bootstrap_ml_token(cfg)

        # Headless por padrão; permite override via env (debug local)
        headless_env = os.getenv("PLAYWRIGHT_HEADLESS", "1").lower()
        headless = headless_env not in ("0", "false", "no")

        # Proxy opcional pra contornar bot detection do ML em IPs cloud.
        # Configure PROXY_SERVER (+ user/pass se autenticado).
        proxy_cfg = self._build_proxy_config()

        print("[enricher] launching chromium...", flush=True)
        try:
            self._ml = MercadoLivreBrowser(headless=headless, proxy=proxy_cfg)
            self._ml.__enter__()
            print("[enricher] chromium ready.", flush=True)
        except Exception as e:
            import traceback
            self._ml = None
            self._init_error = (
                f"Playwright/Chromium não disponível: {type(e).__name__}: {e}"
            )
            print(f"[enricher] init failed: {self._init_error}", flush=True)
            traceback.print_exc()

    @staticmethod
    def _build_proxy_config() -> dict[str, str] | None:
        server = os.getenv("PROXY_SERVER")
        if not server:
            return None
        cfg: dict[str, str] = {"server": server}
        if u := os.getenv("PROXY_USERNAME"):
            cfg["username"] = u
        if p := os.getenv("PROXY_PASSWORD"):
            cfg["password"] = p
        return cfg

    @property
    def is_active(self) -> bool:
        return self._ml is not None

    @property
    def init_error(self) -> str | None:
        return self._init_error

    def close(self) -> None:
        if self._ml:
            self._ml.__exit__(None, None, None)
            self._ml = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def enrich(self, candidates: Iterable[Candidate],
               storage: Storage | None = None) -> tuple[list[Candidate], dict]:
        """
        Devolve (candidatos_enriquecidos, stats).
        stats: {n_total, n_with_ref, n_profitable, errors, n_bench, n_bench_inflated}

        storage: opcional. Passar pra ativar o cache 24h do benchmark cross-loja.
        """
        out: list[Candidate] = []
        stats = {"n_total": 0, "n_with_ref": 0, "n_profitable": 0, "errors": 0,
                 "n_bench": 0, "n_bench_inflated": 0}

        if not self._ml:
            stats["skipped_reason"] = self._init_error or "ML não inicializado"
            # Mesmo sem ML, ainda rodamos benchmark — ele é independente.
            for c in candidates:
                stats["n_total"] += 1
                out.append(self._add_benchmark(c, storage, stats))
            return out, stats

        # Limita enrichment pra evitar OOM/timeouts em deploys com pouca RAM
        # (cada lookup abre uma página chromium).
        max_enrich = int(os.getenv("MAX_ENRICH_PER_CYCLE", "20"))
        cand_list = list(candidates)
        cand_list.sort(key=lambda c: c.score, reverse=True)
        to_enrich = cand_list[:max_enrich]
        to_skip = cand_list[max_enrich:]

        for i, c in enumerate(to_enrich):
            stats["n_total"] += 1
            ref: MarketReference | None = None
            try:
                query = clean_title(c.offer.title)
                if query:
                    # Valida contra a query LIMPA, não o título original cheio de
                    # marketing — senão o overlap fica artificialmente baixo
                    # porque o título original tem 20+ tokens e o título do ML
                    # tem só 6-7. min_matches=2 pra aceitar produtos menos
                    # populares (P25 ainda é razoável com 2 preços).
                    raw = self._ml.reference_price(
                        query,
                        validate_against=query,
                        top_n=25,
                        min_overlap=0.5,
                        min_matches=2,
                    )
                    if raw:
                        ref = MarketReference(**raw)
            except Exception as e:
                stats["errors"] += 1
                print(f"[enricher] erro #{i}: {type(e).__name__}: {e}",
                      flush=True)

            via = None
            if ref is not None and ref.median > 0:
                stats["n_with_ref"] += 1
                # usa P25 como preço de venda conservador (canto inferior)
                via = compute_viability(
                    offer_price=c.offer.price,
                    ml_sale_price=ref.p25,
                    ml_fee_pct=self.cfg.ml_fee_pct,
                    freight_buy=self.cfg.freight_buy,
                    freight_sell=self.cfg.freight_sell,
                )
                if via.margin_brl > 0:
                    stats["n_profitable"] += 1

            enriched = c.model_copy(update={
                "market_reference": ref,
                "viability": via,
            })
            out.append(self._add_benchmark(enriched, storage, stats))

        # candidatos pulados por max_enrich entram sem reference/viability
        # (mas com benchmark, que é leve)
        for c in to_skip:
            stats["n_total"] += 1
            out.append(self._add_benchmark(c, storage, stats))
        if to_skip:
            stats["skipped_by_cap"] = len(to_skip)

        return out, stats

    def _add_benchmark(self, c: Candidate, storage: Storage | None,
                       stats: dict) -> Candidate:
        """
        Anota benchmark cross-loja no candidato. Fail silent.
        Pula se a oferta original já é da Kabum (não tem sentido comparar
        Kabum contra Kabum).
        """
        try:
            ref = benchmark_lookup(
                c.offer.title,
                offer_price=c.offer.price,
                storage=storage,
                skip_source=c.offer.source,
            )
        except Exception as e:
            print(f"[enricher] benchmark erro: {type(e).__name__}: {e}",
                  flush=True)
            return c
        if ref is None:
            return c
        stats["n_bench"] += 1
        if ref.real_discount_pct is not None and ref.real_discount_pct < 5.0:
            stats["n_bench_inflated"] += 1
        return c.model_copy(update={"benchmark": ref})
