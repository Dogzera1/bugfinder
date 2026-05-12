"""Persistência em SQLite."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .models import Candidate, Offer


SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT    NOT NULL,
    sources       TEXT    NOT NULL,           -- csv de source names
    query         TEXT,
    category      TEXT,
    n_offers      INTEGER NOT NULL DEFAULT 0,
    n_candidates  INTEGER NOT NULL DEFAULT 0,
    params_json   TEXT
);

CREATE TABLE IF NOT EXISTS offers (
    source         TEXT NOT NULL,
    external_id    TEXT NOT NULL,
    title          TEXT NOT NULL,
    url            TEXT NOT NULL,
    price          REAL NOT NULL,
    old_price      REAL,
    currency       TEXT NOT NULL,
    store_name     TEXT,
    store_domain   TEXT,
    category       TEXT,
    image          TEXT,
    coupon_code    TEXT,
    rating_score   REAL,
    rating_count   INTEGER NOT NULL DEFAULT 0,
    popularity     INTEGER NOT NULL DEFAULT 0,
    available      INTEGER NOT NULL DEFAULT 1,
    metadata_json  TEXT,
    fetched_at     TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL,
    PRIMARY KEY (source, external_id)
);

CREATE INDEX IF NOT EXISTS idx_offers_store      ON offers(store_name);
CREATE INDEX IF NOT EXISTS idx_offers_category   ON offers(category);
CREATE INDEX IF NOT EXISTS idx_offers_last_seen  ON offers(last_seen_at);

CREATE TABLE IF NOT EXISTS candidates (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id           INTEGER NOT NULL,
    source            TEXT    NOT NULL,
    external_id       TEXT    NOT NULL,
    score             REAL    NOT NULL,
    discount_pct      REAL    NOT NULL,
    reasons_json      TEXT    NOT NULL,
    status            TEXT    NOT NULL DEFAULT 'new',  -- new|seen|bought|ignored
    ts                TEXT    NOT NULL,
    -- Enrichment ML (Fase 2; opcional, NULL se ML não configurado/sem match)
    ml_query          TEXT,
    ml_median         REAL,
    ml_p25            REAL,
    ml_p75            REAL,
    ml_count          INTEGER,
    ml_search_url     TEXT,
    via_sale_price    REAL,   -- usado no cálculo (tipicamente p25)
    via_acquisition   REAL,
    via_margin_brl    REAL,
    via_roi_pct       REAL,
    FOREIGN KEY(scan_id) REFERENCES scans(id),
    FOREIGN KEY(source, external_id) REFERENCES offers(source, external_id)
);

CREATE INDEX IF NOT EXISTS idx_candidates_scan   ON candidates(scan_id);
CREATE INDEX IF NOT EXISTS idx_candidates_status ON candidates(status);
CREATE INDEX IF NOT EXISTS idx_candidates_score  ON candidates(score DESC);

-- Histórico de preço por SKU. Uma linha por scan onde a offer foi vista.
-- Permite detectar se o preço atual é outlier (P10) vs preço normalmente
-- baixo (mediana já é esse valor → old_price é provavelmente inflado).
CREATE TABLE IF NOT EXISTS price_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source       TEXT    NOT NULL,
    external_id  TEXT    NOT NULL,
    price        REAL    NOT NULL,
    ts           TEXT    NOT NULL,
    scan_id      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_price_history_sku_ts
    ON price_history(source, external_id, ts);
CREATE INDEX IF NOT EXISTS idx_price_history_ts
    ON price_history(ts);

-- Cache 24h do benchmark cross-loja por query (chave = sha1 do título normalizado).
-- count=0 marca "tentei e não achou" pra não retentar dentro da TTL.
CREATE TABLE IF NOT EXISTS benchmark_cache (
    query_hash       TEXT    PRIMARY KEY,
    query_used       TEXT    NOT NULL,
    median_brl       REAL,
    p25_brl          REAL,
    p75_brl          REAL,
    count            INTEGER NOT NULL,
    sources_json     TEXT,
    match_confidence REAL,
    ts               TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_benchmark_cache_ts ON benchmark_cache(ts);
"""

# Migrações idempotentes pra DBs antigos — rodam ANTES do executescript
# pra garantir que colunas usadas em índices abaixo já existam.
_MIGRATIONS = [
    "ALTER TABLE candidates ADD COLUMN ml_query TEXT",
    "ALTER TABLE candidates ADD COLUMN ml_median REAL",
    "ALTER TABLE candidates ADD COLUMN ml_p25 REAL",
    "ALTER TABLE candidates ADD COLUMN ml_p75 REAL",
    "ALTER TABLE candidates ADD COLUMN ml_count INTEGER",
    "ALTER TABLE candidates ADD COLUMN ml_search_url TEXT",
    "ALTER TABLE candidates ADD COLUMN ml_match_confidence REAL",
    "ALTER TABLE candidates ADD COLUMN via_sale_price REAL",
    "ALTER TABLE candidates ADD COLUMN via_acquisition REAL",
    "ALTER TABLE candidates ADD COLUMN via_margin_brl REAL",
    "ALTER TABLE candidates ADD COLUMN via_roi_pct REAL",
    "ALTER TABLE candidates ADD COLUMN notified_at TEXT",
    "ALTER TABLE candidates ADD COLUMN hist_count INTEGER",
    "ALTER TABLE candidates ADD COLUMN hist_p10 REAL",
    "ALTER TABLE candidates ADD COLUMN hist_p50 REAL",
    "ALTER TABLE candidates ADD COLUMN hist_min REAL",
    "ALTER TABLE candidates ADD COLUMN hist_is_outlier INTEGER",
    # Benchmark cross-loja (Fase 6)
    "ALTER TABLE candidates ADD COLUMN bench_median REAL",
    "ALTER TABLE candidates ADD COLUMN bench_count INTEGER",
    "ALTER TABLE candidates ADD COLUMN bench_real_discount_pct REAL",
    "ALTER TABLE candidates ADD COLUMN bench_badge TEXT",
    "ALTER TABLE candidates ADD COLUMN bench_sources_json TEXT",
]

# Índices que dependem de colunas adicionadas pelas migrações.
_POST_MIGRATION_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_candidates_roi ON candidates(via_roi_pct DESC)",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Storage:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            # tenta cada migração; ignora se a coluna já existe
            for stmt in _MIGRATIONS:
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError:
                    pass
            # índices que dependem das colunas migradas
            for stmt in _POST_MIGRATION_INDEXES:
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError:
                    pass

    @contextmanager
    def transaction(self):
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # --- scans ---

    def start_scan(self, *, sources: list[str], query: str | None,
                   category: str | None, params: dict | None = None) -> int:
        with self.transaction() as conn:
            cur = conn.execute(
                """INSERT INTO scans (ts, sources, query, category, params_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (_now_iso(), ",".join(sources), query, category,
                 json.dumps(params or {}, ensure_ascii=False)),
            )
            return int(cur.lastrowid)

    def finish_scan(self, scan_id: int, *, n_offers: int, n_candidates: int) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE scans SET n_offers=?, n_candidates=? WHERE id=?",
                (n_offers, n_candidates, scan_id),
            )

    def list_scans(self, top: int = 20) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return list(conn.execute(
                """SELECT id, ts, sources, query, category, n_offers, n_candidates
                   FROM scans ORDER BY id DESC LIMIT ?""",
                (top,),
            ).fetchall())

    # --- offers ---

    def upsert_offers(self, offers: Iterable[Offer],
                      *, scan_id: int | None = None) -> int:
        rows = []
        history_rows = []
        now = _now_iso()
        offers = list(offers)
        for o in offers:
            rows.append((
                o.source, o.external_id, o.title, o.url, o.price, o.old_price,
                o.currency, o.store_name, o.store_domain, o.category, o.image,
                o.coupon_code, o.rating_score, o.rating_count, o.popularity,
                int(o.available),
                json.dumps(o.metadata, ensure_ascii=False, default=str),
                o.fetched_at.isoformat(timespec="seconds"),
                now,
            ))
            history_rows.append((
                o.source, o.external_id, o.price, now, scan_id,
            ))
        if not rows:
            return 0
        with self.transaction() as conn:
            conn.executemany(
                """INSERT INTO offers (
                       source, external_id, title, url, price, old_price,
                       currency, store_name, store_domain, category, image,
                       coupon_code, rating_score, rating_count, popularity,
                       available, metadata_json, fetched_at, last_seen_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(source, external_id) DO UPDATE SET
                       title=excluded.title,
                       url=excluded.url,
                       price=excluded.price,
                       old_price=excluded.old_price,
                       store_name=excluded.store_name,
                       store_domain=excluded.store_domain,
                       category=excluded.category,
                       image=excluded.image,
                       coupon_code=excluded.coupon_code,
                       rating_score=excluded.rating_score,
                       rating_count=excluded.rating_count,
                       popularity=excluded.popularity,
                       available=excluded.available,
                       metadata_json=excluded.metadata_json,
                       last_seen_at=excluded.last_seen_at
                """,
                rows,
            )
            conn.executemany(
                """INSERT INTO price_history
                       (source, external_id, price, ts, scan_id)
                   VALUES (?, ?, ?, ?, ?)""",
                history_rows,
            )
        return len(rows)

    # --- price history ---

    def get_price_history(self, source: str, external_id: str,
                          *, days: int = 30,
                          limit: int = 500) -> list[float]:
        """Retorna lista de preços históricos do SKU nos últimos `days`."""
        from datetime import timedelta
        since = (datetime.now(timezone.utc) - timedelta(days=days))\
            .isoformat(timespec="seconds")
        with self._connect() as conn:
            return [r[0] for r in conn.execute(
                """SELECT price FROM price_history
                   WHERE source = ? AND external_id = ? AND ts >= ?
                   ORDER BY ts DESC
                   LIMIT ?""",
                (source, external_id, since, limit),
            ).fetchall()]

    def get_price_stats_bulk(self, items: Iterable[tuple[str, str]],
                             *, days: int = 30) -> dict[tuple[str, str], dict]:
        """
        Para cada (source, external_id), retorna dict com stats:
          {p10, p25, p50, p75, count, min, max}
        SKUs sem histórico (count<2) são omitidos do retorno.
        """
        out: dict[tuple[str, str], dict] = {}
        for source, eid in items:
            prices = self.get_price_history(source, eid, days=days)
            if len(prices) < 2:
                continue
            sorted_p = sorted(prices)
            n = len(sorted_p)

            def pct(p):
                # interpolação linear
                k = (n - 1) * p
                f = int(k)
                c = min(f + 1, n - 1)
                if f == c:
                    return sorted_p[f]
                return sorted_p[f] + (sorted_p[c] - sorted_p[f]) * (k - f)

            out[(source, eid)] = {
                "count": n,
                "min": sorted_p[0],
                "max": sorted_p[-1],
                "p10": pct(0.10),
                "p25": pct(0.25),
                "p50": pct(0.50),
                "p75": pct(0.75),
            }
        return out

    # --- candidates ---

    def insert_candidates(self, scan_id: int, candidates: Iterable[Candidate]) -> int:
        rows = []
        ts = _now_iso()
        for c in candidates:
            mr = c.market_reference
            via = c.viability
            h = c.history
            bm = c.benchmark
            # badge é derivado em tempo de notificação a partir de real_discount;
            # persiste pra consulta posterior + debug.
            bench_badge = None
            if bm is not None and bm.real_discount_pct is not None:
                from .benchmark import classify_real_discount
                bench_badge = classify_real_discount(bm.real_discount_pct)
            rows.append((
                scan_id, c.offer.source, c.offer.external_id,
                c.score, c.offer.discount_pct,
                json.dumps(c.reasons, ensure_ascii=False), ts,
                mr.query_used if mr else None,
                mr.median if mr else None,
                mr.p25 if mr else None,
                mr.p75 if mr else None,
                mr.count if mr else None,
                mr.search_url if mr else None,
                mr.match_confidence if mr else None,
                via.ml_sale_price if via else None,
                via.acquisition_cost if via else None,
                via.margin_brl if via else None,
                via.roi_pct if via else None,
                h.count if h else None,
                h.p10 if h else None,
                h.p50 if h else None,
                h.min if h else None,
                int(h.is_outlier) if h else None,
                bm.median_brl if bm else None,
                bm.count if bm else None,
                bm.real_discount_pct if bm else None,
                bench_badge,
                json.dumps(bm.sources_used, ensure_ascii=False) if bm else None,
            ))
        if not rows:
            return 0
        with self.transaction() as conn:
            conn.executemany(
                """INSERT INTO candidates
                       (scan_id, source, external_id, score, discount_pct,
                        reasons_json, ts,
                        ml_query, ml_median, ml_p25, ml_p75, ml_count, ml_search_url,
                        ml_match_confidence,
                        via_sale_price, via_acquisition, via_margin_brl, via_roi_pct,
                        hist_count, hist_p10, hist_p50, hist_min, hist_is_outlier,
                        bench_median, bench_count, bench_real_discount_pct,
                        bench_badge, bench_sources_json)
                   VALUES (?,?,?,?,?,?,?, ?,?,?,?,?,?,?, ?,?,?,?, ?,?,?,?,?,
                           ?,?,?,?,?)""",
                rows,
            )
        return len(rows)

    def list_candidates(self, *, scan_id: int | None = None,
                        status: str | None = None,
                        source: str | None = None,
                        top: int = 50) -> list[sqlite3.Row]:
        # Dedup persistente por (source, external_id): exclui ofertas que JÁ
        # foram notificadas em qualquer scan anterior, independente do
        # candidate_id atual. Cada nova oferta avisa só uma vez na vida.
        sql = """
            SELECT c.id, c.scan_id, c.score, c.discount_pct, c.reasons_json,
                   c.status, c.ts,
                   c.ml_query, c.ml_median, c.ml_p25, c.ml_count, c.ml_search_url,
                   c.ml_match_confidence,
                   c.via_sale_price, c.via_margin_brl, c.via_roi_pct,
                   o.source, o.external_id, o.title, o.url, o.price, o.old_price,
                   o.store_name, o.category, o.coupon_code, o.rating_score,
                   o.rating_count, o.popularity, o.image
            FROM candidates c
            JOIN offers o ON o.source = c.source AND o.external_id = c.external_id
            WHERE 1=1
              AND NOT EXISTS (
                  SELECT 1 FROM candidates cprev
                  WHERE cprev.source = c.source
                    AND cprev.external_id = c.external_id
                    AND cprev.notified_at IS NOT NULL
              )
        """
        params: list = []
        if scan_id is not None:
            sql += " AND c.scan_id = ?"
            params.append(scan_id)
        if status:
            sql += " AND c.status = ?"
            params.append(status)
        if source:
            sql += " AND c.source = ?"
            params.append(source)
        sql += " ORDER BY c.score DESC, c.discount_pct DESC LIMIT ?"
        params.append(top)
        with self._connect() as conn:
            return list(conn.execute(sql, params).fetchall())

    def update_candidate_status(self, candidate_id: int, status: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE candidates SET status=? WHERE id=?",
                (status, candidate_id),
            )

    def list_unnotified(self, *, min_roi_pct: float | None = None,
                        min_match_confidence: float | None = None,
                        min_discount_pct: float | None = None,
                        max_inflated_real_discount: float | None = None,
                        require_viability: bool = True,
                        limit: int = 50) -> list[sqlite3.Row]:
        """
        Candidatos ainda não notificados via Telegram.
        Filtros: ROI mínimo, match confidence mínimo, desconto mínimo (fallback
        sem ROI), exige cálculo de viabilidade.

        max_inflated_real_discount: se setado, filtra fora candidates com
        bench_real_discount_pct < esse valor (i.e. old_price totalmente
        inflado: a mediana cross-loja mostra que o "desconto" é falso).
        Candidates sem dados cross-loja (bench_real_discount_pct NULL)
        passam — não temos como dizer que é fake.

        Dedup cross-scan por (source, external_id):
          - exclui se algum candidate anterior já foi notificado
          - exclui se algum candidate anterior tem status != 'new'
            (o usuário já marcou bought/seen/ignored via botão ou CLI)
        """
        sql = """
            SELECT c.id, c.score, c.discount_pct, c.via_margin_brl, c.via_roi_pct,
                   c.via_sale_price, c.ml_search_url, c.ml_match_confidence,
                   c.ml_p25, c.ml_count,
                   c.hist_count, c.hist_p10, c.hist_p50, c.hist_min, c.hist_is_outlier,
                   c.bench_median, c.bench_count, c.bench_real_discount_pct,
                   c.bench_badge, c.bench_sources_json,
                   o.source, o.external_id, o.title, o.url, o.price, o.old_price,
                   o.store_name, o.coupon_code
            FROM candidates c
            JOIN offers o ON o.source = c.source AND o.external_id = c.external_id
            WHERE c.notified_at IS NULL
              AND c.status = 'new'
              AND NOT EXISTS (
                  SELECT 1 FROM candidates cprev
                  WHERE cprev.source = c.source
                    AND cprev.external_id = c.external_id
                    AND (cprev.notified_at IS NOT NULL
                         OR cprev.status != 'new')
              )
        """
        params: list = []
        if require_viability:
            sql += " AND c.via_roi_pct IS NOT NULL"
        if min_roi_pct is not None:
            sql += " AND c.via_roi_pct >= ?"
            params.append(min_roi_pct)
        if min_match_confidence is not None:
            sql += " AND (c.ml_match_confidence IS NULL OR c.ml_match_confidence >= ?)"
            params.append(min_match_confidence)
        if min_discount_pct is not None:
            sql += " AND c.discount_pct >= ?"
            params.append(min_discount_pct)
        if max_inflated_real_discount is not None:
            # mantém candidates sem dados (NULL) — só dropa os comprovadamente fake
            sql += (" AND (c.bench_real_discount_pct IS NULL"
                    "      OR c.bench_real_discount_pct >= ?)")
            params.append(max_inflated_real_discount)
        # Garante que cada (source, external_id) aparece só uma vez (o mais recente)
        sql += """
            AND c.id IN (
                SELECT MAX(id) FROM candidates
                WHERE notified_at IS NULL AND status = 'new'
                GROUP BY source, external_id
            )
            ORDER BY c.via_roi_pct DESC, c.score DESC
            LIMIT ?
        """
        params.append(limit)
        with self._connect() as conn:
            return list(conn.execute(sql, params).fetchall())

    # --- benchmark cache ---

    def read_benchmark_cache(self, query_hash: str,
                             cutoff_iso: str) -> sqlite3.Row | None:
        """Retorna a linha do cache se ts >= cutoff_iso; senão None."""
        with self._connect() as conn:
            return conn.execute(
                """SELECT query_used, median_brl, p25_brl, p75_brl, count,
                          sources_json, match_confidence, ts
                   FROM benchmark_cache
                   WHERE query_hash = ? AND ts >= ?""",
                (query_hash, cutoff_iso),
            ).fetchone()

    def write_benchmark_cache(self, *, query_hash: str, query_used: str,
                              median: float | None, p25: float | None,
                              p75: float | None, count: int,
                              sources: list[str],
                              match_confidence: float) -> None:
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO benchmark_cache (
                       query_hash, query_used, median_brl, p25_brl, p75_brl,
                       count, sources_json, match_confidence, ts
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(query_hash) DO UPDATE SET
                       query_used = excluded.query_used,
                       median_brl = excluded.median_brl,
                       p25_brl = excluded.p25_brl,
                       p75_brl = excluded.p75_brl,
                       count = excluded.count,
                       sources_json = excluded.sources_json,
                       match_confidence = excluded.match_confidence,
                       ts = excluded.ts""",
                (query_hash, query_used, median, p25, p75, count,
                 json.dumps(sources, ensure_ascii=False),
                 match_confidence, _now_iso()),
            )

    def purge_benchmark_cache(self, *, older_than_hours: int = 168) -> int:
        """Limpa cache antigo. Default: 7 dias (cache válido é 24h, mas
        manter histórico ajuda em rebuild rápido)."""
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(hours=older_than_hours)).isoformat(timespec="seconds")
        with self.transaction() as conn:
            cur = conn.execute(
                "DELETE FROM benchmark_cache WHERE ts < ?", (cutoff,),
            )
            return cur.rowcount

    def mark_notified(self, candidate_ids: list[int]) -> None:
        """
        Marca os candidates específicos como notificados. Combinado com o
        filtro NOT EXISTS em list_unnotified, isso é suficiente: scans futuros
        que criem novos candidate_ids pra mesma oferta são naturalmente
        excluídos pela presença desses candidatos antigos com notified_at.
        """
        if not candidate_ids:
            return
        ts = _now_iso()
        with self.transaction() as conn:
            conn.executemany(
                "UPDATE candidates SET notified_at = ? WHERE id = ?",
                [(ts, cid) for cid in candidate_ids],
            )

    def reset_notified(self, *, source: str | None = None) -> int:
        """Limpa flag notified_at — útil pra re-testar ou após mudança grande."""
        with self.transaction() as conn:
            if source:
                cur = conn.execute(
                    "UPDATE candidates SET notified_at = NULL WHERE source = ?",
                    (source,),
                )
            else:
                cur = conn.execute(
                    "UPDATE candidates SET notified_at = NULL"
                )
            return cur.rowcount
