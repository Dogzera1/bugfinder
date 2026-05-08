"""Configuração lida de variáveis de ambiente / .env"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def _float(name: str, default: float) -> float:
    v = os.getenv(name)
    return float(v) if v else default


def _int(name: str, default: int) -> int:
    v = os.getenv(name)
    return int(v) if v else default


@dataclass(frozen=True)
class Config:
    # --- detector hard filters ---
    min_discount_pct: float = _float("MIN_DISCOUNT_PCT", 20.0)
    min_price_brl: float = _float("MIN_PRICE_BRL", 50.0)
    max_price_brl: float = _float("MAX_PRICE_BRL", 15000.0)
    min_rating_score: float = _float("MIN_RATING_SCORE", 0.5)

    # --- detector weights (somam ~1.0) ---
    w_discount: float = _float("W_DISCOUNT", 0.6)
    w_rating: float = _float("W_RATING", 0.3)
    w_popularity: float = _float("W_POPULARITY", 0.1)

    # --- threshold do candidato ---
    min_score: float = _float("MIN_SCORE", 0.25)

    # --- storage ---
    db_path: str = os.getenv("DB_PATH", "data/bugfinder.db")

    # --- ML (Fase 2) ---
    ml_client_id: str | None = os.getenv("ML_CLIENT_ID") or None
    ml_client_secret: str | None = os.getenv("ML_CLIENT_SECRET") or None
    ml_site_id: str = os.getenv("ML_SITE_ID", "MLB")
    ml_redirect_uri: str = os.getenv("ML_REDIRECT_URI",
                                     "https://example.org/callback")

    # --- Viabilidade (Fase 2) ---
    ml_fee_pct: float = _float("ML_FEE_PCT", 0.14)
    freight_buy: float = _float("FREIGHT_BUY", 0.0)
    freight_sell: float = _float("FREIGHT_SELL", 20.0)
    min_roi_pct: float = _float("MIN_ROI_PCT", 0.0)  # filtro pós-enrichment

    # --- Modo de operação ---
    # ENABLE_ML_LOOKUP=0 desliga o enricher (útil em cloud onde ML bloqueia
    # bot). Sem ML, notificação cai pra modo "discount-only".
    enable_ml_lookup: bool = (
        os.getenv("ENABLE_ML_LOOKUP", "1").lower() not in ("0", "false", "no")
    )
    # Discount % mínimo pra notificar quando ML lookup está off
    min_discount_pct_notify: float = _float("MIN_DISCOUNT_PCT_NOTIFY", 35.0)

    # --- Telegram (Fase 5) ---
    telegram_bot_token: str | None = os.getenv("TELEGRAM_BOT_TOKEN") or None
    telegram_chat_id: str | None = os.getenv("TELEGRAM_CHAT_ID") or None

    @property
    def db_full_path(self) -> Path:
        p = Path(self.db_path)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def data_dir(self) -> Path:
        """Diretório onde DB + caches vivem. Em produção (Railway) é o volume."""
        return self.db_full_path.parent

    @property
    def ml_token_cache_path(self) -> Path:
        return self.data_dir / ".ml_token.json"


CONFIG = Config()
