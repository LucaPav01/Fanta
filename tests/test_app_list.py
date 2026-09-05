import sqlite3
import unittest

from app_data import FilterState, exact_search_result, get_player_detail, player_url, query_players


class TestListaGiocatori(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute(
            "CREATE TABLE app_players (player_id TEXT, player_name TEXT, name_aliases TEXT, "
            "team_name TEXT, role TEXT, fvm REAL, average_auction_price REAL, is_pct REAL, "
            "fvm_status TEXT, auction_price_status TEXT, is_status TEXT)"
        )
        self.db.executemany(
            "INSERT INTO app_players VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("p1", "Lautaro Martinez", "MARTINEZLautaro,Lautaro Martínez", "Inter", "FWD", 300, 145, 91, "available", "available", "available"),
                ("p2", "Nicolò Barella", "BARELLANicolò", "Inter", "MID", 90, 41, 84, "available", "available", "available"),
                ("p3", "Paulo Dybala", "DYBALAPaulo", "Roma", "FWD", None, 80, None, "missing", "available", "missing"),
            ],
        )

    def tearDown(self):
        self.db.close()

    def test_filtri_sono_combinabili(self):
        rows = query_players(self.db, FilterState(search="mart", team="Inter", role="FWD"))
        self.assertEqual([row["player_id"] for row in rows], ["p1"])

    def test_ordinamento_mette_i_valori_mancanti_in_fondo(self):
        rows = query_players(self.db, FilterState(role="FWD", sort="fvm"))
        self.assertEqual([row["player_id"] for row in rows], ["p1", "p3"])

    def test_match_esatto_tollera_accenti_e_alias(self):
        rows = query_players(self.db, FilterState(search="Lautaro"))
        self.assertEqual(exact_search_result(rows, "Lautaro Martínez"), "p1")

    def test_ricerca_tollera_accenti_assenti(self):
        rows = query_players(self.db, FilterState(search="Nicolo"))
        self.assertEqual([row["player_id"] for row in rows], ["p2"])

    def test_ricerca_parziale_con_un_risultato_apre_la_scheda(self):
        rows = query_players(self.db, FilterState(search="dyba"))
        self.assertEqual(exact_search_result(rows, "dyba"), "p3")

    def test_url_giocatore_conserva_filtri_e_ordinamento(self):
        url = player_url(FilterState(search="lautaro", team="Inter", role="FWD", sort="price"), "p1")
        self.assertIn("search=lautaro", url)
        self.assertIn("team=Inter", url)
        self.assertIn("role=FWD", url)
        self.assertIn("sort=price", url)
        self.assertIn("player=p1", url)


if __name__ == "__main__":
    unittest.main()
