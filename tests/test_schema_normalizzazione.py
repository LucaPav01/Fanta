import csv
import sqlite3
import unittest
from collections import defaultdict
from pathlib import Path

from normalizza import ROLE_MAP_CSV, normalize_csv_row
from importa_database import normalize_fc_it_quotation, normalize_fc_it_statistics


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

    def test_schema_sqlite_crea_entita_normalizzate_e_viste_app(self):
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
            "auction_price_estimates",
            "player_snapshots",
            "data_sources",
            "competition_teams",
            "app_settings",
            "fantacalcio_it_quotations",
            "fantacalcio_it_statistics",
            "metric_definitions",
        }
        self.assertTrue(expected_tables.issubset(objects))
        self.assertEqual(objects.get("players_enriched"), "view")
        self.assertEqual(objects.get("app_players"), "view")
        self.assertEqual(objects.get("app_data_catalog"), "view")
        self.assertEqual(self.db.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_normalizza_quotazioni_fantacalcio_it_e_fvm(self):
        normalized = normalize_fc_it_quotation(
            {"R": "A", "Rm": "Pc", "Qt.I": 18, "Qt.A": 24, "Diff.": 6, "FVM": 120, "FVM M": 108}
        )
        self.assertEqual(normalized["role_classic"], "FWD")
        self.assertEqual(normalized["quotation_initial"], 18.0)
        self.assertEqual(normalized["quotation_current"], 24.0)
        self.assertEqual(normalized["quotation_delta"], 6.0)
        self.assertEqual(normalized["fvm_classic_1000"], 120.0)
        self.assertEqual(normalized["fvm_mantra_1000"], 108.0)

    def test_normalizza_statistiche_fantacalcio_it(self):
        normalized = normalize_fc_it_statistics(
            {"R": "C", "Pg": 30, "Mv": 6.25, "Fm": 7.1, "Gf": 8, "Ass": 6, "Amm": 4, "Esp": 1}
        )
        self.assertEqual(normalized["role_classic"], "MID")
        self.assertEqual(normalized["appearances"], 30)
        self.assertEqual(normalized["average_rating"], 6.25)
        self.assertEqual(normalized["fantasy_average"], 7.1)
        self.assertEqual(normalized["goals_for"], 8)
        self.assertEqual(normalized["assists"], 6)

    def test_app_players_esclude_categorie_non_serie_a_ed_espone_stati(self):
        self.db.executemany(
            "INSERT INTO teams(team_id, canonical_name) VALUES (?, ?)",
            [("t-serie-a", "Inter"), ("t-estero", "Estero")],
        )
        self.db.executemany(
            "INSERT INTO players(player_id, canonical_full_name, canonical_last_name, current_team_id) "
            "VALUES (?, ?, ?, ?)",
            [("p1", "Mario Rossi", "Rossi", "t-serie-a"), ("p2", "Luigi Bianchi", "Bianchi", "t-estero")],
        )
        self.db.execute(
            "INSERT INTO competition_teams(season, competition_name, team_id) "
            "VALUES ('2026-2027', 'Serie A', 't-serie-a')"
        )
        self.db.executemany(
            "INSERT INTO app_settings(setting_key, setting_value) VALUES (?, ?)",
            [("auction_teams", "10"), ("auction_budget", "500")],
        )
        for source_id, source_name in (("sr-a", "fantacalcio-online-csv"),
                                       ("sr-i", "fantacalcio-online-excel"),
                                       ("sr-f", "fantacalcio-it-quotazioni")):
            self.db.execute(
                "INSERT INTO player_source_records "
                "(source_record_id, batch_id, source_name, source_file, source_row_number, raw_data, raw_hash, "
                "player_id, team_id, match_status) VALUES (?, 'b', ?, 'source', 1, '{}', ?, 'p1', 't-serie-a', 'matched')",
                (source_id, source_name, f"hash-{source_id}"),
            )
        self.db.execute(
            "INSERT INTO auction_prices(price_id, player_id, team_id, source_record_id, ruolo, price_10sq_500, valid_from) "
            "VALUES ('ap', 'p1', 't-serie-a', 'sr-a', 'FWD', 42.5, '2026-2027')"
        )
        self.db.execute(
            "INSERT INTO auction_price_estimates "
            "(estimate_id, player_id, source_record_id, teams_bucket, budget_bucket, average_price, valid_from) "
            "VALUES ('ae', 'p1', 'sr-a', 10, 500, 42.5, '2026-2027')"
        )
        self.db.execute(
            "INSERT INTO player_snapshots(snapshot_id, player_id, team_id, source_record_id, is_pct, valid_from) "
            "VALUES ('ps', 'p1', 't-serie-a', 'sr-i', 78, '2026-2027')"
        )
        self.db.execute(
            "INSERT INTO fantacalcio_it_quotations "
            "(quotation_id, player_id, source_record_id, role_classic, fvm_classic_1000, valid_from) "
            "VALUES ('fq', 'p1', 'sr-f', 'FWD', 100, '2026-2027')"
        )

        rows = self.db.execute(
            "SELECT player_name, team_name, fvm, fvm_parametrized, average_auction_price, is_pct, data_status "
            "FROM app_players"
        ).fetchall()
        self.assertEqual(rows, [("Mario Rossi", "Inter", 100.0, 50.0, 42.5, 78.0, "available")])

    def test_app_players_segnala_valori_mancanti_senza_inventarli(self):
        self.db.execute("INSERT INTO teams(team_id, canonical_name) VALUES ('t1', 'Roma')")
        self.db.execute(
            "INSERT INTO players(player_id, canonical_full_name, canonical_last_name, current_team_id) "
            "VALUES ('p1', 'Mario Rossi', 'Rossi', 't1')"
        )
        self.db.execute(
            "INSERT INTO competition_teams(season, competition_name, team_id) VALUES ('2026-2027', 'Serie A', 't1')"
        )
        self.db.executemany(
            "INSERT INTO app_settings(setting_key, setting_value) VALUES (?, ?)",
            [("auction_teams", "10"), ("auction_budget", "500")],
        )
        row = self.db.execute(
            "SELECT fvm, average_auction_price, is_pct, fvm_status, auction_price_status, is_status, data_status "
            "FROM app_players"
        ).fetchone()
        self.assertEqual(row, (None, None, None, "missing", "missing", "missing", "missing"))

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

    def _insert_player_con_fvm(self, player_id, ruolo, fvm):
        self.db.execute(
            "INSERT INTO players(player_id, canonical_full_name, canonical_last_name, current_team_id) "
            "VALUES (?, ?, ?, 't1')",
            (player_id, player_id, player_id),
        )
        source_id = f"sr-{player_id}"
        self.db.execute(
            "INSERT INTO player_source_records "
            "(source_record_id, batch_id, source_name, source_file, source_row_number, raw_data, raw_hash) "
            "VALUES (?, ?, 'fantacalcio-it-quotazioni', 'source', 1, '{}', ?)",
            (source_id, f"b-{player_id}", f"hash-{player_id}"),
        )
        self.db.execute(
            "INSERT INTO fantacalcio_it_quotations "
            "(quotation_id, player_id, source_record_id, role_classic, fvm_classic_1000, valid_from) "
            "VALUES (?, ?, ?, ?, ?, '2026-2027')",
            (f"fq-{player_id}", player_id, source_id, ruolo, fvm),
        )

    def _setup_fasce_fixture(self):
        self.db.execute("INSERT INTO teams(team_id, canonical_name) VALUES ('t1', 'Roma')")
        self.db.execute(
            "INSERT INTO competition_teams(season, competition_name, team_id) VALUES ('2026-2027', 'Serie A', 't1')"
        )
        self.db.executemany(
            "INSERT INTO app_settings(setting_key, setting_value) VALUES (?, ?)",
            [("auction_teams", "10"), ("auction_budget", "500")],
        )

    def test_fascia_fvm_e_percentile_calcolati_per_ruolo_con_parita(self):
        self._setup_fasce_fixture()
        # Due giocatori FWD a pari FVM devono ricevere lo stesso percentile/fascia,
        # senza che un ordinamento arbitrario li separi.
        self._insert_player_con_fvm("f1", "FWD", 10)
        self._insert_player_con_fvm("f2", "FWD", 10)
        self._insert_player_con_fvm("f3", "FWD", 50)
        self._insert_player_con_fvm("f4", "FWD", 90)
        # Un DEF con lo stesso FVM di un FWD non deve condividerne la fascia:
        # il confronto e' sempre relativo al proprio ruolo.
        self._insert_player_con_fvm("d1", "DEF", 90)

        rows = {
            player_id: (percentile, tier)
            for player_id, percentile, tier in self.db.execute(
                "SELECT player_id, fvm_percentile, fvm_tier FROM app_players"
            )
        }

        self.assertEqual(rows["f1"], rows["f2"])
        self.assertEqual(rows["f1"][0], 0.0)
        self.assertEqual(rows["f1"][1], "Fascia 1")
        self.assertEqual(rows["f4"][0], 100.0)
        self.assertEqual(rows["f4"][1], "Fascia 1")
        # d1 e f4 hanno lo stesso FVM ma appartengono a pool di ruolo diversi: d1 e'
        # l'unico DEF del fixture, quindi il suo percentile e' relativo a un pool di
        # un solo elemento (0.0 per definizione), non al pool FWD di f4. Essendo
        # anche il primo del proprio ruolo, e' nella Fascia 1.
        self.assertEqual(rows["d1"], (0.0, "Fascia 1"))
        self.assertNotEqual(rows["d1"], rows["f4"])

    def test_fasce_fvm_hanno_la_capienza_massima_per_ruolo(self):
        self._setup_fasce_fixture()
        for position in range(21):
            self._insert_player_con_fvm(f"f{position:02}", "FWD", 100 - position)
        for position in range(11):
            self._insert_player_con_fvm(f"g{position:02}", "GK", 100 - position)

        rows = self.db.execute(
            "SELECT role, fvm_tier, COUNT(*) FROM app_players "
            "GROUP BY role, fvm_tier ORDER BY role, fvm_tier"
        ).fetchall()
        counts = {(role, tier): count for role, tier, count in rows}

        self.assertEqual(counts[("FWD", "Fascia 1")], 10)
        self.assertEqual(counts[("FWD", "Fascia 2")], 10)
        self.assertEqual(counts[("FWD", "Fascia 3")], 1)
        self.assertEqual(counts[("GK", "Fascia 1")], 5)
        self.assertEqual(counts[("GK", "Fascia 2")], 5)
        self.assertEqual(counts[("GK", "Fascia 3")], 1)

    def test_fascia_fvm_e_null_quando_il_fvm_manca(self):
        self._setup_fasce_fixture()
        self.db.execute(
            "INSERT INTO players(player_id, canonical_full_name, canonical_last_name, current_team_id) "
            "VALUES ('p1', 'Mario Rossi', 'Rossi', 't1')"
        )
        row = self.db.execute(
            "SELECT fvm_percentile, fvm_tier FROM app_players WHERE player_id = 'p1'"
        ).fetchone()
        self.assertEqual(row, (None, None))

    def test_etichetta_nuovo_non_diventa_parte_del_nome(self):
        affected = [r for r in self.rows if r["nome_raw"].endswith("Nuovo")]
        self.assertEqual(len(affected), 114)
        self.assertTrue(
            all(not (r["nome"] or "").endswith("Nuovo") for r in affected),
            "L'etichetta UI 'Nuovo' è stata incorporata nel nome canonico",
        )


if __name__ == "__main__":
    unittest.main()
