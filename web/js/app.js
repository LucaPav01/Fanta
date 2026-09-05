import { buildSearchIndex, closestLastNames, searchPlayers } from "./search.js";
import { cancelAuctionStatus, loadState, markBought, markTaken, parseState, remainingCredits, saveState, serializeState, setHideTaken, takenPlayerIds, toggleFavorite } from "./state.js";
import { createPlayerCard, openOptionsSheet, openPlayerDetail, roleLabel } from "./ui.js";

const ROUTES = ["giocatori", "preferiti", "asta"];
const DEFAULT_AUCTION = { budget: 500, squad_composition: { GK: 3, DEF: 8, MID: 8, FWD: 6 } };
const ROLE_ORDER = ["GK", "DEF", "MID", "FWD"];
const SORT_OPTIONS = [{ value: "fvm", label: "FVM" }, { value: "price", label: "Prezzo medio" }, { value: "is", label: "IS" }, { value: "name", label: "Nome" }, { value: "team", label: "Squadra" }];
const view = document.getElementById("view");
const searchInput = document.getElementById("player-search");
const playerControls = document.getElementById("player-controls");
const roleFilter = document.getElementById("role-filter");
const teamButton = document.getElementById("team-filter-button");
const sortButton = document.getElementById("sort-button");
const tabButtons = [...document.querySelectorAll(".tab-bar__item")];
const restored = loadState();
const state = { players: [], searchIndex: [], team: "", role: "", query: "", sort: "fvm", auction: DEFAULT_AUCTION, generatedAt: "", local: restored.state, persistenceWarning: restored.warning, backupMessage: "" };
let observer;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function datasetTimestamp() {
  if (!state.generatedAt) return null;
  const timestamp = new Date(state.generatedAt);
  if (Number.isNaN(timestamp.getTime())) return null;
  return `Dati aggiornati il ${new Intl.DateTimeFormat("it-IT", { dateStyle: "medium", timeStyle: "short", timeZone: "Europe/Rome" }).format(timestamp)}`;
}
function appendDatasetTimestamp(parent) {
  const label = datasetTimestamp();
  if (label) parent.append(element("p", "dataset-timestamp", label));
}
function route() {
  const hash = location.hash.replace(/^#\/?/, "");
  return ROUTES.includes(hash) ? hash : "giocatori";
}
function number(value) { return value === null || value === undefined ? -Infinity : Number(value); }
function sortPlayers(players) {
  const collator = new Intl.Collator("it", { sensitivity: "base" });
  return [...players].sort((a, b) => {
    if (state.sort === "name") return collator.compare(a.name, b.name);
    if (state.sort === "team") return collator.compare(a.team, b.team) || collator.compare(a.name, b.name);
    const metric = state.sort === "price" ? "price" : state.sort === "is" ? "is_pct" : "fvm";
    return number(b[metric]) - number(a[metric]) || collator.compare(a.name, b.name);
  });
}
function favoriteIds() { return new Set(state.local.preferiti); }
function statusFor(playerId) {
  const mine = state.local.asta.miei.find(({ player_id }) => player_id === playerId);
  return { mine, taken: Boolean(mine || state.local.asta.presi.includes(playerId)) };
}
function filteredPlayers({ favoritesOnly = false, hideTaken = false } = {}) {
  const favorites = favoriteIds();
  const taken = takenPlayerIds(state.local);
  return sortPlayers(searchPlayers(state.searchIndex, state.query).filter((player) =>
    (!state.role || player.role === state.role)
    && (!state.team || player.team === state.team)
    && (!favoritesOnly || favorites.has(player.id))
    && (!hideTaken || !taken.has(player.id)),
  ));
}
function persist(next) {
  const saved = saveState(next);
  state.local = saved.state;
  state.persistenceWarning = saved.warning;
}
function validateAuctionState(next) {
  const spent = next.asta.miei.reduce((total, { prezzo_pagato }) => total + prezzo_pagato, 0);
  if (spent > state.auction.budget) throw new Error("Il backup supera i " + state.auction.budget + " crediti disponibili.");
  const playersById = new Map(state.players.map((player) => [player.id, player]));
  const counts = { GK: 0, DEF: 0, MID: 0, FWD: 0 };
  next.asta.miei.forEach(({ player_id }) => {
    const player = playersById.get(player_id);
    if (!player) throw new Error("Il backup contiene un giocatore che non è presente nel dataset attuale.");
    counts[player.role] += 1;
  });
  ROLE_ORDER.forEach((role) => {
    if (counts[role] > (state.auction.squad_composition[role] || 0)) {
      throw new Error("Il backup supera gli slot disponibili per il ruolo " + roleLabel(role) + ".");
    }
  });
}
function togglePlayerFavorite(player) {
  persist(toggleFavorite(state.local, player.id));
  if (route() === "preferiti") renderFavorites();
  return state.local.preferiti.includes(player.id);
}
function renderCurrent() { mount(route()); }
function openDetail(player) {
  openPlayerDetail(player, {
    isFavorite: favoriteIds().has(player.id), auctionStatus: statusFor(player.id), onToggleFavorite: togglePlayerFavorite,
    onMarkBought: (picked, price) => {
      const oldPrice = statusFor(picked.id).mine?.prezzo_pagato || 0;
      const available = remainingCredits(state.local, state.auction.budget) + oldPrice;
      const teammates = state.local.asta.miei.filter(({ player_id }) => player_id !== picked.id)
        .map(({ player_id }) => state.players.find(({ id }) => id === player_id))
        .filter((teammate) => teammate?.role === picked.role);
      if (!Number.isInteger(price) || price < 1) return "Inserisci un prezzo intero maggiore di zero.";
      if (price > available) return `Hai ${available} crediti disponibili per questo acquisto.`;
      if (teammates.length >= (state.auction.squad_composition[picked.role] || 0)) return `Hai già riempito gli slot per il ruolo ${roleLabel(picked.role)}.`;
      persist(markBought(state.local, picked.id, price)); renderCurrent(); return true;
    },
    onMarkTaken: (picked) => { persist(markTaken(state.local, picked.id)); renderCurrent(); return true; },
    onCancelAuction: (picked) => { persist(cancelAuctionStatus(state.local, picked.id)); renderCurrent(); return true; },
  });
}
function updateControls() {
  roleFilter.querySelectorAll("button").forEach((button) => {
    const active = button.dataset.role === state.role;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  teamButton.classList.toggle("is-active", Boolean(state.team));
  teamButton.textContent = state.team || "Squadra";
  const selected = SORT_OPTIONS.find(({ value }) => value === state.sort);
  sortButton.textContent = selected.label;
}
function chip(label, handler) {
  const button = element("button", "chip");
  button.type = "button"; button.setAttribute("aria-label", `Rimuovi filtro ${label}`);
  button.append(label, element("span", "chip__remove", "×"));
  button.addEventListener("click", handler);
  return button;
}
function renderChips(parent) {
  if (state.role) parent.append(chip(`Ruolo ${roleLabel(state.role)}`, () => { state.role = ""; renderPlayers(); }));
  if (state.team) parent.append(chip(state.team, () => { state.team = ""; renderPlayers(); }));
  if (state.query) parent.append(chip(`“${state.query}”`, () => { state.query = ""; searchInput.value = ""; renderPlayers(); }));
}
function renderEmpty(parent, { title = "Nessun giocatore trovato", text = "", suggestions = false } = {}) {
  const empty = element("div", "empty-state");
  empty.append(element("p", "empty-state__title", title));
  if (text) empty.append(element("p", "", text));
  const names = suggestions ? closestLastNames(state.searchIndex, state.query) : [];
  if (names.length) {
    empty.append(document.createTextNode("Prova uno di questi cognomi:"));
    const choices = element("div", "suggestions");
    names.forEach(({ name }) => {
      const choice = element("button", "suggestion-button", name);
      choice.type = "button";
      choice.addEventListener("click", () => { state.query = name; searchInput.value = name; renderPlayers(); });
      choices.append(choice);
    });
    empty.append(choices);
  }
  parent.append(empty);
}
function cardOptions(player) {
  const auctionStatus = statusFor(player.id);
  return { onOpen: openDetail, onToggleFavorite: togglePlayerFavorite, isFavorite: favoriteIds().has(player.id), auctionStatus: auctionStatus.taken ? (auctionStatus.mine ? "mine" : "other") : "" };
}
function appendInfiniteList(players, parent) {
  if (observer) observer.disconnect();
  const list = element("div", "player-list");
  parent.append(list);
  let rendered = 0;
  const sentinel = element("div", "list-sentinel");
  const appendNext = () => {
    const fragment = document.createDocumentFragment();
    players.slice(rendered, rendered + 40).forEach((player) => fragment.append(createPlayerCard(player, cardOptions(player))));
    rendered += 40; list.append(fragment);
    if (rendered >= players.length) sentinel.remove();
  };
  list.append(sentinel); appendNext();
  if (rendered < players.length) {
    observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting) && rendered < players.length) appendNext();
    }, { root: view, rootMargin: "320px 0px" });
    observer.observe(sentinel);
  }
}
function renderPlayers() {
  const players = filteredPlayers({ hideTaken: state.local.nascondi_gia_presi });
  const content = element("section", "players-view");
  content.append(element("div", "players-summary", `${players.length} ${players.length === 1 ? "giocatore" : "giocatori"}`));
  const chips = element("div", "active-chips"); renderChips(chips);
  if (chips.childElementCount) content.append(chips);
  if (state.local.nascondi_gia_presi) content.append(element("p", "taken-filter-note", "I giocatori già presi sono nascosti."));
  if (players.length) appendInfiniteList(players, content); else renderEmpty(content, { suggestions: true });
  appendDatasetTimestamp(content);
  view.replaceChildren(content); updateControls(); view.scrollTop = 0;
}
function renderFavorites() {
  const players = filteredPlayers({ favoritesOnly: true });
  const content = element("section", "players-view");
  content.append(element("div", "players-summary", `${players.length} ${players.length === 1 ? "preferito" : "preferiti"}`));
  if (players.length) appendInfiniteList(players, content);
  else renderEmpty(content, { title: "Ancora nessun preferito", text: "Tocca la stella accanto a un giocatore per ritrovarlo qui." });
  appendDatasetTimestamp(content);
  view.replaceChildren(content); view.scrollTop = 0;
}
function renderAuction() {
  if (observer) observer.disconnect();
  const content = element("section", "auction-view");
  const remaining = remainingCredits(state.local, state.auction.budget);
  const summary = element("section", "auction-summary");
  summary.append(element("span", "auction-summary__label", "Crediti residui"), element("strong", "auction-summary__value", `${remaining} / ${state.auction.budget}`));
  content.append(summary);
  if (state.backupMessage) {
    content.append(element("p", "backup-feedback", state.backupMessage));
    state.backupMessage = "";
  }
  if (state.persistenceWarning) content.append(element("p", "backup-warning", state.persistenceWarning));
  if (state.local.asta.miei.length || state.local.asta.presi.length) {
    content.append(element("aside", "backup-callout", "Asta in corso: Safari può eliminare lo stato locale dopo un periodo di inattività. Salva ora un backup JSON qui sotto."));
  }
  const hideTaken = element("label", "taken-toggle");
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox"; checkbox.checked = state.local.nascondi_gia_presi;
  checkbox.addEventListener("change", () => { persist(setHideTaken(state.local, checkbox.checked)); renderAuction(); });
  hideTaken.append(checkbox, document.createTextNode(" Nascondi già presi nella lista Giocatori")); content.append(hideTaken);
  const playersById = new Map(state.players.map((player) => [player.id, player]));
  const roster = element("section", "roster");
  roster.append(element("h2", "section-title", "La mia rosa"));
  ROLE_ORDER.forEach((role) => {
    const purchases = state.local.asta.miei.map((purchase) => ({ ...purchase, player: playersById.get(purchase.player_id) })).filter(({ player }) => player?.role === role);
    const slots = state.auction.squad_composition[role] || 0;
    const section = element("section", "roster-role");
    section.append(element("h3", "roster-role__title", `${roleLabel(role)} · ${purchases.length}/${slots} · ${Math.max(0, slots - purchases.length)} liberi`));
    if (!purchases.length) section.append(element("p", "roster-role__empty", "Nessun giocatore acquistato."));
    purchases.forEach(({ player, prezzo_pagato }) => {
      const row = element("button", "roster-player"); row.type = "button";
      row.append(element("span", "roster-player__name", `${player.name} · ${player.team}`), element("strong", "roster-player__price", `${prezzo_pagato} cr`));
      row.addEventListener("click", () => openDetail(player)); section.append(row);
    });
    roster.append(section);
  });
  content.append(roster);
  const backup = element("section", "backup-section");
  backup.append(element("h2", "section-title", "Backup manuale"), element("p", "backup-section__copy", "Esporta lo stato in JSON, copialo e conservalo. Per ripristinare, incolla un backup valido nello stesso spazio."));
  const textarea = document.createElement("textarea");
  textarea.className = "backup-textarea"; textarea.rows = 8; textarea.spellcheck = false; textarea.placeholder = "Il backup JSON comparirà qui…"; textarea.setAttribute("aria-label", "Backup JSON");
  const actions = element("div", "backup-actions");
  const exportButton = element("button", "action-button action-button--primary", "Esporta JSON"); exportButton.type = "button";
  exportButton.addEventListener("click", () => { textarea.value = serializeState(state.local); textarea.focus(); textarea.select(); });
  const importButton = element("button", "action-button", "Ripristina JSON"); importButton.type = "button";
  const feedback = element("p", "backup-feedback");
  importButton.addEventListener("click", () => {
    try {
      const restoredState = parseState(textarea.value);
      validateAuctionState(restoredState);
      persist(restoredState); state.backupMessage = "Backup ripristinato."; renderCurrent();
    }
    catch (error) { feedback.textContent = error.message || "Backup non valido."; }
  });
  actions.append(exportButton, importButton); backup.append(textarea, actions, feedback); content.append(backup);
  appendDatasetTimestamp(content);
  view.replaceChildren(content); view.scrollTop = 0;
}
function mount(activeRoute) {
  const showPlayers = activeRoute === "giocatori";
  searchInput.closest(".app-header__row").hidden = !showPlayers; playerControls.hidden = !showPlayers;
  if (activeRoute === "giocatori") renderPlayers(); else if (activeRoute === "preferiti") renderFavorites(); else renderAuction();
  tabButtons.forEach((button) => {
    const active = button.dataset.route === activeRoute;
    button.classList.toggle("is-active", active); button.toggleAttribute("aria-current", active);
  });
}
function navigate(next) { if (route() === next) mount(next); else location.hash = `/${next}`; }
function openTeamPicker() {
  const teams = [...new Set(state.players.map(({ team }) => team))].sort(new Intl.Collator("it", { sensitivity: "base" }).compare);
  openOptionsSheet({ title: "Squadra", selected: state.team, options: [{ value: "", label: "Tutte le squadre" }, ...teams.map((team) => ({ value: team, label: team }))], onSelect: (team) => { state.team = team; renderPlayers(); } });
}
function openSortPicker() { openOptionsSheet({ title: "Ordina per", options: SORT_OPTIONS, selected: state.sort, onSelect: (sort) => { state.sort = sort; renderPlayers(); } }); }
async function loadPlayers() {
  try {
    const response = await fetch("data/players.json");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    state.players = payload.players || []; state.generatedAt = payload.generated_at || ""; state.searchIndex = buildSearchIndex(state.players);
    state.auction = { ...DEFAULT_AUCTION, ...(payload.config?.auction || {}), squad_composition: { ...DEFAULT_AUCTION.squad_composition, ...(payload.config?.squad_composition || {}) } };
    mount(route());
  } catch (_) {
    view.replaceChildren(element("div", "empty-state", "Impossibile caricare i giocatori. Riprova quando sei connesso."));
  }
}
roleFilter.addEventListener("click", (event) => { const button = event.target.closest("button[data-role]"); if (button) { state.role = button.dataset.role; renderPlayers(); } });
searchInput.addEventListener("input", () => { state.query = searchInput.value; renderPlayers(); });
teamButton.addEventListener("click", openTeamPicker); sortButton.addEventListener("click", openSortPicker);
tabButtons.forEach((button) => button.addEventListener("click", () => navigate(button.dataset.route)));
window.addEventListener("hashchange", () => mount(route()));
if (!location.hash) location.hash = "/giocatori";
loadPlayers();
if ("serviceWorker" in navigator) window.addEventListener("load", () => navigator.serviceWorker.register("service-worker.js").catch(() => {}));
