#!/usr/bin/env python3
"""
Pipeline di import Fantacalcio: collega ingestione immutabile, validazione
strutturale, staging, identity resolution (già prodotta da risolvi_identita.py
in player_aliases.csv / team_aliases.csv / match_review.csv), arricchimento e
pubblicazione, in un'unica esecuzione idempotente.

Ordine delle fasi (ognuna scrive solo ciò che le compete, mai sovrascrive
il raw):
  1. Ingestione: ogni riga di ogni fonte diventa un player_source_records,
     raw_data intatto, deduplicata per raw_hash (rerun senza dati cambiati
     non produce righe duplicate).
  2. Staging: normalizza.normalize_csv_row per il CSV; lettura diretta delle
     colonne per l'Excel-Online (sono già nel vocabolario canonico dello
     schema, nessuna trasformazione necessaria oltre al parsing numerico).
  3. Identity resolution: applica la mappa alias già calcolata (nessun nuovo
     matching qui: la gerarchia di risoluzione vive in risolvi_identita.py).
  4. Arricchimento: popola teams, players, auction_prices, player_snapshots.
  5. Controlli: verifica i conteggi attesi e produce un report di qualità.
  6. Pubblicazione: esporta players_enriched.csv per Google Sheets.
"""
import csv
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
import sys
import uuid
from pathlib import Path

import openpyxl

from normalizza import (
    make_key,
    normalize_csv_row,
    split_full_name,
    strip_nuovo_label,
    strip_season_suffix,
)
from risolvi_identita import (
    NAMESPACE,
    QUOT_IT_XLSX,
    QUOT_ONLINE_XLSX,
    STAT_XLSX,
    extract_cognome_quot_online,
    load_quot_online,
    load_xlsx_with_id,
)

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "fantacalcio.db"
SCHEMA_PATH = ROOT / "schema.sql"
PLAYER_ALIASES_PATH = ROOT / "player_aliases.csv"
TEAM_ALIASES_PATH = ROOT / "team_aliases.csv"
MATCH_REVIEW_PATH = ROOT / "match_review.csv"
DUPLICATE_OVERRIDES_PATH = ROOT / "duplicate_overrides.csv"
NAME_OVERRIDES_PATH = ROOT / "name_overrides.csv"
ENRICHED_EXPORT_PATH = ROOT / "players_enriched.csv"
QUALITY_REPORT_PATH = ROOT / "quality_report.txt"
APP_DATABASE_PATH = ROOT / "fantacalcio_app.db"
CONFIG_PATH = ROOT / "app_config.json"

CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
SEASON_VALID_FROM = CONFIG["season"]
CSV_PATH = ROOT / CONFIG["source_files"]["fantacalcio_online_csv"]

BATCH_ID = str(uuid.uuid4())


# ------------------------------------------------------------------
# Fase 1: ingestione grezza (raw_data integrale, per ogni riga fisica)
# ------------------------------------------------------------------
def iter_csv_raw(path: Path):
    with path.open(newline="", encoding="utf-8") as f:
        for row_number, row in enumerate(csv.DictReader(f), start=1):
            yield row_number, dict(row)


def iter_xlsx_with_id_raw(path: str):
    for d in load_xlsx_with_id(path):
        row_number = d["_row"]
        raw = {k: v for k, v in d.items() if k != "_row"}
        yield row_number, raw


def iter_quot_online_raw(path: str):
    for d in load_quot_online(path):
        yield d["row"], dict(d["raw"])


def json_safe(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def raw_hash(raw_data: dict) -> str:
    payload = json.dumps(raw_data, sort_keys=True, ensure_ascii=False, default=json_safe)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def ingest_source(conn, source_name: str, source_file: str, rows) -> dict:
    """Inserisce le righe non ancora viste (per hash) e ritorna la mappa
    source_row_number -> source_record_id per TUTTE le righe di questa fonte
    (sia quelle appena inserite sia quelle già presenti da run precedenti)."""
    cur = conn.cursor()
    result = {}
    inserted = 0
    reused = 0
    for row_number, raw in rows:
        h = raw_hash(raw)
        existing = cur.execute(
            "SELECT source_record_id FROM player_source_records "
            "WHERE source_name = ? AND source_file = ? AND source_row_number = ? AND raw_hash = ?",
            (source_name, source_file, row_number, h),
        ).fetchone()
        if existing:
            result[row_number] = existing[0]
            reused += 1
            continue

        record_id = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO player_source_records "
            "(source_record_id, batch_id, source_name, source_file, source_row_number, raw_data, raw_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (record_id, BATCH_ID, source_name, source_file, row_number,
             json.dumps(raw, ensure_ascii=False, default=json_safe), h),
        )
        result[row_number] = record_id
        inserted += 1
    conn.commit()
    print(f"  [{source_name}] righe totali: {len(result)}  nuove: {inserted}  invariate (riusate): {reused}")
    return result


# ------------------------------------------------------------------
# Fase 3: identity resolution (letta da player_aliases.csv / team_aliases.csv,
# prodotti da risolvi_identita.py — nessun nuovo matching qui)
# ------------------------------------------------------------------
def load_player_aliases() -> list[dict]:
    with PLAYER_ALIASES_PATH.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_team_aliases() -> list[dict]:
    with TEAM_ALIASES_PATH.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_match_review_keys() -> set:
    with MATCH_REVIEW_PATH.open(newline="", encoding="utf-8") as f:
        return {(r["source_name"], int(r["source_row_number"])) for r in csv.DictReader(f)}


def upsert_teams_and_aliases(conn, team_aliases: list[dict]) -> None:
    cur = conn.cursor()
    seen_teams = {}
    for row in team_aliases:
        team_id = row["team_id"]
        if team_id not in seen_teams:
            seen_teams[team_id] = row["canonical_name"]
            cur.execute("INSERT OR IGNORE INTO teams (team_id, canonical_name) VALUES (?, ?)",
                        (team_id, row["canonical_name"]))
        cur.execute(
            "INSERT OR IGNORE INTO team_aliases "
            "(alias_id, team_id, alias_raw, alias_normalized, source_name) VALUES (?, ?, ?, ?, ?)",
            (row["alias_id"], team_id, row["alias_raw"], row["alias_normalized"], row["source_name"]),
        )
    conn.commit()
    print(f"  teams: {len(seen_teams)}  team_aliases: {len(team_aliases)}")


def upsert_players_and_aliases(conn, player_aliases: list[dict]) -> None:
    cur = conn.cursor()
    aliases_by_player = {}
    for row in player_aliases:
        aliases_by_player.setdefault(row["player_id"], []).append(row)

    def preferred_identity(rows):
        priority = {
            "fantacalcio-online-excel": 0,
            "fantacalcio-online-csv": 1,
            "fantacalcio-it-statistiche": 2,
            "fantacalcio-it-quotazioni": 3,
        }
        ordered = sorted(rows, key=lambda r: priority.get(r["source_name"], 99))
        for candidate in ordered:
            raw_name = (candidate["alias_raw"] or "").strip()
            if candidate["source_name"] == "fantacalcio-online-excel":
                surname = extract_cognome_quot_online(raw_name)
                given_name = raw_name[len(surname or ""):].strip(" -")
                if surname and given_name:
                    return f"{given_name} {surname.title()}", surname.title(), given_name
            if candidate["source_name"] == "fantacalcio-online-csv":
                clean_name, _ = strip_season_suffix(raw_name)
                clean_name, _ = strip_nuovo_label(clean_name)
                surname, given_name, ambiguous = split_full_name(clean_name)
                if not ambiguous and surname and given_name:
                    surname = surname.title()
                    return f"{given_name} {surname}", surname, given_name
        display = (ordered[0]["canonical_name"] or ordered[0]["alias_raw"]).strip()
        return display, display.split()[0], None

    seen_players = set()
    for row in player_aliases:
        player_id = row["player_id"]
        if player_id not in seen_players:
            seen_players.add(player_id)
            full_name, last_name, first_name = preferred_identity(aliases_by_player[player_id])
            team_id = str(uuid.uuid5(NAMESPACE, f"team:{row['canonical_team_key']}"))
            cur.execute(
                "INSERT INTO players "
                "(player_id, canonical_full_name, canonical_last_name, canonical_first_name, current_team_id) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(player_id) DO UPDATE SET "
                "canonical_full_name=excluded.canonical_full_name, "
                "canonical_last_name=excluded.canonical_last_name, "
                "canonical_first_name=excluded.canonical_first_name, "
                "current_team_id=excluded.current_team_id, "
                "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')",
                (player_id, full_name, last_name, first_name, team_id),
            )
        confidence = float(row["match_confidence"]) if row["match_confidence"] else None
        cur.execute(
            "INSERT OR IGNORE INTO player_aliases "
            "(alias_id, player_id, alias_raw, alias_normalized, source_name, match_method, match_confidence) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (row["alias_id"], player_id, row["alias_raw"], row["alias_normalized"],
             row["source_name"], row["match_method"], confidence),
        )
    conn.commit()
    print(f"  players: {len(seen_players)}  player_aliases: {len(player_aliases)}")


def link_source_records(conn, source_name: str, record_ids: dict, player_by_row: dict,
                         team_by_row: dict, review_keys: set) -> None:
    cur = conn.cursor()
    for row_number, source_record_id in record_ids.items():
        player_id = player_by_row.get(row_number)
        team_id = team_by_row.get(row_number)
        if player_id:
            status = "matched"
        elif (source_name, row_number) in review_keys:
            status = "ambiguous"
        else:
            status = "unmatched"
        cur.execute(
            "UPDATE player_source_records SET player_id = ?, team_id = ?, match_status = ? "
            "WHERE source_record_id = ?",
            (player_id, team_id, status, source_record_id),
        )
    conn.commit()


# ------------------------------------------------------------------
# Fase 4: arricchimento — auction_prices (CSV) e player_snapshots (Excel-Online)
# ------------------------------------------------------------------
def build_auction_prices(conn, record_ids: dict, player_by_row: dict, team_by_row: dict) -> int:
    with CSV_PATH.open(newline="", encoding="utf-8") as f:
        staged = [normalize_csv_row(row, i + 1) for i, row in enumerate(csv.DictReader(f))]

    cur = conn.cursor()
    n = 0
    for r in staged:
        row_number = r["source_row_number"]
        player_id = player_by_row.get(row_number)
        if not player_id or r["ruolo_normalizzato"] is None:
            continue  # riga in match_review o ruolo non mappato: nessuna quotazione fabbricata
        source_record_id = record_ids[row_number]
        team_id = team_by_row.get(row_number)
        price_id = str(uuid.uuid5(NAMESPACE, f"price:{player_id}:{SEASON_VALID_FROM}"))
        cur.execute(
            "INSERT INTO auction_prices "
            "(price_id, player_id, team_id, source_record_id, ruolo, kap, "
            "price_8sq_350, price_10sq_350, price_8sq_500, price_10sq_500, mv, presenze, valid_from) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(player_id, valid_from) DO UPDATE SET "
            "team_id=excluded.team_id, source_record_id=excluded.source_record_id, ruolo=excluded.ruolo, "
            "kap=excluded.kap, price_8sq_350=excluded.price_8sq_350, price_10sq_350=excluded.price_10sq_350, "
            "price_8sq_500=excluded.price_8sq_500, price_10sq_500=excluded.price_10sq_500, "
            "mv=excluded.mv, presenze=excluded.presenze",
            (price_id, player_id, team_id, source_record_id, r["ruolo_normalizzato"], r["kap"],
             r["price_8sq_350"], r["price_10sq_350"], r["price_8sq_500"], r["price_10sq_500"],
             r["mv"], r["presenze"], SEASON_VALID_FROM),
        )
        for teams_bucket, budget_bucket, value in (
            (8, 350, r["price_8sq_350"]),
            (10, 350, r["price_10sq_350"]),
            (8, 500, r["price_8sq_500"]),
            (10, 500, r["price_10sq_500"]),
        ):
            estimate_id = str(uuid.uuid5(
                NAMESPACE,
                f"auction-estimate:{player_id}:{teams_bucket}:{budget_bucket}:{SEASON_VALID_FROM}",
            ))
            cur.execute(
                "INSERT INTO auction_price_estimates "
                "(estimate_id, player_id, source_record_id, teams_bucket, budget_bucket, average_price, valid_from) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(player_id, teams_bucket, budget_bucket, valid_from) DO UPDATE SET "
                "source_record_id=excluded.source_record_id, average_price=excluded.average_price",
                (estimate_id, player_id, source_record_id, teams_bucket, budget_bucket, value, SEASON_VALID_FROM),
            )
        n += 1
    conn.commit()
    return n


def parse_num(value):
    if value is None or value == "":
        return None
    return float(str(value).replace(",", "."))


ROLE_MAP_FANTACALCIO_IT = {"P": "GK", "D": "DEF", "C": "MID", "A": "FWD"}


def first_value(raw: dict, *keys):
    """Legge una colonna accettando le varianti note degli export ufficiali."""
    for key in keys:
        if key in raw and raw[key] not in (None, ""):
            return raw[key]
    return None


def parse_int_num(value):
    parsed = parse_num(value)
    return None if parsed is None else int(parsed)


def normalize_fc_it_quotation(raw: dict) -> dict:
    role_raw = str(first_value(raw, "R", "Ruolo") or "").strip().upper()
    return {
        "role_classic": ROLE_MAP_FANTACALCIO_IT.get(role_raw),
        "role_mantra": first_value(raw, "Rm", "R M", "Ruolo Mantra"),
        "quotation_initial": parse_num(first_value(raw, "Qt.I", "Qt. I", "Quotazione iniziale")),
        "quotation_current": parse_num(first_value(raw, "Qt.A", "Qt. A", "Quotazione attuale")),
        "quotation_delta": parse_num(first_value(raw, "Diff.", "Diff", "Differenza")),
        "fvm_classic_1000": parse_num(first_value(raw, "FVM", "FVM Classic")),
        "fvm_mantra_1000": parse_num(first_value(raw, "FVM M", "FVM Mantra")),
    }


def normalize_fc_it_statistics(raw: dict) -> dict:
    role_raw = str(first_value(raw, "R", "Ruolo") or "").strip().upper()
    return {
        "role_classic": ROLE_MAP_FANTACALCIO_IT.get(role_raw),
        "role_mantra": first_value(raw, "Rm", "R M", "Ruolo Mantra"),
        "appearances": parse_int_num(first_value(raw, "Pg", "PG", "Presenze")),
        "average_rating": parse_num(first_value(raw, "Mv", "MV", "Media Voto")),
        "fantasy_average": parse_num(first_value(raw, "Fm", "FM", "Mf", "Fantamedia")),
        "goals_for": parse_int_num(first_value(raw, "Gf", "GF", "Gol fatti")),
        "goals_against": parse_int_num(first_value(raw, "Gs", "GS", "Gol subiti")),
        "penalties_saved": parse_int_num(first_value(raw, "Rp", "RP", "Rigori parati")),
        "penalties_taken": parse_int_num(first_value(raw, "Rc", "RC", "Rigori calciati")),
        "penalties_scored": parse_int_num(first_value(raw, "R+", "Rigori segnati")),
        "penalties_missed": parse_int_num(first_value(raw, "R-", "Rigori sbagliati")),
        "assists": parse_int_num(first_value(raw, "Ass", "Assist")),
        "yellow_cards": parse_int_num(first_value(raw, "Amm", "Ammonizioni")),
        "red_cards": parse_int_num(first_value(raw, "Esp", "Espulsioni")),
        "own_goals": parse_int_num(first_value(raw, "Au", "Autogol")),
    }


def build_fc_it_quotations(conn, rows, record_ids: dict, player_by_row: dict) -> int:
    columns = (
        "role_classic", "role_mantra", "quotation_initial", "quotation_current",
        "quotation_delta", "fvm_classic_1000", "fvm_mantra_1000",
    )
    cur = conn.cursor()
    n = 0
    for raw in rows:
        row_number = raw["_row"]
        player_id = player_by_row.get(row_number)
        if not player_id:
            continue
        normalized = normalize_fc_it_quotation(raw)
        quotation_id = str(uuid.uuid5(NAMESPACE, f"fc-it-quotation:{player_id}:{SEASON_VALID_FROM}"))
        cur.execute(
            f"INSERT INTO fantacalcio_it_quotations "
            f"(quotation_id, player_id, source_record_id, {', '.join(columns)}, valid_from) "
            f"VALUES (?, ?, ?, {', '.join('?' for _ in columns)}, ?) "
            "ON CONFLICT(player_id, valid_from) DO UPDATE SET "
            "source_record_id=excluded.source_record_id, role_classic=excluded.role_classic, "
            "role_mantra=excluded.role_mantra, quotation_initial=excluded.quotation_initial, "
            "quotation_current=excluded.quotation_current, quotation_delta=excluded.quotation_delta, "
            "fvm_classic_1000=excluded.fvm_classic_1000, fvm_mantra_1000=excluded.fvm_mantra_1000",
            (quotation_id, player_id, record_ids[row_number],
             *(normalized[column] for column in columns), SEASON_VALID_FROM),
        )
        n += 1
    conn.commit()
    return n


def build_fc_it_statistics(conn, rows, record_ids: dict, player_by_row: dict) -> int:
    columns = (
        "role_classic", "role_mantra", "appearances", "average_rating", "fantasy_average",
        "goals_for", "goals_against", "penalties_saved", "penalties_taken",
        "penalties_scored", "penalties_missed", "assists", "yellow_cards", "red_cards", "own_goals",
    )
    cur = conn.cursor()
    n = 0
    for raw in rows:
        row_number = raw["_row"]
        player_id = player_by_row.get(row_number)
        if not player_id:
            continue
        normalized = normalize_fc_it_statistics(raw)
        statistic_id = str(uuid.uuid5(NAMESPACE, f"fc-it-statistic:{player_id}:{SEASON_VALID_FROM}"))
        cur.execute(
            f"INSERT INTO fantacalcio_it_statistics "
            f"(statistic_id, player_id, source_record_id, {', '.join(columns)}, valid_from) "
            f"VALUES (?, ?, ?, {', '.join('?' for _ in columns)}, ?) "
            "ON CONFLICT(player_id, valid_from) DO UPDATE SET "
            "source_record_id=excluded.source_record_id, " +
            ", ".join(f"{column}=excluded.{column}" for column in columns),
            (statistic_id, player_id, record_ids[row_number],
             *(normalized[column] for column in columns), SEASON_VALID_FROM),
        )
        n += 1
    conn.commit()
    return n


def build_player_snapshots(conn, record_ids: dict, player_by_row: dict, team_by_row: dict) -> int:
    rows = load_quot_online(QUOT_ONLINE_XLSX)
    cur = conn.cursor()
    n = 0
    for d in rows:
        row_number = d["row"]
        player_id = player_by_row.get(row_number)
        if not player_id:
            continue  # riga in match_review: nessuno snapshot fabbricato
        raw = d["raw"]
        source_record_id = record_ids[row_number]
        team_id = team_by_row.get(row_number)
        snapshot_id = str(uuid.uuid5(NAMESPACE, f"snapshot:{player_id}:{SEASON_VALID_FROM}"))
        # idempotenza: nessun vincolo UNIQUE su player_snapshots, si sostituisce a mano
        cur.execute("DELETE FROM player_snapshots WHERE player_id = ? AND valid_from = ?",
                    (player_id, SEASON_VALID_FROM))
        cur.execute(
            "INSERT INTO player_snapshots "
            "(snapshot_id, player_id, team_id, source_record_id, eta, rat, pot, is_pct, "
            "ruolo_standard, ruolo_trequartista, ruolo_fantacalcio_it, valid_from) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (snapshot_id, player_id, team_id, source_record_id,
             parse_num(raw.get("ETA'")), parse_num(raw.get("RAT")), parse_num(raw.get("POT")),
             parse_num(raw.get("IS %")), raw.get("Ruolo standard"), raw.get("Ruolo trequartista"),
             raw.get("Ruolo Fantacalcio.it"), SEASON_VALID_FROM),
        )
        n += 1
    conn.commit()
    return n


SOURCE_CATALOG = {
    "fantacalcio-online-csv": {
        "display_name": "Fantacalcio-Online · stime d'asta",
        "source_url": "https://www.fantacalcio-online.com/it/asta-fantacalcio-stima-prezzi",
        "dataset_kind": "auction_prices",
    },
    "fantacalcio-online-excel": {
        "display_name": "Fantacalcio-Online · profili giocatori",
        "source_url": "https://www.fantacalcio-online.com/it/",
        "dataset_kind": "player_profiles",
    },
    "fantacalcio-it-statistiche": {
        "display_name": "Fantacalcio.it · statistiche",
        "source_url": "https://www.fantacalcio.it/statistiche-serie-a",
        "dataset_kind": "statistics",
    },
    "fantacalcio-it-quotazioni": {
        "display_name": "Fantacalcio.it · quotazioni",
        "source_url": "https://www.fantacalcio.it/quotazioni-fantacalcio",
        "dataset_kind": "quotations",
    },
}


def file_updated_at(path) -> str | None:
    source_path = Path(path)
    if not source_path.exists():
        return None
    return datetime.fromtimestamp(source_path.stat().st_mtime, timezone.utc).isoformat().replace("+00:00", "Z")


def sync_data_sources(conn, source_files: dict) -> None:
    cur = conn.cursor()
    for source_name, source_file in source_files.items():
        catalog = SOURCE_CATALOG[source_name]
        counts = cur.execute(
            "SELECT COUNT(*), "
            "SUM(CASE WHEN match_status = 'matched' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN match_status = 'ambiguous' THEN 1 ELSE 0 END) "
            "FROM player_source_records WHERE source_name = ?",
            (source_name,),
        ).fetchone()
        row_count, matched_count, ambiguous_count = (value or 0 for value in counts)
        status = "missing" if row_count == 0 else ("verify" if ambiguous_count else "available")
        cur.execute(
            "INSERT INTO data_sources "
            "(source_name, display_name, source_file, source_url, dataset_kind, season, "
            "retrieved_at, row_count, matched_count, data_status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(source_name) DO UPDATE SET "
            "display_name=excluded.display_name, source_file=excluded.source_file, "
            "source_url=excluded.source_url, dataset_kind=excluded.dataset_kind, season=excluded.season, "
            "retrieved_at=excluded.retrieved_at, imported_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'), "
            "row_count=excluded.row_count, matched_count=excluded.matched_count, data_status=excluded.data_status",
            (source_name, catalog["display_name"], Path(source_file).name, catalog["source_url"],
             catalog["dataset_kind"], SEASON_VALID_FROM, file_updated_at(source_file),
             row_count, matched_count, status),
        )
    conn.commit()


def sync_app_configuration(conn, team_aliases: list[dict]) -> None:
    cur = conn.cursor()
    competition = CONFIG["competition"]
    auction_teams = CONFIG["auction"]["teams"]
    auction_budget = CONFIG["auction"]["budget"]
    if auction_teams not in (8, 10) or auction_budget not in (350, 500):
        raise ValueError("Formato asta non disponibile: usare squadre 8/10 e budget 350/500")
    cur.executemany(
        "INSERT INTO app_settings(setting_key, setting_value) VALUES (?, ?) "
        "ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value",
        (
            ("season", SEASON_VALID_FROM),
            ("competition", competition),
            ("auction_teams", str(auction_teams)),
            ("auction_budget", str(auction_budget)),
        ),
    )
    cur.execute(
        "DELETE FROM competition_teams WHERE season = ? AND competition_name = ?",
        (SEASON_VALID_FROM, competition),
    )
    canonical_team_ids = {}
    for row in team_aliases:
        canonical_team_ids.setdefault(row["canonical_name"].casefold(), row["team_id"])
    missing = []
    for team_name in CONFIG["serie_a_teams"]:
        team_id = canonical_team_ids.get(team_name.casefold())
        if not team_id:
            missing.append(team_name)
            continue
        cur.execute(
            "INSERT INTO competition_teams(season, competition_name, team_id) VALUES (?, ?, ?)",
            (SEASON_VALID_FROM, competition, team_id),
        )
    if missing:
        raise ValueError(f"Squadre configurate senza alias canonico: {', '.join(missing)}")

    metrics = (
        ("fvm", "FVM", "Valore teorico editoriale del giocatore in asta, scalabile sul budget della lega.",
         "crediti su base 1000", "fantacalcio-it-quotazioni", "available"),
        ("average_auction_price", "Prezzo medio d'asta",
         "Prezzo medio osservato nelle aste da 9-11 squadre con budget compreso tra 440 e 560 crediti.",
         "crediti", "fantacalcio-online-csv", "available"),
        ("is_pct", "IS", "Indice di schierabilità stagionale: probabilità stimata di prendere voto in una giornata.",
         "percentuale 0-100", "fantacalcio-online-excel", "available"),
    )
    cur.executemany(
        "INSERT INTO metric_definitions "
        "(metric_key, display_name, meaning, unit, source_name, verification_status) "
        "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(metric_key) DO UPDATE SET "
        "display_name=excluded.display_name, meaning=excluded.meaning, unit=excluded.unit, "
        "source_name=excluded.source_name, verification_status=excluded.verification_status",
        metrics,
    )
    conn.commit()


# ------------------------------------------------------------------
# Fase 4b: fusione duplicati identità (stessa persona, player_id diversi
# sfuggiti alle due passate di matching indipendenti) + correzione nomi.
# Applicata DOPO l'arricchimento e PRIMA dei controlli/pubblicazione, cosi'
# che data_status/fvm_status/auction_price_status/is_status (calcolati dalla
# vista app_players sui dati gia' fusi) non marchino piu' "mancante" una
# scheda che in realta' ha i numeri, solo sparsi tra le due righe originarie.
# ------------------------------------------------------------------
def load_duplicate_overrides() -> list[dict]:
    if not DUPLICATE_OVERRIDES_PATH.exists():
        return []
    with DUPLICATE_OVERRIDES_PATH.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_name_overrides() -> list[dict]:
    if not NAME_OVERRIDES_PATH.exists():
        return []
    with NAME_OVERRIDES_PATH.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def merge_child_rows(cur, table: str, id_column: str, unique_columns: tuple, fillable_columns: tuple,
                      principal_id: str, merged_id: str) -> None:
    """Riassegna al player_id principale le righe della tabella che appartenevano
    al player_id fuso. Se il principale ha gia' una riga con la stessa chiave
    (unique_columns), riempie solo i campi vuoti e scarta la riga fusa."""
    columns = [row[1] for row in cur.execute(f"PRAGMA table_info({table})").fetchall()]
    merged_rows = cur.execute(f"SELECT * FROM {table} WHERE player_id = ?", (merged_id,)).fetchall()
    for merged_row in merged_rows:
        merged = dict(zip(columns, merged_row))
        conditions = ["player_id = ?"]
        params = [principal_id]
        for column in unique_columns:
            if column == "player_id":
                continue
            conditions.append(f"{column} = ?")
            params.append(merged[column])
        existing = cur.execute(
            f"SELECT * FROM {table} WHERE {' AND '.join(conditions)}", params
        ).fetchone()
        if existing is None:
            cur.execute(f"UPDATE {table} SET player_id = ? WHERE {id_column} = ?",
                        (principal_id, merged[id_column]))
            continue
        existing_row = dict(zip(columns, existing))
        updates = {
            column: merged[column]
            for column in fillable_columns
            if existing_row.get(column) in (None, "") and merged.get(column) not in (None, "")
        }
        if updates:
            set_clause = ", ".join(f"{column} = ?" for column in updates)
            cur.execute(f"UPDATE {table} SET {set_clause} WHERE {id_column} = ?",
                        (*updates.values(), existing_row[id_column]))
        cur.execute(f"DELETE FROM {table} WHERE {id_column} = ?", (merged[id_column],))


def merge_player_aliases(cur, principal_id: str, merged_id: str) -> None:
    """Unisce gli alias cosi' che la ricerca trovi il giocatore con entrambe
    le grafie viste nelle fonti."""
    rows = cur.execute(
        "SELECT alias_id, alias_normalized, source_name FROM player_aliases WHERE player_id = ?",
        (merged_id,),
    ).fetchall()
    for alias_id, alias_normalized, source_name in rows:
        conflict = cur.execute(
            "SELECT 1 FROM player_aliases WHERE source_name = ? AND alias_normalized = ? AND player_id != ?",
            (source_name, alias_normalized, merged_id),
        ).fetchone()
        if conflict:
            cur.execute("DELETE FROM player_aliases WHERE alias_id = ?", (alias_id,))
        else:
            cur.execute("UPDATE player_aliases SET player_id = ? WHERE alias_id = ?", (principal_id, alias_id))


def apply_duplicate_overrides(conn) -> int:
    overrides = load_duplicate_overrides()
    cur = conn.cursor()
    merged_count = 0
    for row in overrides:
        principal_id = row["player_id_principale"].strip()
        merged_id = row["player_id_da_fondere"].strip()
        if not cur.execute("SELECT 1 FROM players WHERE player_id = ?", (merged_id,)).fetchone():
            continue

        merge_child_rows(cur, "auction_prices", "price_id", ("player_id", "valid_from"),
                          ("team_id", "source_record_id", "ruolo", "kap", "price_8sq_350",
                           "price_10sq_350", "price_8sq_500", "price_10sq_500", "mv", "presenze"),
                          principal_id, merged_id)
        merge_child_rows(cur, "auction_price_estimates", "estimate_id",
                          ("player_id", "teams_bucket", "budget_bucket", "valid_from"),
                          ("source_record_id", "average_price"), principal_id, merged_id)
        merge_child_rows(cur, "player_snapshots", "snapshot_id", ("player_id", "valid_from"),
                          ("team_id", "source_record_id", "eta", "rat", "pot", "is_pct",
                           "ruolo_standard", "ruolo_trequartista", "ruolo_fantacalcio_it"),
                          principal_id, merged_id)
        merge_child_rows(cur, "fantacalcio_it_quotations", "quotation_id", ("player_id", "valid_from"),
                          ("source_record_id", "role_classic", "role_mantra", "quotation_initial",
                           "quotation_current", "quotation_delta", "fvm_classic_1000", "fvm_mantra_1000"),
                          principal_id, merged_id)
        merge_child_rows(cur, "fantacalcio_it_statistics", "statistic_id", ("player_id", "valid_from"),
                          ("source_record_id", "role_classic", "role_mantra", "appearances",
                           "average_rating", "fantasy_average", "goals_for", "goals_against",
                           "penalties_saved", "penalties_taken", "penalties_scored", "penalties_missed",
                           "assists", "yellow_cards", "red_cards", "own_goals"),
                          principal_id, merged_id)

        cur.execute("UPDATE player_source_records SET player_id = ? WHERE player_id = ?",
                    (principal_id, merged_id))
        merge_player_aliases(cur, principal_id, merged_id)

        principal_first_name, principal_team_id = cur.execute(
            "SELECT canonical_first_name, current_team_id FROM players WHERE player_id = ?",
            (principal_id,),
        ).fetchone()
        merged_first_name, merged_team_id = cur.execute(
            "SELECT canonical_first_name, current_team_id FROM players WHERE player_id = ?",
            (merged_id,),
        ).fetchone()
        cur.execute(
            "UPDATE players SET canonical_first_name = ?, current_team_id = ? WHERE player_id = ?",
            (principal_first_name or merged_first_name, principal_team_id or merged_team_id, principal_id),
        )
        cur.execute("DELETE FROM players WHERE player_id = ?", (merged_id,))
        merged_count += 1
    conn.commit()
    print(f"  giocatori fusi da duplicate_overrides.csv: {merged_count}")
    return merged_count


def apply_name_overrides(conn) -> int:
    overrides = load_name_overrides()
    cur = conn.cursor()
    applied = 0
    for row in overrides:
        cur.execute(
            "UPDATE players SET canonical_full_name = ? WHERE player_id = ?",
            (row["nome_corretto"].strip(), row["player_id"].strip()),
        )
        applied += cur.rowcount
    conn.commit()
    print(f"  nomi corretti da name_overrides.csv: {applied}")
    return applied


# ------------------------------------------------------------------
# Fase 5: controlli di qualità
# ------------------------------------------------------------------
def run_quality_checks(conn, counts: dict) -> str:
    cur = conn.cursor()
    lines = []

    def emit(line=""):
        lines.append(line)
        print(line)

    emit("=== REPORT QUALITÀ IMPORT ===")
    emit()
    emit("-- Conteggi righe sorgente (ingestione, nessuna riga persa) --")
    expected = {
        "fantacalcio-online-csv": 716,
        "fantacalcio-online-excel": 565,
        "fantacalcio-it-statistiche": 593,
        "fantacalcio-it-quotazioni": 531,
    }
    for source_name, expected_count in expected.items():
        actual = counts[source_name]
        status = "OK" if actual == expected_count else "MISMATCH"
        emit(f"  {source_name}: attese {expected_count}, ingerite {actual}  [{status}]")

    emit()
    emit("-- Copertura match_status per fonte (nessuna riga sparita) --")
    for source_name in expected:
        rows = cur.execute(
            "SELECT match_status, COUNT(*) FROM player_source_records "
            "WHERE source_name = ? GROUP BY match_status", (source_name,)
        ).fetchall()
        by_status = dict(rows)
        total = sum(by_status.values())
        emit(f"  {source_name}: totale={total}  " +
             "  ".join(f"{status}={n}" for status, n in sorted(by_status.items())))

    emit()
    emit("-- Entità canoniche --")
    n_players = cur.execute("SELECT COUNT(*) FROM players").fetchone()[0]
    n_teams = cur.execute("SELECT COUNT(*) FROM teams").fetchone()[0]
    n_prices = cur.execute("SELECT COUNT(*) FROM auction_prices").fetchone()[0]
    n_snapshots = cur.execute("SELECT COUNT(*) FROM player_snapshots").fetchone()[0]
    emit(f"  players: {n_players}  teams: {n_teams}")
    n_fc_quotes = cur.execute("SELECT COUNT(*) FROM fantacalcio_it_quotations").fetchone()[0]
    n_fc_stats = cur.execute("SELECT COUNT(*) FROM fantacalcio_it_statistics").fetchone()[0]
    n_app_players = cur.execute("SELECT COUNT(*) FROM app_players").fetchone()[0]
    emit(f"  auction_prices: {n_prices}  player_snapshots: {n_snapshots}")
    emit(f"  fantacalcio_it_quotations: {n_fc_quotes}  fantacalcio_it_statistics: {n_fc_stats}")
    emit(f"  app_players (solo Serie A): {n_app_players}")

    emit()
    emit("-- Copertura incrociata quotazione/anagrafica --")
    only_price = cur.execute(
        "SELECT COUNT(*) FROM auction_prices ap "
        "WHERE NOT EXISTS (SELECT 1 FROM player_snapshots ps WHERE ps.player_id = ap.player_id)"
    ).fetchone()[0]
    only_snapshot = cur.execute(
        "SELECT COUNT(*) FROM player_snapshots ps "
        "WHERE NOT EXISTS (SELECT 1 FROM auction_prices ap WHERE ap.player_id = ps.player_id)"
    ).fetchone()[0]
    emit(f"  giocatori con quotazione ma senza anagrafica Excel-Online (senza metadata): {only_price}")
    emit(f"  giocatori con anagrafica Excel-Online ma senza quotazione: {only_snapshot}")
    emit("  nota: la stima iniziale (CONFRONTO_FONTI.md) era 716-565=151 righe sorgente;")
    emit("  il numero qui sopra è più basso perché il cross-matching CSV<->Excel-Online")
    emit("  (cluster identità) ha già ricongiunto alcuni giocatori visti in una sola fonte.")

    emit()
    emit("-- Casi anomali noti: verificati come preservati, non persi né uniti --")
    esposito = cur.execute(
        "SELECT DISTINCT p.canonical_full_name FROM players p "
        "JOIN player_aliases pa ON pa.player_id = p.player_id "
        "WHERE pa.alias_normalized LIKE 'ESPOSITO%'"
    ).fetchall()
    emit(f"  Esposito: {len(esposito)} entità distinte (atteso 3, nessun merge per solo cognome)")

    review_rows = cur.execute(
        "SELECT source_name, source_row_number, match_status FROM player_source_records "
        "WHERE match_status = 'ambiguous' ORDER BY source_name, source_row_number"
    ).fetchall()
    emit(f"  righe in revisione manuale (match_review.csv), preservate come 'ambiguous': {len(review_rows)}")
    for row in review_rows:
        emit(f"    {row[0]}  riga {row[1]}")

    report = "\n".join(lines)
    QUALITY_REPORT_PATH.write_text(report + "\n", encoding="utf-8")
    return report


# ------------------------------------------------------------------
# Fase 6: pubblicazione — export players_enriched.csv per Google Sheets
# ------------------------------------------------------------------
def export_enriched_csv(conn) -> int:
    cur = conn.execute("SELECT * FROM players_enriched")
    columns = [d[0] for d in cur.description]
    rows = cur.fetchall()
    with ENRICHED_EXPORT_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        writer.writerows(rows)
    return len(rows)


def export_app_database(conn, target_path: Path = APP_DATABASE_PATH) -> int:
    """Pubblica solo il contratto dati necessario alla UI, senza tabelle raw."""
    temporary_path = target_path.with_suffix(target_path.suffix + ".tmp")
    temporary_path.unlink(missing_ok=True)
    output = sqlite3.connect(temporary_path)
    try:
        source = conn.execute("SELECT * FROM app_players")
        columns = [description[0] for description in source.description]
        rows = source.fetchall()
        definitions = ", ".join(f'"{column}"' for column in columns)
        placeholders = ", ".join("?" for _ in columns)
        output.execute(f"CREATE TABLE app_players ({definitions})")
        output.executemany(f"INSERT INTO app_players VALUES ({placeholders})", rows)
        output.execute("CREATE UNIQUE INDEX app_players_id ON app_players(player_id)")
        output.execute("CREATE INDEX app_players_filters ON app_players(team_name, role)")
        output.commit()
    finally:
        output.close()
    temporary_path.replace(target_path)
    return len(rows)


def main():
    fresh_db = not DB_PATH.exists()
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    print(f"Database: {DB_PATH} ({'nuovo' if fresh_db else 'esistente, import idempotente'})")

    print("\n[1/6] Ingestione grezza (immutabile, dedup per raw_hash)")
    csv_ids = ingest_source(conn, "fantacalcio-online-csv", CSV_PATH.name, iter_csv_raw(CSV_PATH))
    excel_online_ids = ingest_source(conn, "fantacalcio-online-excel", Path(QUOT_ONLINE_XLSX).name,
                                      iter_quot_online_raw(QUOT_ONLINE_XLSX))
    stat_ids = ingest_source(conn, "fantacalcio-it-statistiche", Path(STAT_XLSX).name,
                              iter_xlsx_with_id_raw(STAT_XLSX))
    quot_it_ids = ingest_source(conn, "fantacalcio-it-quotazioni", Path(QUOT_IT_XLSX).name,
                                 iter_xlsx_with_id_raw(QUOT_IT_XLSX))
    counts = {
        "fantacalcio-online-csv": len(csv_ids),
        "fantacalcio-online-excel": len(excel_online_ids),
        "fantacalcio-it-statistiche": len(stat_ids),
        "fantacalcio-it-quotazioni": len(quot_it_ids),
    }

    print("\n[2/6] Caricamento identity resolution (già calcolata da risolvi_identita.py)")
    team_aliases = load_team_aliases()
    player_aliases = load_player_aliases()
    review_keys = load_match_review_keys()
    upsert_teams_and_aliases(conn, team_aliases)
    upsert_players_and_aliases(conn, player_aliases)

    def index_by_source_row(aliases, source_name, key):
        return {int(r["source_row_number"]): r[key] for r in aliases if r["source_name"] == source_name}

    def team_by_source_row(source_name, staged_rows, row_number_field, team_key_field):
        team_alias_lookup = {(r["source_name"], r["alias_normalized"]): r["team_id"]
                              for r in team_aliases if r["source_name"] == source_name}
        return {r[row_number_field]: team_alias_lookup.get((source_name, r[team_key_field]))
                for r in staged_rows}

    csv_player_by_row = index_by_source_row(player_aliases, "fantacalcio-online-csv", "player_id")
    excel_player_by_row = index_by_source_row(player_aliases, "fantacalcio-online-excel", "player_id")
    stat_player_by_row = index_by_source_row(player_aliases, "fantacalcio-it-statistiche", "player_id")
    quot_it_player_by_row = index_by_source_row(player_aliases, "fantacalcio-it-quotazioni", "player_id")

    with CSV_PATH.open(newline="", encoding="utf-8") as f:
        csv_staged = [normalize_csv_row(row, i + 1) for i, row in enumerate(csv.DictReader(f))]
    excel_rows = load_quot_online(QUOT_ONLINE_XLSX)
    stat_rows = load_xlsx_with_id(STAT_XLSX)
    quot_it_rows = load_xlsx_with_id(QUOT_IT_XLSX)

    csv_team_by_row = team_by_source_row("fantacalcio-online-csv", csv_staged, "source_row_number", "team_key")
    excel_team_by_row = team_by_source_row("fantacalcio-online-excel", excel_rows, "row", "team_key")
    stat_team_by_row = {d["_row"]: None for d in stat_rows}  # squadra fantacalcio.it non ha team_alias dedicato qui
    quot_it_team_by_row = {d["_row"]: None for d in quot_it_rows}

    print("\n[3/6] Collegamento player_source_records -> players/teams")
    link_source_records(conn, "fantacalcio-online-csv", csv_ids, csv_player_by_row, csv_team_by_row, review_keys)
    link_source_records(conn, "fantacalcio-online-excel", excel_online_ids, excel_player_by_row, excel_team_by_row, review_keys)
    link_source_records(conn, "fantacalcio-it-statistiche", stat_ids, stat_player_by_row, stat_team_by_row, review_keys)
    link_source_records(conn, "fantacalcio-it-quotazioni", quot_it_ids, quot_it_player_by_row, quot_it_team_by_row, review_keys)

    print("\n[4/6] Normalizzazione delle quattro fonti")
    n_prices = build_auction_prices(conn, csv_ids, csv_player_by_row, csv_team_by_row)
    n_snapshots = build_player_snapshots(conn, excel_online_ids, excel_player_by_row, excel_team_by_row)
    n_fc_stats = build_fc_it_statistics(conn, stat_rows, stat_ids, stat_player_by_row)
    n_fc_quotes = build_fc_it_quotations(conn, quot_it_rows, quot_it_ids, quot_it_player_by_row)
    sync_app_configuration(conn, team_aliases)
    sync_data_sources(
        conn,
        {
            "fantacalcio-online-csv": CSV_PATH,
            "fantacalcio-online-excel": QUOT_ONLINE_XLSX,
            "fantacalcio-it-statistiche": STAT_XLSX,
            "fantacalcio-it-quotazioni": QUOT_IT_XLSX,
        },
    )
    print(f"  auction_prices scritte/aggiornate: {n_prices}")
    print(f"  player_snapshots scritte/aggiornate: {n_snapshots}")
    print(f"  fantacalcio_it_statistics scritte/aggiornate: {n_fc_stats}")
    print(f"  fantacalcio_it_quotations scritte/aggiornate: {n_fc_quotes}")

    print("\n[4b/6] Fusione duplicati identità e correzione nomi")
    apply_duplicate_overrides(conn)
    apply_name_overrides(conn)

    print("\n[5/6] Controlli di qualità")
    run_quality_checks(conn, counts)

    print("\n[6/6] Pubblicazione: export dati e database ridotto per l’app")
    n_exported = export_enriched_csv(conn)
    print(f"  {n_exported} righe esportate in {ENRICHED_EXPORT_PATH.name}")
    n_published = export_app_database(conn)
    print(f"  {n_published} giocatori pubblicati in {APP_DATABASE_PATH.name}")

    conn.close()

    from export_web_data import export_players_json
    n_web_exported = export_players_json()
    print(f"  {n_web_exported} giocatori esportati in web/data/players.json")


if __name__ == "__main__":
    sys.exit(main())
