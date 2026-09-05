import sqlite3
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path

from app_data import FilterState, connect_read_only, exact_search_result, normalize_name, query_players
from importa_database import export_app_database, upsert_players_and_aliases


ROOT = Path(__file__).resolve().parents[1]
PUBLISHED_DATABASE = ROOT / "fantacalcio_app.db"

FUSED_PLAYERS = {
    "65fa4b88-21c9-5222-9f6c-5af854355c44": ("Zambo Anguissa", "ANGUISSA André Zambo"),
    "98b9c3b6-e66b-5dc1-9cc0-f8d5ef068e72": ("Ederson", "EDERSON-"),
    "84896b99-a94c-530e-b472-00d0ed3d3058": ("Omar Fayed", "FAYED Omar"),
    "5092a928-da94-5977-a494-7bad0695d85a": ("Juan Jesus", "JESUS Juan"),
    "dd9e3dee-a9dd-535a-9194-14d55cb9940c": ("Esposito Se.", "ESPOSITO S Sebastiano"),
    "56d2bff4-c53d-58f9-8cfd-6fb52a9c4b43": ("Nzola", "NZOLA M'Bala"),
    "1878ae46-2008-53c0-82b4-09c3e22b963c": ("Zè Pedro", "ZE PEDRO da Silva Figueiredo Freitas"),
    "5846ee56-ef2f-5175-a4fb-111605062b7e": ("Luis Henrique", "LUIS HENRIQUE de Lima"),
    "75e9282c-f176-55c5-b3d9-27687590609a": ("De Martis", "DE MARTIS DE LA ROSA Thomás"),
    "3105434b-2888-5301-92a0-60379432fda8": ("Diallo O.", "DIALLO THIAO Ousmane"),
    "790ef4af-ec58-5868-840f-fd0523f41e4c": ("De Marzi", "DE MARZI GIORGIO Giorgio"),
}


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
        self.assertTrue(PUBLISHED_DATABASE.exists(), "Manca fantacalcio_app.db")

        with connect_read_only(PUBLISHED_DATABASE) as published:
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
        self.assertEqual(players, 663, "La lista di fusione non e' stata applicata per intero")
        self.assertEqual(teams, 20)
        self.assertEqual(excluded, 0)
        self.assertIsNotNone(barella)
        self.assertEqual(barella[0], "Nicolò Barella")

    def test_varianti_del_nome_aprono_una_sola_scheda_canonica(self):
        with connect_read_only(PUBLISHED_DATABASE) as published:
            cases = {
                "Nicolò Barella": "Nicolò Barella",
                "Nicolo Barella": "Nicolò Barella",
                "Barella": "Nicolò Barella",
                "Lautaro Martinez": "Lautaro Martinez",
                "Martinez Lautaro": "Lautaro Martinez",
            }
            for search, canonical_name in cases.items():
                with self.subTest(search=search):
                    rows = query_players(published, FilterState(search=search))
                    player_id = exact_search_result(rows, search)
                    self.assertIsNotNone(player_id)
                    matched = [row for row in rows if row["player_id"] == player_id]
                    self.assertEqual(len(matched), 1)
                    self.assertEqual(matched[0]["player_name"], canonical_name)

    def test_nessuna_coppia_residua_per_squadra_e_cognome_normalizzato(self):
        omonimi_legittimi = {"gelli", "oyono", "martinez", "terracciano"}
        with connect_read_only(PUBLISHED_DATABASE) as published:
            rows = published.execute(
                "SELECT player_name, last_name, team_name FROM app_players"
            ).fetchall()

        by_team_and_surname = defaultdict(list)
        for row in rows:
            surname = normalize_name(row["last_name"] or "")
            if surname and surname not in omonimi_legittimi:
                by_team_and_surname[(row["team_name"], surname)].append(row["player_name"])
        residual_pairs = {
            key: names for key, names in by_team_and_surname.items() if len(names) > 1
        }
        self.assertEqual(residual_pairs, {})

    def test_ogni_giocatore_con_dati_decisionali_ha_un_ruolo(self):
        with connect_read_only(PUBLISHED_DATABASE) as published:
            rows = published.execute(
                "SELECT player_id, player_name FROM app_players "
                "WHERE (fvm IS NOT NULL OR average_auction_price IS NOT NULL OR is_pct IS NOT NULL) "
                "AND (role IS NULL OR trim(role) = '')"
            ).fetchall()
        self.assertEqual([dict(row) for row in rows], [])

    def test_le_undici_schede_fuse_sono_complete(self):
        placeholders = ",".join("?" for _ in FUSED_PLAYERS)
        with connect_read_only(PUBLISHED_DATABASE) as published:
            rows = published.execute(
                "SELECT player_id, player_name, fvm, average_auction_price, is_pct, data_status "
                f"FROM app_players WHERE player_id IN ({placeholders})",
                tuple(FUSED_PLAYERS),
            ).fetchall()

        by_id = {row["player_id"]: row for row in rows}
        self.assertEqual(set(by_id), set(FUSED_PLAYERS))
        for player_id, row in by_id.items():
            with self.subTest(player=row["player_name"]):
                self.assertNotEqual(row["data_status"], "missing")

        anguissa = by_id["65fa4b88-21c9-5222-9f6c-5af854355c44"]
        self.assertEqual(anguissa["fvm"], 41)
        self.assertAlmostEqual(anguissa["average_auction_price"], 20.79, places=2)
        self.assertEqual(anguissa["is_pct"], 60)

    def test_ricerca_trova_ogni_fuso_con_entrambe_le_grafie(self):
        with connect_read_only(PUBLISHED_DATABASE) as published:
            for expected_id, spellings in FUSED_PLAYERS.items():
                found_ids = []
                for spelling in spellings:
                    with self.subTest(player_id=expected_id, spelling=spelling):
                        rows = query_players(published, FilterState(search=spelling))
                        player_id = exact_search_result(rows, spelling)
                        self.assertEqual(player_id, expected_id)
                        found_ids.append(player_id)
                self.assertEqual(found_ids[0], found_ids[1])

    def test_nomi_pubblicati_non_contengono_pattern_di_corruzione(self):
        with connect_read_only(PUBLISHED_DATABASE) as published:
            names = [row[0] for row in published.execute(
                "SELECT player_name FROM app_players"
            )]

        corrupted = [
            name for name in names
            if name.rstrip().endswith(("-", "'")) or "2025/2026" in name
        ]
        self.assertEqual(corrupted, [])


if __name__ == "__main__":
    unittest.main()
