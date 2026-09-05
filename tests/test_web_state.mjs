import assert from "node:assert/strict";
import test from "node:test";

import {
  STATE_STORAGE_KEY,
  loadState,
  markBought,
  markTaken,
  parseState,
  remainingCredits,
  saveState,
  takenPlayerIds,
  toggleFavorite,
} from "../web/js/state.js";

function storage() {
  const values = new Map();
  return { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) };
}

test("preferiti e stato asta resistono a una nuova apertura", () => {
  const localStorage = storage();
  let state = loadState(localStorage).state;
  state = toggleFavorite(state, "p1");
  state = markBought(state, "p1", 37);
  state = markTaken(state, "p2");
  saveState(state, localStorage);

  const reopened = loadState(localStorage).state;
  assert.equal(reopened.schema_version, 1);
  assert.deepEqual(reopened.preferiti, ["p1"]);
  assert.deepEqual(reopened.asta.miei, [{ player_id: "p1", prezzo_pagato: 37 }]);
  assert.deepEqual(takenPlayerIds(reopened), new Set(["p1", "p2"]));
  assert.equal(remainingCredits(reopened, 500), 463);
  assert.ok(localStorage.getItem(STATE_STORAGE_KEY));
});

test("il backup rifiuta prezzi non validi e conflitti", () => {
  assert.throws(() => parseState("{rotto"), /JSON non valido/);
  assert.throws(() => parseState('{"schema_version":1,"preferiti":[],"asta":{"miei":[{"player_id":"p1","prezzo_pagato":0}],"presi":[]}}'), /intero positivo/);
  const restored = parseState('{"schema_version":1,"preferiti":[],"asta":{"miei":[{"player_id":"p1","prezzo_pagato":12}],"presi":["p1"]}}');
  assert.deepEqual(restored.asta.presi, []);
});
