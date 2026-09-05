import sqlite3
import unittest

from app_data import get_player_detail


class TestSchedaAsta(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute(
            "CREATE TABLE app_players (player_id TEXT, player_name TEXT, team_name TEXT, role TEXT, "
            "fvm REAL, fvm_parametrized REAL, fvm_budget INTEGER, fvm_percentile REAL, fvm_tier TEXT, "
            "average_auction_price REAL, "
            "auction_teams INTEGER, auction_budget INTEGER, is_pct REAL, age INTEGER, rating REAL, "
            "potential REAL, appearances INTEGER, average_rating REAL, fantasy_average REAL, "
            "fvm_updated_at TEXT, auction_price_updated_at TEXT, is_updated_at TEXT, "
            "fvm_status TEXT, auction_price_status TEXT, is_status TEXT, data_status TEXT, source_names TEXT)"
        )
        self.db.execute(
            "INSERT INTO app_players VALUES "
            "('p1', 'Lautaro Martinez', 'Inter', 'FWD', 300, 150, 500, 92.5, 'Fascia 1', 145, 10, 500, 91, "
            "29, 88, 90, 34, 6.6, 8.1, '2026-09-01', '2026-09-02', '2026-09-03', "
            "'available', 'available', 'available', 'available', 'quotazioni,statistiche')"
        )

    def tearDown(self):
        self.db.close()

    def test_dettaglio_espone_tutte_le_sezioni_informative(self):
        row = get_player_detail(self.db, "p1")
        self.assertEqual(row["fvm_parametrized"], 150)
        self.assertEqual(row["fvm_percentile"], 92.5)
        self.assertEqual(row["fvm_tier"], "Fascia 1")
        self.assertEqual(row["fantasy_average"], 8.1)
        self.assertEqual(row["potential"], 90)
        self.assertEqual(row["fvm_status"], "available")
        self.assertEqual(row["source_names"], "quotazioni,statistiche")

    def test_giocatore_inesistente_non_restituisce_una_scheda(self):
        self.assertIsNone(get_player_detail(self.db, "assente"))


if __name__ == "__main__":
    unittest.main()
