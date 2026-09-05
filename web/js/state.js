export const STATE_STORAGE_KEY = "fanta_state";
export const SCHEMA_VERSION = 1;

export function emptyState() {
  return {
    schema_version: SCHEMA_VERSION,
    preferiti: [],
    asta: { miei: [], presi: [] },
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

function migrate(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Il backup non contiene uno stato valido.");
  if (value.schema_version === undefined) return { ...value, schema_version: SCHEMA_VERSION };
  if (value.schema_version !== SCHEMA_VERSION) throw new Error("Questo backup usa una versione non supportata dell'app.");
  return value;
}

export function normalizeState(value) {
  const state = migrate(value);
  if (!state.asta || typeof state.asta !== "object" || Array.isArray(state.asta)) throw new Error("Lo stato dell'asta non è valido.");
  const mieiById = new Map();
  if (!Array.isArray(state.asta.miei)) throw new Error("L'elenco dei tuoi acquisti non è valido.");
  state.asta.miei.forEach((purchase) => {
    if (!purchase || typeof purchase !== "object" || typeof purchase.player_id !== "string" || !purchase.player_id.trim()) {
      throw new Error("Ogni acquisto deve indicare un giocatore.");
    }
    const price = purchase.prezzo_pagato;
    if (!Number.isInteger(price) || price < 1) throw new Error("Ogni prezzo pagato deve essere un intero positivo.");
    mieiById.set(purchase.player_id.trim(), { player_id: purchase.player_id.trim(), prezzo_pagato: price });
  });
  const miei = [...mieiById.values()];
  const mieiIds = new Set(miei.map(({ player_id: id }) => id));
  return {
    schema_version: SCHEMA_VERSION,
    preferiti: playerIds(state.preferiti, "I preferiti"),
    asta: {
      miei,
      presi: playerIds(state.asta.presi, "I giocatori presi da altri").filter((id) => !mieiIds.has(id)),
    },
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

export function loadState(storage = window.localStorage) {
  try {
    const saved = storage.getItem(STATE_STORAGE_KEY);
    return { state: saved ? parseState(saved) : emptyState(), warning: "" };
  } catch (_) {
    return { state: emptyState(), warning: "Non riesco a leggere lo stato locale. Esporta un backup appena possibile." };
  }
}

export function saveState(state, storage = window.localStorage) {
  const normalized = normalizeState(state);
  try {
    storage.setItem(STATE_STORAGE_KEY, serializeState(normalized));
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

export function markBought(state, playerId, price) {
  const next = normalizeState(state);
  next.asta.miei = next.asta.miei.filter(({ player_id: id }) => id !== playerId);
  next.asta.miei.push({ player_id: playerId, prezzo_pagato: price });
  next.asta.presi = next.asta.presi.filter((id) => id !== playerId);
  return next;
}

export function markTaken(state, playerId) {
  const next = normalizeState(state);
  next.asta.miei = next.asta.miei.filter(({ player_id: id }) => id !== playerId);
  if (!next.asta.presi.includes(playerId)) next.asta.presi.push(playerId);
  return next;
}

export function cancelAuctionStatus(state, playerId) {
  const next = normalizeState(state);
  next.asta.miei = next.asta.miei.filter(({ player_id: id }) => id !== playerId);
  next.asta.presi = next.asta.presi.filter((id) => id !== playerId);
  return next;
}

export function setHideTaken(state, hidden) {
  return { ...normalizeState(state), nascondi_gia_presi: Boolean(hidden) };
}

export function takenPlayerIds(state) {
  const normalized = normalizeState(state);
  return new Set([...normalized.asta.presi, ...normalized.asta.miei.map(({ player_id: id }) => id)]);
}

export function remainingCredits(state, budget) {
  const spent = normalizeState(state).asta.miei.reduce((total, { prezzo_pagato: price }) => total + price, 0);
  return Math.max(0, budget - spent);
}
