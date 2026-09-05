import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from export_web_data import REQUIRED_FIELDS, export_players_json

ROOT = Path(__file__).resolve().parents[1]
PLAYERS_JSON = ROOT / "web" / "data" / "players.json"


class TestExportWebData(unittest.TestCase):
    def test_players_json_e_valido_e_completo(self):
        self.assertTrue(PLAYERS_JSON.exists(), "Manca web/data/players.json")
        payload = json.loads(PLAYERS_JSON.read_text(encoding="utf-8"))

        self.assertIn("generated_at", payload)
        self.assertIn("config", payload)
        self.assertIn("players", payload)
        self.assertEqual(len(payload["players"]), 663)

        config = payload["config"]
        self.assertEqual(config["auction"]["teams"], 10)
        self.assertEqual(config["auction"]["budget"], 500)
        self.assertEqual(
            config["squad_composition"],
            {"GK": 3, "DEF": 8, "MID": 8, "FWD": 6},
        )

        required_json_fields = ("id", "name", "team", "role", "search_blob")
        for player in payload["players"]:
            for field in required_json_fields:
                self.assertIsNotNone(player.get(field), f"{field} nullo per {player.get('id')}")
                self.assertNotEqual(player.get(field), "")
            self.assertIsInstance(player.get("aliases"), list)
            self.assertIsInstance(player.get("sources"), list)
            self.assertIn("fvm_updated_at", player)
            self.assertIn("price_updated_at", player)
            self.assertIn("is_updated_at", player)
            self.assertIn("fvm_percentile", player)
            self.assertIn("fvm_tier", player)
            if player["fvm"] is None:
                self.assertIsNone(player["fvm_percentile"])
                self.assertIsNone(player["fvm_tier"])
            else:
                self.assertRegex(player["fvm_tier"], r"^Fascia [1-9][0-9]*$")
                self.assertGreaterEqual(player["fvm_percentile"], 0)
                self.assertLessEqual(player["fvm_percentile"], 100)

        for role, limit in {"GK": 5, "DEF": 10, "MID": 10, "FWD": 10}.items():
            with self.subTest(role=role):
                tier_counts = {}
                for player in payload["players"]:
                    if player["role"] == role and player["fvm_tier"]:
                        tier_counts[player["fvm_tier"]] = tier_counts.get(player["fvm_tier"], 0) + 1
                self.assertTrue(tier_counts)
                self.assertTrue(all(count <= limit for count in tier_counts.values()))

    def test_esportazione_solleva_errore_se_un_campo_obbligatorio_e_nullo(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "broken.db"
            config_path = Path(directory) / "app_config.json"
            target_path = Path(directory) / "players.json"

            connection = sqlite3.connect(database_path)
            columns = [
                "player_id", "player_name", "first_name", "last_name", "name_aliases",
                "team_name", "role", "fvm", "fvm_parametrized", "fvm_budget",
                "fvm_percentile", "fvm_tier",
                "average_auction_price", "auction_teams", "auction_budget", "is_pct",
                "age", "rating", "potential", "appearances", "average_rating",
                "fantasy_average", "fvm_status", "auction_price_status", "is_status",
                "data_status", "fvm_updated_at", "auction_price_updated_at", "is_updated_at",
                "source_names",
            ]
            connection.execute(f"CREATE TABLE app_players ({', '.join(columns)})")
            placeholders = ", ".join("?" for _ in columns)
            row = ["p1", "Test Player", "Test", "Player", "", "Roma", None] + [None] * (len(columns) - 7)
            connection.execute(f"INSERT INTO app_players VALUES ({placeholders})", row)
            connection.commit()
            connection.close()

            config_path.write_text(json.dumps({
                "season": "2026-2027",
                "competition": "Serie A",
                "auction": {"teams": 10, "budget": 500},
                "squad_composition": {"GK": 3, "DEF": 8, "MID": 8, "FWD": 6},
            }), encoding="utf-8")

            with self.assertRaises(ValueError):
                export_players_json(database_path, config_path, target_path)

    def test_required_fields_coprono_i_vincoli_non_nulli_del_db(self):
        self.assertEqual(
            REQUIRED_FIELDS,
            ("player_id", "player_name", "team_name", "role"),
        )


if __name__ == "__main__":
    unittest.main()
