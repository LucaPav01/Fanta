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

-- Forma lunga delle quattro medie d'asta: permette all'app di cambiare
-- formato lega da configurazione senza scegliere una colonna nel codice.
CREATE TABLE IF NOT EXISTS auction_price_estimates (
    estimate_id          TEXT PRIMARY KEY,
    player_id            TEXT NOT NULL REFERENCES players(player_id),
    source_record_id     TEXT NOT NULL REFERENCES player_source_records(source_record_id),
    teams_bucket         INTEGER NOT NULL CHECK (teams_bucket IN (8, 10)),
    budget_bucket        INTEGER NOT NULL CHECK (budget_bucket IN (350, 500)),
    average_price        REAL,
    valid_from           TEXT NOT NULL,
    UNIQUE (player_id, teams_bucket, budget_bucket, valid_from)
);

CREATE INDEX IF NOT EXISTS idx_auction_price_estimates_player
    ON auction_price_estimates(player_id, teams_bucket, budget_bucket, valid_from);

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
-- DATA_SOURCES: catalogo applicativo della provenienza. La data di
-- aggiornamento è quella del file sorgente; imported_at è invece il momento
-- in cui la pipeline lo ha letto. Nessuno dei due valori modifica il raw.
-- ============================================================
CREATE TABLE IF NOT EXISTS data_sources (
    source_name          TEXT PRIMARY KEY,
    display_name         TEXT NOT NULL,
    source_file          TEXT NOT NULL,
    source_url           TEXT,
    dataset_kind         TEXT NOT NULL,
    season               TEXT NOT NULL,
    retrieved_at         TEXT,
    imported_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    row_count            INTEGER NOT NULL DEFAULT 0,
    matched_count        INTEGER NOT NULL DEFAULT 0,
    data_status          TEXT NOT NULL CHECK (data_status IN ('available', 'missing', 'verify'))
);

-- Le squadre ammesse nell'app arrivano da app_config.json. In questo modo la
-- vista non incorpora una stagione o una lista club nel codice SQL.
CREATE TABLE IF NOT EXISTS competition_teams (
    season               TEXT NOT NULL,
    competition_name     TEXT NOT NULL,
    team_id              TEXT NOT NULL REFERENCES teams(team_id),
    PRIMARY KEY (season, competition_name, team_id)
);

CREATE TABLE IF NOT EXISTS app_settings (
    setting_key          TEXT PRIMARY KEY,
    setting_value        TEXT NOT NULL
);

-- Quotazioni ufficiali Fantacalcio.it normalizzate. FVM è deliberatamente
-- distinto dalla quotazione: è un valore editoriale d'asta su base 1000.
CREATE TABLE IF NOT EXISTS fantacalcio_it_quotations (
    quotation_id         TEXT PRIMARY KEY,
    player_id            TEXT NOT NULL REFERENCES players(player_id),
    source_record_id     TEXT NOT NULL REFERENCES player_source_records(source_record_id),
    role_classic         TEXT CHECK (role_classic IN ('GK', 'DEF', 'MID', 'FWD')),
    role_mantra          TEXT,
    quotation_initial    REAL,
    quotation_current    REAL,
    quotation_delta      REAL,
    fvm_classic_1000     REAL,
    fvm_mantra_1000      REAL,
    valid_from           TEXT NOT NULL,
    created_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (player_id, valid_from)
);

CREATE INDEX IF NOT EXISTS idx_fc_it_quotations_player
    ON fantacalcio_it_quotations(player_id, valid_from);

-- Statistiche ufficiali Fantacalcio.it: nomi e numeri sono convertiti in un
-- vocabolario stabile, mentre la riga originale resta in raw_data.
CREATE TABLE IF NOT EXISTS fantacalcio_it_statistics (
    statistic_id         TEXT PRIMARY KEY,
    player_id            TEXT NOT NULL REFERENCES players(player_id),
    source_record_id     TEXT NOT NULL REFERENCES player_source_records(source_record_id),
    role_classic         TEXT CHECK (role_classic IN ('GK', 'DEF', 'MID', 'FWD')),
    role_mantra          TEXT,
    appearances          INTEGER,
    average_rating       REAL,
    fantasy_average      REAL,
    goals_for            INTEGER,
    goals_against        INTEGER,
    penalties_saved      INTEGER,
    penalties_taken      INTEGER,
    penalties_scored     INTEGER,
    penalties_missed     INTEGER,
    assists              INTEGER,
    yellow_cards         INTEGER,
    red_cards            INTEGER,
    own_goals            INTEGER,
    valid_from           TEXT NOT NULL,
    created_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (player_id, valid_from)
);

CREATE INDEX IF NOT EXISTS idx_fc_it_statistics_player
    ON fantacalcio_it_statistics(player_id, valid_from);

CREATE TABLE IF NOT EXISTS metric_definitions (
    metric_key           TEXT PRIMARY KEY,
    display_name         TEXT NOT NULL,
    meaning              TEXT NOT NULL,
    unit                 TEXT NOT NULL,
    source_name          TEXT NOT NULL,
    verification_status TEXT NOT NULL CHECK (verification_status IN ('available', 'missing', 'verify'))
);

-- ============================================================
-- PLAYERS_ENRICHED: vista piatta di consultazione (join di tutte le entità,
-- ultimo valore disponibile per ciascuna fonte). Questa è la vista da esportare
-- in players_enriched.csv per uso quotidiano (asta, analisi).
-- ============================================================
DROP VIEW IF EXISTS players_enriched;
CREATE VIEW players_enriched AS
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

-- ============================================================
-- APP_PLAYERS: contratto dati del prodotto. Una sola riga per giocatore,
-- esclusivamente per i club della competizione configurata.
-- ============================================================
DROP VIEW IF EXISTS app_players;
CREATE VIEW app_players AS
WITH latest_auction AS (
    SELECT ap.* FROM auction_prices ap
    WHERE ap.valid_from = (
        SELECT MAX(x.valid_from) FROM auction_prices x WHERE x.player_id = ap.player_id
    )
), latest_snapshot AS (
    SELECT ps.* FROM player_snapshots ps
    WHERE ps.valid_from = (
        SELECT MAX(x.valid_from) FROM player_snapshots x WHERE x.player_id = ps.player_id
    )
), latest_quotation AS (
    SELECT fq.* FROM fantacalcio_it_quotations fq
    WHERE fq.valid_from = (
        SELECT MAX(x.valid_from) FROM fantacalcio_it_quotations x WHERE x.player_id = fq.player_id
    )
), latest_statistics AS (
    SELECT fs.* FROM fantacalcio_it_statistics fs
    WHERE fs.valid_from = (
        SELECT MAX(x.valid_from) FROM fantacalcio_it_statistics x WHERE x.player_id = fs.player_id
    )
), selected_auction_format AS (
    SELECT
        CAST((SELECT setting_value FROM app_settings WHERE setting_key = 'auction_teams') AS INTEGER) AS teams_bucket,
        CAST((SELECT setting_value FROM app_settings WHERE setting_key = 'auction_budget') AS INTEGER) AS budget_bucket
), latest_auction_estimate AS (
    SELECT ae.* FROM auction_price_estimates ae
    JOIN selected_auction_format af
      ON af.teams_bucket = ae.teams_bucket AND af.budget_bucket = ae.budget_bucket
    WHERE ae.valid_from = (
        SELECT MAX(x.valid_from) FROM auction_price_estimates x
        WHERE x.player_id = ae.player_id
          AND x.teams_bucket = ae.teams_bucket AND x.budget_bucket = ae.budget_bucket
    )
), alias_summary AS (
    SELECT player_id,
           group_concat(DISTINCT alias_raw) AS name_aliases,
           MAX(CASE WHEN match_method = 'fuzzy' OR COALESCE(match_confidence, 1) < 1
                    THEN 1 ELSE 0 END) AS needs_verification
    FROM player_aliases
    GROUP BY player_id
), source_summary AS (
    SELECT player_id,
           group_concat(DISTINCT source_name) AS source_names
    FROM player_source_records
    WHERE player_id IS NOT NULL
    GROUP BY player_id
), resolved_role AS (
    -- Se il ruolo canonico manca, ripiega sulle colonne ruolo_* dello snapshot
    -- Excel-Online (codici P/D/C/A, T=trequartista trattato come MID).
    SELECT p.player_id,
           COALESCE(
               la.ruolo, lq.role_classic, lst.role_classic,
               CASE ls.ruolo_fantacalcio_it
                   WHEN 'P' THEN 'GK' WHEN 'D' THEN 'DEF' WHEN 'C' THEN 'MID' WHEN 'A' THEN 'FWD' END,
               CASE ls.ruolo_standard
                   WHEN 'P' THEN 'GK' WHEN 'D' THEN 'DEF' WHEN 'C' THEN 'MID' WHEN 'A' THEN 'FWD' END,
               CASE ls.ruolo_trequartista
                   WHEN 'P' THEN 'GK' WHEN 'D' THEN 'DEF' WHEN 'C' THEN 'MID' WHEN 'A' THEN 'FWD' WHEN 'T' THEN 'MID' END
           ) AS role
    FROM players p
    LEFT JOIN latest_auction la ON la.player_id = p.player_id
    LEFT JOIN latest_quotation lq ON lq.player_id = p.player_id
    LEFT JOIN latest_statistics lst ON lst.player_id = p.player_id
    LEFT JOIN latest_snapshot ls ON ls.player_id = p.player_id
), fvm_tiers AS (
    -- Percentile del FVM tra i soli giocatori con ruolo e FVM noti, calcolato
    -- separatamente per ruolo (un FWD non va confrontato con un GK). PERCENT_RANK
    -- assegna lo stesso percentile a valori di FVM pari (parità), a differenza di
    -- RANK/ROW_NUMBER che li spezzerebbe arbitrariamente.
    SELECT rr.player_id,
           ROUND(PERCENT_RANK() OVER (
               PARTITION BY rr.role ORDER BY fq.fvm_classic_1000
           ) * 100, 1) AS fvm_percentile
    FROM resolved_role rr
    JOIN latest_quotation fq ON fq.player_id = rr.player_id
    WHERE rr.role IS NOT NULL AND fq.fvm_classic_1000 IS NOT NULL
)
SELECT
    p.player_id,
    p.canonical_full_name AS player_name,
    p.canonical_first_name AS first_name,
    p.canonical_last_name AS last_name,
    COALESCE(a.name_aliases, p.canonical_full_name) AS name_aliases,
    t.canonical_name AS team_name,
    ct.competition_name,
    ct.season,
    rr.role AS role,
    fq.fvm_classic_1000 AS fvm,
    CASE WHEN fq.fvm_classic_1000 IS NULL THEN NULL
         ELSE fq.fvm_classic_1000 * af.budget_bucket / 1000.0 END AS fvm_parametrized,
    af.budget_bucket AS fvm_budget,
    ft.fvm_percentile,
    CASE WHEN ft.fvm_percentile IS NULL THEN NULL
         WHEN ft.fvm_percentile >= 80 THEN 'Fascia 1'
         WHEN ft.fvm_percentile >= 60 THEN 'Fascia 2'
         WHEN ft.fvm_percentile >= 40 THEN 'Fascia 3'
         WHEN ft.fvm_percentile >= 20 THEN 'Fascia 4'
         ELSE 'Fascia 5' END AS fvm_tier,
    ae.average_price AS average_auction_price,
    af.teams_bucket AS auction_teams,
    af.budget_bucket AS auction_budget,
    ps.is_pct,
    ps.eta AS age,
    ps.rat AS rating,
    ps.pot AS potential,
    fs.appearances,
    fs.average_rating,
    fs.fantasy_average,
    COALESCE(qs.retrieved_at, fq.valid_from) AS fvm_updated_at,
    COALESCE(aps.retrieved_at, ap.valid_from) AS auction_price_updated_at,
    COALESCE(iss.retrieved_at, ps.valid_from) AS is_updated_at,
    CASE WHEN fq.fvm_classic_1000 IS NULL THEN 'missing'
         WHEN COALESCE(a.needs_verification, 0) = 1 THEN 'verify'
         ELSE 'available' END AS fvm_status,
    CASE WHEN ae.average_price IS NULL THEN 'missing'
         WHEN COALESCE(a.needs_verification, 0) = 1 THEN 'verify'
         ELSE 'available' END AS auction_price_status,
    CASE WHEN ps.is_pct IS NULL THEN 'missing'
         WHEN ps.is_pct < 0 OR ps.is_pct > 100 THEN 'verify'
         WHEN COALESCE(a.needs_verification, 0) = 1 THEN 'verify'
         ELSE 'available' END AS is_status,
    CASE WHEN COALESCE(a.needs_verification, 0) = 1 THEN 'verify'
         WHEN rr.role IS NULL
           OR (fq.fvm_classic_1000 IS NULL AND ae.average_price IS NULL AND ps.is_pct IS NULL)
           THEN 'missing'
         ELSE 'available' END AS data_status,
    COALESCE(s.source_names, '') AS source_names
FROM players p
JOIN teams t ON t.team_id = p.current_team_id
JOIN competition_teams ct ON ct.team_id = t.team_id
CROSS JOIN selected_auction_format af
LEFT JOIN latest_auction ap ON ap.player_id = p.player_id
LEFT JOIN latest_auction_estimate ae ON ae.player_id = p.player_id
LEFT JOIN latest_snapshot ps ON ps.player_id = p.player_id
LEFT JOIN latest_quotation fq ON fq.player_id = p.player_id
LEFT JOIN latest_statistics fs ON fs.player_id = p.player_id
LEFT JOIN alias_summary a ON a.player_id = p.player_id
LEFT JOIN source_summary s ON s.player_id = p.player_id
LEFT JOIN resolved_role rr ON rr.player_id = p.player_id
LEFT JOIN fvm_tiers ft ON ft.player_id = p.player_id
LEFT JOIN data_sources qs ON qs.source_name = 'fantacalcio-it-quotazioni'
LEFT JOIN data_sources aps ON aps.source_name = 'fantacalcio-online-csv'
LEFT JOIN data_sources iss ON iss.source_name = 'fantacalcio-online-excel';

DROP VIEW IF EXISTS app_data_catalog;
CREATE VIEW app_data_catalog AS
SELECT md.metric_key, md.display_name, md.meaning, md.unit,
       md.source_name, md.verification_status,
       ds.display_name AS source_display_name, ds.source_url,
       ds.retrieved_at AS source_updated_at, ds.imported_at, ds.data_status AS source_status
FROM metric_definitions md
LEFT JOIN data_sources ds ON ds.source_name = md.source_name;
