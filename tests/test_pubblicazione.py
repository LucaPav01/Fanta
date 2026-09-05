import sqlite3
import tempfile
import unittest
from pathlib import Path

from app_data import connect_read_only
from importa_database import export_app_database, upsert_players_and_aliases


class TestPubblicazione(unittest.TestCase):
    def test_import_preferisce_il_nome_completo_in_ordine_naturale(self):
        connection = sqlite3.connect(":memory:")
        connection.executescript(
            """
            CREATE TABLE players (
                player_id TEXT PRIMARY KEY,
                canonical_full_name TEXT,
                canonical_last_name TEXT,
                canonical_first_name TEXT,
                current_team_id TEXT,
                updated_at TEXT
            );
            CREATE TABLE player_aliases (
                alias_id TEXT PRIMARY KEY,
                player_id TEXT,
                alias_raw TEXT,
                alias_normalized TEXT,
                source_name TEXT,
                match_method TEXT,
                match_confidence REAL
            );
            """
        )
        upsert_players_and_aliases(connection, [{
            "alias_id": "a1",
            "player_id": "p1",
            "alias_raw": "BARELLA Nicolò",
            "alias_normalized": "BARELLANICOLO",
            "source_name": "fantacalcio-online-excel",
            "canonical_name": "Barella",
            "canonical_team_key": "INTER",
            "match_method": "exact",
            "match_confidence": "1.0",
        }])

        player = connection.execute(
            "SELECT canonical_full_name, canonical_last_name, canonical_first_name FROM players"
        ).fetchone()
        self.assertEqual(player, ("Nicolò Barella", "Barella", "Nicolò"))
        connection.close()

    def test_database_pubblicato_contiene_solo_il_contratto_app(self):
        source = sqlite3.connect(":memory:")
        source.execute("CREATE TABLE app_players (player_id TEXT, player_name TEXT, team_name TEXT, role TEXT)")
        source.execute("INSERT INTO app_players VALUES ('p1', 'Nicolò Barella', 'Inter', 'MID')")

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "fantacalcio_app.db"
            count = export_app_database(source, target)
            with connect_read_only(target) as published:
                tables = [row[0] for row in published.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
                )]
                player = published.execute("SELECT * FROM app_players").fetchone()

            self.assertEqual(count, 1)
            self.assertEqual(tables, ["app_players"])
            self.assertEqual(player["player_name"], "Nicolò Barella")

        source.close()

    def test_artefatto_pubblicato_e_pronto_per_l_hosting(self):
        database = Path(__file__).resolve().parents[1] / "fantacalcio_app.db"
        self.assertTrue(database.exists(), "Manca fantacalcio_app.db")

        with connect_read_only(database) as published:
            objects = [row[0] for row in published.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )]
            players, teams = published.execute(
                "SELECT COUNT(*), COUNT(DISTINCT team_name) FROM app_players"
            ).fetchone()
            excluded = published.execute(
                "SELECT COUNT(*) FROM app_players WHERE team_name IN ('Estero', 'Serie Minori')"
            ).fetchone()[0]
            barella = published.execute(
                "SELECT player_name FROM app_players WHERE name_aliases LIKE '%BARELLA Nicolò%'"
            ).fetchone()

        self.assertEqual(objects, ["app_players"])
        self.assertGreater(players, 500)
        self.assertEqual(teams, 20)
        self.assertEqual(excluded, 0)
        self.assertIsNotNone(barella)
        self.assertEqual(barella[0], "Nicolò Barella")


if __name__ == "__main__":
    unittest.main()
