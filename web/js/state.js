export const STATE_STORAGE_KEY = "fanta_state";
export const STATE_V1_BACKUP_STORAGE_KEY = "fanta_state_backup_v1";
export const SCHEMA_VERSION = 2;
export const MY_TEAM_ID = "mia";
export const MAX_OPPONENT_TEAMS = 9;
const SNAPSHOT_KEYS = ["fanta_state_snap_0", "fanta_state_snap_1", "fanta_state_snap_2"];
const SNAPSHOT_INDEX_KEY = "fanta_state_snap_idx";
const IDB_NAME = "fanta";
const IDB_STORE = "state";
const IDB_KEY = "current";

export function emptyState() {
  return {
    schema_version: SCHEMA_VERSION,
    preferiti: [],
    squadre: [],
    assegnazioni: [],
    nascondi_gia_presi: false,
  };
}

function playerIds(value, label) {
  if (!Array.isArray(value)) throw new Error(`${label} deve essere un elenco.`);
  const ids = [];
  value.forEach((id) => {
    if (typeof id !== "string" || !id.trim()) throw new Error(`${label} contiene un giocatore non valido.`);
    if (!ids.includes(id.trim())) ids.push(id.trim());
  });
  return ids;
}

function migrateV1(value) {
  if (!value.asta || typeof value.asta !== "object" || Array.isArray(value.asta)) {
    throw new Error("Lo stato dell'asta non è valido.");
  }
  if (!Array.isArray(value.asta.miei)) throw new Error("L'elenco dei tuoi acquisti non è valido.");
  const mine = value.asta.miei.map((purchase) => ({
    player_id: purchase?.player_id,
    squadra_id: MY_TEAM_ID,
    prezzo_pagato: purchase?.prezzo_pagato,
  }));
  const mineIds = new Set(mine.map(({ player_id: id }) => typeof id === "string" ? id.trim() : id));
  const others = playerIds(value.asta.presi, "I giocatori presi da altri")
    .filter((id) => !mineIds.has(id))
    .map((player_id) => ({ player_id, squadra_id: null, prezzo_pagato: null }));
  return {
    schema_version: SCHEMA_VERSION,
    preferiti: value.preferiti,
    squadre: [],
    assegnazioni: [...mine, ...others],
    nascondi_gia_presi: value.nascondi_gia_presi,
  };
}

function migrate(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Il backup non contiene uno stato valido.");
  if (value.schema_version === undefined || value.schema_version === 1) return migrateV1(value);
  if (value.schema_version !== SCHEMA_VERSION) throw new Error("Questo backup usa una versione non supportata dell'app.");
  return value;
}

function normalizeTeams(value) {
  if (!Array.isArray(value)) throw new Error("Le squadre avversarie devono essere un elenco.");
  if (value.length > MAX_OPPONENT_TEAMS) throw new Error(`Puoi configurare al massimo ${MAX_OPPONENT_TEAMS} squadre avversarie.`);
  const ids = new Set();
  const names = new Set();
  return value.map((team) => {
    if (!team || typeof team !== "object" || Array.isArray(team)) throw new Error("Ogni squadra deve contenere id e nome.");
    const id = typeof team.id === "string" ? team.id.trim() : "";
    const nome = typeof team.nome === "string" ? team.nome.trim() : "";
    if (!id || id === MY_TEAM_ID) throw new Error("Ogni squadra avversaria deve avere un id valido.");
    if (!nome) throw new Error("Ogni squadra avversaria deve avere un nome.");
    const normalizedName = nome.toLocaleLowerCase("it-IT");
    if (ids.has(id)) throw new Error("Gli id delle squadre avversarie devono essere univoci.");
    if (names.has(normalizedName)) throw new Error("I nomi delle squadre avversarie devono essere univoci.");
    ids.add(id); names.add(normalizedName);
    return { id, nome };
  });
}

function normalizeAssignments(value, teams) {
  if (!Array.isArray(value)) throw new Error("Le assegnazioni devono essere un elenco.");
  const teamIds = new Set(teams.map(({ id }) => id));
  const byPlayer = new Map();
  value.forEach((assignment) => {
    if (!assignment || typeof assignment !== "object" || Array.isArray(assignment)) {
      throw new Error("Ogni assegnazione deve indicare un giocatore e una squadra.");
    }
    const playerId = typeof assignment.player_id === "string" ? assignment.player_id.trim() : "";
    if (!playerId) throw new Error("Ogni assegnazione deve indicare un giocatore valido.");
    const teamId = assignment.squadra_id === null
      ? null
      : typeof assignment.squadra_id === "string" ? assignment.squadra_id.trim() : "";
    if (teamId !== null && teamId !== MY_TEAM_ID && !teamIds.has(teamId)) {
      throw new Error("Un'assegnazione fa riferimento a una squadra inesistente.");
    }
    const price = assignment.prezzo_pagato;
    if (price !== null && (!Number.isInteger(price) || price < 1)) {
      throw new Error("Ogni prezzo pagato deve essere nullo oppure un intero positivo.");
    }
    if (teamId !== null && price === null) throw new Error("Le assegnazioni a una squadra devono avere un prezzo pagato.");
    byPlayer.set(playerId, { player_id: playerId, squadra_id: teamId, prezzo_pagato: price });
  });
  return [...byPlayer.values()];
}

export function normalizeState(value) {
  const state = migrate(value);
  const teams = normalizeTeams(state.squadre);
  return {
    schema_version: SCHEMA_VERSION,
    preferiti: playerIds(state.preferiti, "I preferiti"),
    squadre: teams,
    assegnazioni: normalizeAssignments(state.assegnazioni, teams),
    nascondi_gia_presi: Boolean(state.nascondi_gia_presi),
  };
}

export function serializeState(state) {
  return JSON.stringify(normalizeState(state), null, 2);
}

export function parseState(serialized) {
  let value;
  try {
    value = JSON.parse(serialized);
  } catch (_) {
    throw new Error("JSON non valido.");
  }
  return normalizeState(value);
}

export function encodeStateForLink(state) {
  const json = JSON.stringify(normalizeState(state));
  const bytes = new TextEncoder().encode(json);
  let binary = "";
  bytes.forEach((byte) => { binary += String.fromCharCode(byte); });
  return btoa(binary);
}

export function decodeStateFromLink(encoded) {
  let binary;
  try {
    binary = atob(encoded);
  } catch (_) {
    throw new Error("Il link di ripristino non è valido.");
  }
  const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
  const json = new TextDecoder().decode(bytes);
  return parseState(json);
}

function writeSnapshot(storage, serialized) {
  try {
    const current = Number(storage.getItem(SNAPSHOT_INDEX_KEY));
    const slot = Number.isInteger(current) ? ((current % SNAPSHOT_KEYS.length) + SNAPSHOT_KEYS.length) % SNAPSHOT_KEYS.length : 0;
    storage.setItem(SNAPSHOT_KEYS[slot], JSON.stringify({ timestamp: Date.now(), data: serialized }));
    storage.setItem(SNAPSHOT_INDEX_KEY, String(slot + 1));
  } catch (_) {
    // il fallimento dello snapshot rotativo non deve mai bloccare il salvataggio principale
  }
}

function recoverFromSnapshots(storage) {
  let best = null;
  SNAPSHOT_KEYS.forEach((key) => {
    try {
      const raw = storage.getItem(key);
      if (!raw) return;
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed.timestamp !== "number" || typeof parsed.data !== "string") return;
      if (!best || parsed.timestamp >= best.timestamp) best = parsed;
    } catch (_) {
      // snapshot corrotto: ignoralo e prova il prossimo
    }
  });
  if (!best) return null;
  try {
    return normalizeState(JSON.parse(best.data));
  } catch (_) {
    return null;
  }
}

function recoverFromV1Backup(storage) {
  try {
    const backup = storage.getItem(STATE_V1_BACKUP_STORAGE_KEY);
    if (!backup) return null;
    return normalizeState(JSON.parse(backup));
  } catch (_) {
    return null;
  }
}

function recoverState(storage) {
  const fromSnapshot = recoverFromSnapshots(storage);
  if (fromSnapshot) return { state: fromSnapshot, warning: "Stato recuperato da una copia di sicurezza locale." };
  const fromV1 = recoverFromV1Backup(storage);
  if (fromV1) return { state: fromV1, warning: "Stato recuperato dal backup v1." };
  return { state: emptyState(), warning: "Non riesco a leggere lo stato locale. Esporta un backup appena possibile." };
}

function openStateDb() {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === "undefined") { reject(new Error("IndexedDB non disponibile.")); return; }
    const request = indexedDB.open(IDB_NAME, 1);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(IDB_STORE)) db.createObjectStore(IDB_STORE);
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("Apertura IndexedDB fallita."));
  });
}

function mirrorToIndexedDB(state) {
  openStateDb().then((db) => {
    try {
      const tx = db.transaction(IDB_STORE, "readwrite");
      tx.objectStore(IDB_STORE).put(state, IDB_KEY);
      tx.oncomplete = () => db.close();
      tx.onerror = () => db.close();
    } catch (_) {
      // il mirror asincrono non deve mai bloccare il salvataggio principale
    }
  }).catch(() => {});
}

export function readFromIndexedDB() {
  return openStateDb().then((db) => new Promise((resolve) => {
    try {
      const tx = db.transaction(IDB_STORE, "readonly");
      const getRequest = tx.objectStore(IDB_STORE).get(IDB_KEY);
      getRequest.onsuccess = () => { resolve(getRequest.result || null); db.close(); };
      getRequest.onerror = () => { resolve(null); db.close(); };
    } catch (_) {
      resolve(null);
    }
  })).catch(() => null);
}

export function loadState(storage = window.localStorage) {
  try {
    const saved = storage.getItem(STATE_STORAGE_KEY);
    if (saved) {
      const raw = JSON.parse(saved);
      const isV1 = raw?.schema_version === undefined || raw?.schema_version === 1;
      const state = normalizeState(raw);
      if (isV1) {
        try {
          if (!storage.getItem(STATE_V1_BACKUP_STORAGE_KEY)) storage.setItem(STATE_V1_BACKUP_STORAGE_KEY, saved);
          storage.setItem(STATE_STORAGE_KEY, serializeState(state));
        } catch (_) {
          return { state, warning: "Stato v1 recuperato, ma la migrazione locale non è stata salvata: esporta subito un backup JSON." };
        }
      }
      return { state, warning: "" };
    }
  } catch (_) {
    // stato principale illeggibile o corrotto: prova la catena di recupero sotto
  }
  return recoverState(storage);
}

export function saveState(state, storage = window.localStorage) {
  const normalized = normalizeState(state);
  try {
    const serialized = serializeState(normalized);
    storage.setItem(STATE_STORAGE_KEY, serialized);
    writeSnapshot(storage, serialized);
    try { mirrorToIndexedDB(normalized); } catch (_) {}
    return { state: normalized, warning: "" };
  } catch (_) {
    return { state: normalized, warning: "Il salvataggio locale non è disponibile: conserva subito un backup JSON." };
  }
}

export function toggleFavorite(state, playerId) {
  const next = normalizeState(state);
  const index = next.preferiti.indexOf(playerId);
  if (index === -1) next.preferiti.push(playerId); else next.preferiti.splice(index, 1);
  return next;
}

function nextTeamId(teams) {
  let index = 1;
  const ids = new Set(teams.map(({ id }) => id));
  while (ids.has(`avversaria-${index}`)) index += 1;
  return `avversaria-${index}`;
}

export function addTeam(state, name) {
  const next = normalizeState(state);
  if (next.squadre.length >= MAX_OPPONENT_TEAMS) throw new Error(`Puoi configurare al massimo ${MAX_OPPONENT_TEAMS} squadre avversarie.`);
  next.squadre.push({ id: nextTeamId(next.squadre), nome: typeof name === "string" ? name.trim() : "" });
  return normalizeState(next);
}

export function renameTeam(state, teamId, name) {
  const next = normalizeState(state);
  const team = next.squadre.find(({ id }) => id === teamId);
  if (!team) throw new Error("Squadra avversaria non trovata.");
  if (next.assegnazioni.some(({ squadra_id: id }) => id === teamId)) {
    throw new Error("Non puoi modificare una squadra che ha già dei giocatori assegnati.");
  }
  team.nome = typeof name === "string" ? name.trim() : "";
  return normalizeState(next);
}

export function deleteTeam(state, teamId) {
  const next = normalizeState(state);
  if (!next.squadre.some(({ id }) => id === teamId)) throw new Error("Squadra avversaria non trovata.");
  if (next.assegnazioni.some(({ squadra_id: id }) => id === teamId)) {
    throw new Error("Non puoi eliminare una squadra che ha già dei giocatori assegnati.");
  }
  next.squadre = next.squadre.filter(({ id }) => id !== teamId);
  return next;
}

export function assignPlayer(state, playerId, teamId, price = null) {
  const next = normalizeState(state);
  const cleanPlayerId = typeof playerId === "string" ? playerId.trim() : "";
  if (!cleanPlayerId) throw new Error("Ogni assegnazione deve indicare un giocatore valido.");
  const cleanTeamId = teamId === null ? null : typeof teamId === "string" ? teamId.trim() : "";
  next.assegnazioni = next.assegnazioni.filter(({ player_id: id }) => id !== cleanPlayerId);
  next.assegnazioni.push({ player_id: cleanPlayerId, squadra_id: cleanTeamId, prezzo_pagato: price });
  return normalizeState(next);
}

export function markBought(state, playerId, price) {
  return assignPlayer(state, playerId, MY_TEAM_ID, price);
}

export function markTaken(state, playerId, teamId = null, price = null) {
  return assignPlayer(state, playerId, teamId, price);
}

export function cancelAuctionStatus(state, playerId) {
  const next = normalizeState(state);
  next.assegnazioni = next.assegnazioni.filter(({ player_id: id }) => id !== playerId);
  return next;
}

export function setHideTaken(state, hidden) {
  return { ...normalizeState(state), nascondi_gia_presi: Boolean(hidden) };
}

export function assignmentFor(state, playerId) {
  return normalizeState(state).assegnazioni.find(({ player_id: id }) => id === playerId) || null;
}

export function myPurchases(state) {
  return normalizeState(state).assegnazioni
    .filter(({ squadra_id: id }) => id === MY_TEAM_ID)
    .map(({ player_id, prezzo_pagato }) => ({ player_id, prezzo_pagato }));
}

export function takenPlayerIds(state) {
  return new Set(normalizeState(state).assegnazioni.map(({ player_id: id }) => id));
}

export function remainingCredits(state, budget) {
  const spent = myPurchases(state).reduce((total, { prezzo_pagato: price }) => total + price, 0);
  return Math.max(0, budget - spent);
}
