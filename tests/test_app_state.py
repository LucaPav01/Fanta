import tempfile
import unittest
from pathlib import Path

from app_state import (
    cancel_auction_status,
    empty_state,
    load_state,
    mark_bought,
    mark_taken,
    parse_state,
    save_state,
    taken_player_ids,
    toggle_favorite,
)


class TestStatoPreferitiAsta(unittest.TestCase):
    def test_preferiti_e_asta_condividono_il_formato_richiesto(self):
        state = toggle_favorite(empty_state(), "p1")
        state = mark_bought(state, "p1", 37)
        state = mark_taken(state, "p2")

        self.assertEqual(state["preferiti"], ["p1"])
        self.assertEqual(state["asta"]["miei"], [{"player_id": "p1", "prezzo_pagato": 37}])
        self.assertEqual(state["asta"]["presi"], ["p2"])
        self.assertEqual(taken_player_ids(state), {"p1", "p2"})

    def test_uno_stato_asta_esclude_conflitti_e_supporta_annulla(self):
        state = mark_taken(empty_state(), "p1")
        state = mark_bought(state, "p1", 20)
        self.assertEqual(state["asta"]["presi"], [])

        state = mark_taken(state, "p1")
        self.assertEqual(state["asta"]["miei"], [])
        self.assertEqual(state["asta"]["presi"], ["p1"])
        self.assertEqual(cancel_auction_status(state, "p1")["asta"]["presi"], [])

    def test_salvataggio_e_ricaricamento_json(self):
        state = mark_bought(toggle_favorite(empty_state(), "p1"), "p2", 11)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            save_state(path, state)
            loaded, warning = load_state(path)

        self.assertIsNone(warning)
        self.assertEqual(loaded, state)

    def test_ripristino_rifiuta_json_non_valido(self):
        with self.assertRaisesRegex(ValueError, "JSON non valido"):
            parse_state("{rotto")
        with self.assertRaisesRegex(ValueError, "intero positivo"):
            parse_state(
                '{"preferiti": [], "asta": {"miei": '
                '[{"player_id": "p1", "prezzo_pagato": 0}], "presi": []}}'
            )


if __name__ == "__main__":
    unittest.main()
