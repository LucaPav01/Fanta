import csv
import sqlite3
import unittest
from collections import defaultdict
from pathlib import Path

from normalizza import ROLE_MAP_CSV, normalize_csv_row


ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "fantacalcio_prezzi.csv"
SCHEMA_PATH = ROOT / "schema.sql"


def load_rows():
    with CSV_PATH.open(newline="", encoding="utf-8") as source:
        return [
            normalize_csv_row(row, row_number)
            for row_number, row in enumerate(csv.DictReader(source), start=1)
        ]


class TestSchemaENormalizzazione(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = load_rows()

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

    def tearDown(self):
        self.db.close()

    def test_schema_sqlite_crea_le_sette_entita_e_la_vista(self):
        objects = dict(
            self.db.execute(
                "SELECT name, type FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' AND type IN ('table', 'view')"
            )
        )
        expected_tables = {
            "teams",
            "team_aliases",
            "players",
            "player_aliases",
            "player_source_records",
            "auction_prices",
            "player_snapshots",
        }
        self.assertTrue(expected_tables.issubset(objects))
        self.assertEqual(objects.get("players_enriched"), "view")
        self.assertEqual(self.db.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_tutte_le_righe_csv_hanno_squadra_e_ruolo_noto(self):
        self.assertEqual(len(self.rows), 716)
        self.assertEqual(
            [(r["source_row_number"], r["squadra_raw"]) for r in self.rows if not r["team_key"]],
            [],
        )
        self.assertEqual(
            [(r["source_row_number"], r["ruolo_raw"]) for r in self.rows if r["ruolo_normalizzato"] is None],
            [],
        )
        self.assertEqual(set(ROLE_MAP_CSV), {"P", "D", "C", "A"})

    def test_ruolo_canonico_prodotto_dal_normalizzatore_e_accettato_dallo_schema(self):
        """Il vocabolario canonico dello staging deve poter essere persistito."""
        self.db.execute("INSERT INTO teams(team_id, canonical_name) VALUES ('t1', 'Inter')")
        self.db.execute(
            "INSERT INTO players(player_id, canonical_full_name, canonical_last_name, "
            "canonical_first_name, current_team_id) VALUES ('p1', 'Rossi Mario', 'Rossi', 'Mario', 't1')"
        )
        self.db.execute(
            "INSERT INTO player_source_records("
            "source_record_id, batch_id, source_name, source_file, source_row_number, "
            "raw_data, raw_hash, player_id, team_id, match_status"
            ") VALUES ('sr1', 'b1', 'test', 'test.csv', 1, '{}', 'hash', 'p1', 't1', 'matched')"
        )

        for i, (raw_role, canonical_role) in enumerate(ROLE_MAP_CSV.items()):
            with self.subTest(raw_role=raw_role, canonical_role=canonical_role):
                self.db.execute(
                    "INSERT INTO auction_prices("
                    "price_id, player_id, team_id, source_record_id, ruolo, valid_from"
                    ") VALUES (?, 'p1', 't1', 'sr1', ?, ?)",
                    (f"price-{raw_role}", canonical_role, f"2026-09-{i + 1:02d}"),
                )

    def test_quotazione_stessa_data_non_puo_duplicare_il_giocatore(self):
        """Lo schema promette una sola quotazione per giocatore e valid_from."""
        self.db.execute("INSERT INTO teams(team_id, canonical_name) VALUES ('t1', 'Inter')")
        self.db.execute(
            "INSERT INTO players(player_id, canonical_full_name, canonical_last_name, "
            "canonical_first_name, current_team_id) VALUES ('p1', 'Rossi Mario', 'Rossi', 'Mario', 't1')"
        )
        for source_id, row_number in (("sr1", 1), ("sr2", 2)):
            self.db.execute(
                "INSERT INTO player_source_records("
                "source_record_id, batch_id, source_name, source_file, source_row_number, "
                "raw_data, raw_hash, player_id, team_id, match_status"
                ") VALUES (?, 'b1', 'test', 'test.csv', ?, '{}', ?, 'p1', 't1', 'matched')",
                (source_id, row_number, f"hash-{row_number}"),
            )
        self.db.execute(
            "INSERT INTO auction_prices("
            "price_id, player_id, team_id, source_record_id, ruolo, valid_from"
            ") VALUES ('price-1', 'p1', 't1', 'sr1', 'FWD', '2026-09-05')"
        )

        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO auction_prices("
                "price_id, player_id, team_id, source_record_id, ruolo, valid_from"
                ") VALUES ('price-2', 'p1', 't1', 'sr2', 'FWD', '2026-09-05')"
            )

    def test_esposito_non_vengono_uniti_per_il_solo_cognome(self):
        esposito = [r for r in self.rows if r["name_key"].startswith("ESPOSITO")]
        self.assertEqual(
            [(r["nome_raw"], r["squadra_raw"], r["ruolo_raw"]) for r in esposito],
            [
                ("ESPOSITO FPFrancesco Pio", "Inter", "A"),
                ("ESPOSITO SSebastiano", "Sassuolo", "A"),
                ("ESPOSITOSebastian2025/2026", "Lecce", "D"),
            ],
        )
        self.assertEqual(len({r["name_key"] for r in esposito}), 3)

    def test_esposito_sono_tutti_segnalati_come_omonimi_di_cognome(self):
        """Le iniziali di disambiguazione della fonte non devono nascondere l'omonimia."""
        esposito = [r for r in self.rows if r["name_key"].startswith("ESPOSITO")]
        self.assertEqual({r["cognome"] for r in esposito}, {"ESPOSITO"})

    def test_elenco_omonimi_non_contiene_merge_impliciti(self):
        by_last_name = defaultdict(list)
        for row in self.rows:
            if row["cognome"]:
                by_last_name[row["cognome"]].append(row)

        for same_last_name in by_last_name.values():
            name_keys = [row["name_key"] for row in same_last_name]
            self.assertEqual(len(name_keys), len(set(name_keys)))

    def test_nome_non_parsabile_resta_da_revisionare(self):
        ambiguous = [r for r in self.rows if r["name_ambiguous"]]
        self.assertEqual(
            [(r["source_row_number"], r["nome_raw"], r["squadra_raw"]) for r in ambiguous],
            [(485, "FRANJIćBartolNuovo", "Venezia")],
        )
        self.assertIsNone(ambiguous[0]["cognome"])
        self.assertIsNone(ambiguous[0]["nome"])

    def test_suffissi_stagione_sono_rimossi_ma_segnalati(self):
        affected = [
            r
            for r in self.rows
            if "suffisso stagione rimosso dal nome (es. '2025/2026')"
            in r["validation_errors"]
        ]
        self.assertEqual(len(affected), 200)
        self.assertTrue(all("2025/2026" not in r["nome_clean"] for r in affected))

    def test_etichetta_nuovo_non_diventa_parte_del_nome(self):
        affected = [r for r in self.rows if r["nome_raw"].endswith("Nuovo")]
        self.assertEqual(len(affected), 114)
        self.assertTrue(
            all(not (r["nome"] or "").endswith("Nuovo") for r in affected),
            "L'etichetta UI 'Nuovo' è stata incorporata nel nome canonico",
        )


if __name__ == "__main__":
    unittest.main()
