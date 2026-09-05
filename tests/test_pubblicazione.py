import sqlite3
import tempfile
import unittest
from pathlib import Path

from app_data import connect_read_only
from importa_database import export_app_database


class TestPubblicazione(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
