import json
import tempfile
import unittest
from pathlib import Path

from app_state import (
    MAX_OPPONENT_TEAMS,
    MY_TEAM_ID,
    add_team,
    assign_player,
    cancel_auction_status,
    delete_team,
    empty_state,
    load_state,
    mark_bought,
    mark_taken,
    my_purchases,
    parse_state,
    rename_team,
    save_state,
    taken_player_ids,
    toggle_favorite,
)


class TestStatoPreferitiAsta(unittest.TestCase):
    def test_preferiti_e_assegnazioni_condividono_lo_schema_v2(self):
        state = toggle_favorite(empty_state(), "p1")
        state = mark_bought(state, "p1", 37)
        state = mark_taken(state, "p2")

        self.assertEqual(state["schema_version"], 2)
        self.assertEqual(state["preferiti"], ["p1"])
        self.assertEqual(
            state["assegnazioni"],
            [
                {"player_id": "p1", "squadra_id": MY_TEAM_ID, "prezzo_pagato": 37},
                {"player_id": "p2", "squadra_id": None, "prezzo_pagato": None},
            ],
        )
        self.assertEqual(my_purchases(state), [{"player_id": "p1", "prezzo_pagato": 37}])
        self.assertEqual(taken_player_ids(state), {"p1", "p2"})

    def test_una_nuova_assegnazione_sostituisce_la_precedente_e_supporta_annulla(self):
        state = mark_taken(empty_state(), "p1")
        state = mark_bought(state, "p1", 20)
        self.assertEqual(len(state["assegnazioni"]), 1)
        self.assertEqual(state["assegnazioni"][0]["squadra_id"], MY_TEAM_ID)

        state = mark_taken(state, "p1")
        self.assertEqual(state["assegnazioni"][0]["squadra_id"], None)
        self.assertEqual(cancel_auction_status(state, "p1")["assegnazioni"], [])

    def test_salvataggio_e_ricaricamento_json(self):
        state = mark_bought(toggle_favorite(empty_state(), "p1"), "p2", 11)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            save_state(path, state)
            loaded, warning = load_state(path)

        self.assertIsNone(warning)
        self.assertEqual(loaded, state)

    def test_file_v1_viene_migrato_mantenendo_un_backup_intatto(self):
        old_state = {
            "preferiti": ["p3"],
            "asta": {
                "miei": [{"player_id": "p1", "prezzo_pagato": 15}],
                "presi": ["p1", "p2"],
            },
            "nascondi_gia_presi": True,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            original = json.dumps(old_state, ensure_ascii=False)
            path.write_text(original, encoding="utf-8")
            loaded, warning = load_state(path)
            backup = path.with_name("state.json.v1.bak")

            self.assertIsNone(warning)
            self.assertEqual(backup.read_text(encoding="utf-8"), original)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema_version"], 2)

        self.assertEqual(loaded["preferiti"], ["p3"])
        self.assertEqual(
            loaded["assegnazioni"],
            [
                {"player_id": "p1", "squadra_id": MY_TEAM_ID, "prezzo_pagato": 15},
                {"player_id": "p2", "squadra_id": None, "prezzo_pagato": None},
            ],
        )

    def test_backup_v1_puo_essere_ripristinato(self):
        restored = parse_state(
            '{"schema_version":1,"preferiti":[],"asta":{"miei":[],"presi":["p2"]}}'
        )
        self.assertEqual(restored["schema_version"], 2)
        self.assertEqual(
            restored["assegnazioni"],
            [{"player_id": "p2", "squadra_id": None, "prezzo_pagato": None}],
        )

    def test_squadre_validate_e_protette_quando_hanno_giocatori(self):
        state = add_team(empty_state(), "I Falchi")
        team_id = state["squadre"][0]["id"]
        state = assign_player(state, "p4", team_id, 21)
        with self.assertRaisesRegex(ValueError, "giocatori assegnati"):
            rename_team(state, team_id, "Le Aquile")
        with self.assertRaisesRegex(ValueError, "giocatori assegnati"):
            delete_team(state, team_id)

        available = add_team(empty_state(), "Le Aquile")
        renamed = rename_team(available, available["squadre"][0]["id"], "Le Tigri")
        self.assertEqual(renamed["squadre"][0]["nome"], "Le Tigri")
        self.assertEqual(delete_team(available, available["squadre"][0]["id"])["squadre"], [])
        with self.assertRaisesRegex(ValueError, "univoci"):
            add_team(add_team(empty_state(), "Falchi"), " falchi ")
        with self.assertRaisesRegex(ValueError, "inesistente"):
            assign_player(empty_state(), "p1", "inesistente", 10)
        with self.assertRaisesRegex(ValueError, "prezzo_pagato"):
            assign_player(available, "p1", available["squadre"][0]["id"])

    def test_massimo_nove_squadre_avversarie(self):
        state = empty_state()
        for index in range(1, MAX_OPPONENT_TEAMS + 1):
            state = add_team(state, f"Squadra {index}")
        with self.assertRaisesRegex(ValueError, "al massimo 9"):
            add_team(state, "Squadra 10")

    def test_ripristino_rifiuta_json_e_prezzi_non_validi(self):
        with self.assertRaisesRegex(ValueError, "JSON non valido"):
            parse_state("{rotto")
        with self.assertRaisesRegex(ValueError, "intero positivo"):
            parse_state(
                '{"schema_version":2,"preferiti":[],"squadre":[],"assegnazioni":'
                '[{"player_id":"p1","squadra_id":"mia","prezzo_pagato":0}]}'
            )


if __name__ == "__main__":
    unittest.main()
