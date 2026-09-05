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
import hashlib
import json
import sqlite3
import sys
import uuid
from pathlib import Path

import openpyxl

from normalizza import make_key, normalize_csv_row
from risolvi_identita import (
    NAMESPACE,
    QUOT_IT_XLSX,
    QUOT_ONLINE_XLSX,
    STAT_XLSX,
    load_quot_online,
    load_xlsx_with_id,
)

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "fantacalcio.db"
SCHEMA_PATH = ROOT / "schema.sql"
CSV_PATH = ROOT / "fantacalcio_prezzi.csv"
PLAYER_ALIASES_PATH = ROOT / "player_aliases.csv"
TEAM_ALIASES_PATH = ROOT / "team_aliases.csv"
MATCH_REVIEW_PATH = ROOT / "match_review.csv"
ENRICHED_EXPORT_PATH = ROOT / "players_enriched.csv"
QUALITY_REPORT_PATH = ROOT / "quality_report.txt"

# Le fonti fantacalcio.it non hanno una data di pubblicazione esplicita: si
# usa la stagione come chiave di validità, unica per questa esecuzione.
SEASON_VALID_FROM = "2026-2027"

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
    seen_players = {}
    for row in player_aliases:
        player_id = row["player_id"]
        if player_id not in seen_players:
            seen_players[player_id] = row["canonical_name"]
            last_name = row["canonical_name"].split()[0] if row["canonical_name"] else row["canonical_name"]
            team_id = str(uuid.uuid5(NAMESPACE, f"team:{row['canonical_team_key']}"))
            cur.execute(
                "INSERT OR IGNORE INTO players "
                "(player_id, canonical_full_name, canonical_last_name, current_team_id) VALUES (?, ?, ?, ?)",
                (player_id, row["canonical_name"], last_name, team_id),
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
        n += 1
    conn.commit()
    return n


def parse_num(value):
    if value is None or value == "":
        return None
    return float(value)


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
    emit(f"  auction_prices: {n_prices}  player_snapshots: {n_snapshots}")

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

    print("\n[4/6] Arricchimento: auction_prices (CSV) e player_snapshots (Excel-Online)")
    n_prices = build_auction_prices(conn, csv_ids, csv_player_by_row, csv_team_by_row)
    n_snapshots = build_player_snapshots(conn, excel_online_ids, excel_player_by_row, excel_team_by_row)
    print(f"  auction_prices scritte/aggiornate: {n_prices}")
    print(f"  player_snapshots scritte/aggiornate: {n_snapshots}")

    print("\n[5/6] Controlli di qualità")
    run_quality_checks(conn, counts)

    print("\n[6/6] Pubblicazione: export players_enriched.csv")
    n_exported = export_enriched_csv(conn)
    print(f"  {n_exported} righe esportate in {ENRICHED_EXPORT_PATH.name}")

    conn.close()


if __name__ == "__main__":
    sys.exit(main())
