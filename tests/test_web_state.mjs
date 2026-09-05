import assert from "node:assert/strict";
import test from "node:test";

import {
  MAX_OPPONENT_TEAMS,
  MY_TEAM_ID,
  STATE_STORAGE_KEY,
  STATE_V1_BACKUP_STORAGE_KEY,
  addTeam,
  assignPlayer,
  decodeStateFromLink,
  deleteTeam,
  emptyState,
  encodeStateForLink,
  loadState,
  markBought,
  markTaken,
  myPurchases,
  parseState,
  remainingCredits,
  renameTeam,
  saveState,
  takenPlayerIds,
  toggleFavorite,
} from "../web/js/state.js";

const SNAPSHOT_KEYS = ["fanta_state_snap_0", "fanta_state_snap_1", "fanta_state_snap_2"];

function storage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) };
}

test("preferiti e assegnazioni v2 resistono a una nuova apertura", () => {
  const localStorage = storage();
  let state = toggleFavorite(emptyState(), "p1");
  state = markBought(state, "p1", 37);
  state = markTaken(state, "p2");
  saveState(state, localStorage);

  const reopened = loadState(localStorage).state;
  assert.equal(reopened.schema_version, 2);
  assert.deepEqual(reopened.preferiti, ["p1"]);
  assert.deepEqual(reopened.assegnazioni, [
    { player_id: "p1", squadra_id: MY_TEAM_ID, prezzo_pagato: 37 },
    { player_id: "p2", squadra_id: null, prezzo_pagato: null },
  ]);
  assert.deepEqual(myPurchases(reopened), [{ player_id: "p1", prezzo_pagato: 37 }]);
  assert.deepEqual(takenPlayerIds(reopened), new Set(["p1", "p2"]));
  assert.equal(remainingCredits(reopened, 500), 463);
});

test("lo stato locale v1 viene migrato, salvato in v2 e conservato come backup", () => {
  const v1 = JSON.stringify({
    schema_version: 1,
    preferiti: ["p3"],
    asta: { miei: [{ player_id: "p1", prezzo_pagato: 12 }], presi: ["p1", "p2"] },
    nascondi_gia_presi: true,
  });
  const localStorage = storage({ [STATE_STORAGE_KEY]: v1 });
  const { state, warning } = loadState(localStorage);

  assert.equal(warning, "");
  assert.equal(state.schema_version, 2);
  assert.deepEqual(state.preferiti, ["p3"]);
  assert.deepEqual(state.assegnazioni, [
    { player_id: "p1", squadra_id: MY_TEAM_ID, prezzo_pagato: 12 },
    { player_id: "p2", squadra_id: null, prezzo_pagato: null },
  ]);
  assert.equal(state.nascondi_gia_presi, true);
  assert.equal(localStorage.getItem(STATE_V1_BACKUP_STORAGE_KEY), v1);
  assert.equal(JSON.parse(localStorage.getItem(STATE_STORAGE_KEY)).schema_version, 2);
});

test("anche un backup manuale v1 resta ripristinabile", () => {
  const restored = parseState('{"schema_version":1,"preferiti":[],"asta":{"miei":[{"player_id":"p1","prezzo_pagato":12}],"presi":["p1"]}}');
  assert.equal(restored.schema_version, 2);
  assert.deepEqual(restored.assegnazioni, [
    { player_id: "p1", squadra_id: MY_TEAM_ID, prezzo_pagato: 12 },
  ]);
});

test("squadre e assegnazioni sono validate e una squadra in uso è immutabile", () => {
  let state = addTeam(emptyState(), "I Falchi");
  const teamId = state.squadre[0].id;
  state = assignPlayer(state, "p4", teamId, 21);
  assert.deepEqual(state.assegnazioni[0], { player_id: "p4", squadra_id: teamId, prezzo_pagato: 21 });
  assert.throws(() => renameTeam(state, teamId, "Le Aquile"), /giocatori assegnati/);
  assert.throws(() => deleteTeam(state, teamId), /giocatori assegnati/);

  const available = addTeam(emptyState(), "Le Aquile");
  assert.equal(renameTeam(available, available.squadre[0].id, "Le Tigri").squadre[0].nome, "Le Tigri");
  assert.deepEqual(deleteTeam(available, available.squadre[0].id).squadre, []);
  assert.throws(() => addTeam(addTeam(emptyState(), "Falchi"), " falchi "), /univoci/);
  assert.throws(() => assignPlayer(emptyState(), "p1", "inesistente", 10), /inesistente/);
  assert.throws(() => assignPlayer(available, "p1", available.squadre[0].id), /prezzo pagato/);
});

test("non si possono configurare più di nove squadre avversarie", () => {
  let state = emptyState();
  for (let index = 1; index <= MAX_OPPONENT_TEAMS; index += 1) state = addTeam(state, `Squadra ${index}`);
  assert.throws(() => addTeam(state, "Squadra 10"), /al massimo 9/);
});

test("ogni salvataggio scrive uno snapshot rotativo su tre slot", () => {
  const localStorage = storage();
  let state = markBought(emptyState(), "p1", 10);
  saveState(state, localStorage);
  state = markBought(state, "p2", 20);
  saveState(state, localStorage);
  state = markBought(state, "p3", 30);
  saveState(state, localStorage);
  state = markBought(state, "p4", 40);
  saveState(state, localStorage);

  const filled = SNAPSHOT_KEYS.filter((key) => localStorage.getItem(key));
  assert.equal(filled.length, 3);
  const latest = SNAPSHOT_KEYS
    .map((key) => JSON.parse(localStorage.getItem(key)))
    .sort((a, b) => b.timestamp - a.timestamp)[0];
  assert.deepEqual(JSON.parse(latest.data).assegnazioni.map(({ player_id }) => player_id).sort(), ["p1", "p2", "p3", "p4"]);
});

test("se lo stato principale è corrotto, il caricamento recupera dallo snapshot più recente", () => {
  const localStorage = storage();
  let state = markBought(emptyState(), "p1", 15);
  saveState(state, localStorage);
  state = toggleFavorite(state, "p9");
  saveState(state, localStorage);

  localStorage.setItem(STATE_STORAGE_KEY, "{rotto");
  const { state: recovered, warning } = loadState(localStorage);
  assert.match(warning, /copia di sicurezza/);
  assert.deepEqual(recovered.preferiti, ["p9"]);
  assert.deepEqual(recovered.assegnazioni, [{ player_id: "p1", squadra_id: MY_TEAM_ID, prezzo_pagato: 15 }]);
});

test("se non ci sono snapshot ma esiste un backup v1, il caricamento recupera da quello", () => {
  const v1 = JSON.stringify({ schema_version: 1, preferiti: ["p5"], asta: { miei: [], presi: [] } });
  const localStorage = storage({ [STATE_V1_BACKUP_STORAGE_KEY]: v1 });
  const { state, warning } = loadState(localStorage);
  assert.match(warning, /backup v1/);
  assert.deepEqual(state.preferiti, ["p5"]);
});

test("il backup rifiuta JSON e prezzi non validi", () => {
  assert.throws(() => parseState("{rotto"), /JSON non valido/);
  assert.throws(
    () => parseState('{"schema_version":2,"preferiti":[],"squadre":[],"assegnazioni":[{"player_id":"p1","squadra_id":"mia","prezzo_pagato":0}]}'),
    /intero positivo/,
  );
});

test("lo stato codificato per il link di ripristino si decodifica nello stesso stato, anche con caratteri accentati", () => {
  let state = addTeam(emptyState(), "Città Città");
  state = assignPlayer(state, "p1", state.squadre[0].id, 15);
  state = toggleFavorite(state, "p2");

  const encoded = encodeStateForLink(state);
  assert.equal(typeof encoded, "string");
  const decoded = decodeStateFromLink(encoded);
  assert.deepEqual(decoded, state);
});

test("un link di ripristino corrotto viene segnalato", () => {
  assert.throws(() => decodeStateFromLink("###non-base64###"), /link di ripristino/);
});
