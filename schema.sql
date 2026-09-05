-- Schema canonico Fantacalcio DB personale
-- Dialetto: SQLite (portabile, zero setup, sufficiente per uso personale)
-- UUID: generati in Python (uuid4 per entità nuove, uuid5 deterministico per chiavi
-- derivate da stringhe normalizzate, cosi' import ripetuti dello stesso giocatore
-- producono sempre lo stesso player_id/team_id senza bisogno di lookup preventivo).

PRAGMA foreign_keys = ON;

-- ============================================================
-- TEAMS: entità squadra canonica
-- ============================================================
CREATE TABLE IF NOT EXISTS teams (
    team_id         TEXT PRIMARY KEY,      -- uuid5(NAMESPACE, nome_normalizzato)
    canonical_name  TEXT NOT NULL UNIQUE,  -- es. "Roma", "Inter"
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- ============================================================
-- TEAM_ALIASES: ogni variante di stringa vista nelle fonti, mappata alla squadra canonica
-- ============================================================
CREATE TABLE IF NOT EXISTS team_aliases (
    alias_id            TEXT PRIMARY KEY,  -- uuid5(NAMESPACE, source_name + alias_normalized)
    team_id             TEXT NOT NULL REFERENCES teams(team_id),
    alias_raw           TEXT NOT NULL,     -- stringa esatta cosi' come appare nella fonte
    alias_normalized    TEXT NOT NULL,     -- lowercase, trim, accenti rimossi
    source_name         TEXT NOT NULL,     -- es. "fantacalcio-online-csv"
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (source_name, alias_normalized)
);

-- ============================================================
-- PLAYERS: entità giocatore canonica (identità stabile nel tempo)
-- ============================================================
CREATE TABLE IF NOT EXISTS players (
    player_id           TEXT PRIMARY KEY,  -- uuid5(NAMESPACE, nome_normalizzato + squadra_normalizzata)
    canonical_full_name TEXT NOT NULL,     -- "Cognome Nome", forma leggibile
    canonical_last_name TEXT NOT NULL,
    canonical_first_name TEXT,             -- nullable: alcune fonti non separano cognome/nome
    current_team_id     TEXT REFERENCES teams(team_id),
    created_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_players_last_name ON players(canonical_last_name);

-- ============================================================
-- PLAYER_ALIASES: ogni variante di nome vista nelle fonti, mappata al giocatore canonico
-- ============================================================
CREATE TABLE IF NOT EXISTS player_aliases (
    alias_id            TEXT PRIMARY KEY,  -- uuid5(NAMESPACE, source_name + alias_normalized)
    player_id           TEXT NOT NULL REFERENCES players(player_id),
    alias_raw            TEXT NOT NULL,    -- stringa esatta cosi' come appare nella fonte (es. "MALENDonyell")
    alias_normalized     TEXT NOT NULL,    -- normalizzata per matching (lowercase, spazi, accenti)
    source_name           TEXT NOT NULL,
    match_method          TEXT NOT NULL CHECK (match_method IN ('exact', 'fuzzy', 'manual')),
    match_confidence      REAL,            -- 0.0-1.0, NULL se match_method = 'exact'
    created_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (source_name, alias_normalized)
);

-- ============================================================
-- PLAYER_SOURCE_RECORDS: provenienza grezza, una riga per ogni riga importata da una fonte.
-- Conserva TUTTE le colonne originali integralmente (raw_data), a prescindere
-- da come vengono poi interpretate/normalizzate a valle.
-- ============================================================
CREATE TABLE IF NOT EXISTS player_source_records (
    source_record_id    TEXT PRIMARY KEY,  -- uuid4, uno per riga fisica importata
    batch_id             TEXT NOT NULL,     -- uuid4 comune a tutte le righe di una stessa esecuzione di import
    source_name          TEXT NOT NULL,     -- es. "fantacalcio-online-csv", "statistiche-excel"
    source_file           TEXT NOT NULL,    -- nome file originale (es. "fantacalcio_prezzi.csv")
    source_row_number     INTEGER NOT NULL, -- numero riga nel file originale (1-based, header escluso)
    raw_data              TEXT NOT NULL,    -- JSON: {"colonna_originale": "valore_originale", ...} — nessuna trasformazione
    raw_hash              TEXT NOT NULL,    -- sha256(raw_data) — per dedup e rilevare righe invariate tra import
    ingested_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    player_id              TEXT REFERENCES players(player_id),   -- NULL finché non risolto dal matching
    team_id                TEXT REFERENCES teams(team_id),        -- NULL finché non risolto dal matching
    match_status            TEXT NOT NULL DEFAULT 'unmatched'
                             CHECK (match_status IN ('matched', 'ambiguous', 'unmatched')),
    UNIQUE (source_name, source_file, source_row_number, batch_id)
);

CREATE INDEX IF NOT EXISTS idx_source_records_batch ON player_source_records(batch_id);
CREATE INDEX IF NOT EXISTS idx_source_records_hash ON player_source_records(raw_hash);

-- ============================================================
-- AUCTION_PRICES: quotazioni/prezzi asta (fonte: CSV fantacalcio-online)
-- Una riga per (giocatore, data di validità) — storicizzabile nel tempo.
-- ============================================================
CREATE TABLE IF NOT EXISTS auction_prices (
    price_id            TEXT PRIMARY KEY,  -- uuid4
    player_id            TEXT NOT NULL REFERENCES players(player_id),
    team_id               TEXT REFERENCES teams(team_id),
    source_record_id      TEXT NOT NULL REFERENCES player_source_records(source_record_id),
    ruolo                 TEXT NOT NULL CHECK (ruolo IN ('GK', 'DEF', 'MID', 'FWD')),
    kap                    INTEGER,
    price_8sq_350          REAL,
    price_10sq_350         REAL,
    price_8sq_500          REAL,
    price_10sq_500         REAL,
    mv                     REAL,          -- valore medio, sempre normalizzato a punto decimale
    presenze               INTEGER,
    valid_from             TEXT NOT NULL, -- data (o stagione) a cui si riferisce la quotazione
    created_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (player_id, valid_from)
);

CREATE INDEX IF NOT EXISTS idx_auction_prices_player ON auction_prices(player_id, valid_from);

-- ============================================================
-- PLAYER_SNAPSHOTS: anagrafica + metriche di performance (fonte: Excel statistiche)
-- Una riga per (giocatore, data di validità) — storicizzabile nel tempo.
-- ============================================================
CREATE TABLE IF NOT EXISTS player_snapshots (
    snapshot_id           TEXT PRIMARY KEY, -- uuid4
    player_id              TEXT NOT NULL REFERENCES players(player_id),
    team_id                 TEXT REFERENCES teams(team_id),
    source_record_id        TEXT NOT NULL REFERENCES player_source_records(source_record_id),
    eta                      INTEGER,
    rat                      REAL,
    pot                      REAL,
    is_pct                   REAL,
    ruolo_standard           TEXT,          -- colonna "Ruolo standard" originale, non normalizzata
    ruolo_trequartista       TEXT,          -- colonna "Ruolo trequartista" originale
    ruolo_fantacalcio_it     TEXT,          -- colonna "Ruolo Fantacalcio.it" originale
    valid_from               TEXT NOT NULL,
    created_at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_player_snapshots_player ON player_snapshots(player_id, valid_from);

-- ============================================================
-- PLAYERS_ENRICHED: vista piatta di consultazione (join di tutte le entità,
-- ultimo valore disponibile per ciascuna fonte). Questa è la vista da esportare
-- in players_enriched.csv per uso quotidiano (asta, analisi).
-- ============================================================
CREATE VIEW IF NOT EXISTS players_enriched AS
SELECT
    p.player_id,
    p.canonical_full_name,
    t.canonical_name AS team_name,
    ap.ruolo,
    ap.kap,
    ap.price_8sq_350,
    ap.price_10sq_350,
    ap.price_8sq_500,
    ap.price_10sq_500,
    ap.mv,
    ap.presenze,
    ap.valid_from AS auction_valid_from,
    ps.eta,
    ps.rat,
    ps.pot,
    ps.is_pct,
    ps.ruolo_standard,
    ps.ruolo_trequartista,
    ps.ruolo_fantacalcio_it,
    ps.valid_from AS snapshot_valid_from
FROM players p
LEFT JOIN teams t ON t.team_id = p.current_team_id
LEFT JOIN auction_prices ap ON ap.player_id = p.player_id
    AND ap.valid_from = (
        SELECT MAX(ap2.valid_from) FROM auction_prices ap2 WHERE ap2.player_id = p.player_id
    )
LEFT JOIN player_snapshots ps ON ps.player_id = p.player_id
    AND ps.valid_from = (
        SELECT MAX(ps2.valid_from) FROM player_snapshots ps2 WHERE ps2.player_id = p.player_id
    );
