import { buildSearchIndex, closestLastNames, searchPlayers } from "./search.js";
import { MAX_OPPONENT_TEAMS, MY_TEAM_ID, addTeam, assignmentFor, cancelAuctionStatus, decodeStateFromLink, deleteTeam, encodeStateForLink, loadState, markBought, markTaken, myPurchases, normalizeState, parseState, readFromIndexedDB, remainingCredits, renameTeam, saveState, serializeState, setHideTaken, takenPlayerIds, toggleFavorite } from "./state.js";
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
const tierButton = document.getElementById("tier-filter-button");
const sortButton = document.getElementById("sort-button");
const tabButtons = [...document.querySelectorAll(".tab-bar__item")];
const restored = loadState();
const state = { players: [], searchIndex: [], team: "", role: "", tier: "", query: "", sort: "fvm", auction: DEFAULT_AUCTION, generatedAt: "", local: restored.state, persistenceWarning: restored.warning, backupMessage: "" };
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
function percent(value, total) { return total ? Math.round((value / total) * 100) : 0; }
function sortPlayers(players) {
  const collator = new Intl.Collator("it", { sensitivity: "base" });
  return [...players].sort((a, b) => {
    if (state.sort === "name") return collator.compare(a.name, b.name);
    if (state.sort === "team") return collator.compare(a.team, b.team) || collator.compare(a.name, b.name);
    const metric = state.sort === "price" ? "price" : state.sort === "is" ? "is_pct" : "fvm";
    return number(b[metric]) - number(a[metric]) || collator.compare(a.name, b.name);
  });
}
function tierOptions() {
  return [...new Set(state.players.map(({ fvm_tier }) => fvm_tier).filter(Boolean))]
    .sort((a, b) => Number(a.replace("Fascia ", "")) - Number(b.replace("Fascia ", "")))
    .map((tier) => ({ value: tier, label: tier }));
}
function favoriteIds() { return new Set(state.local.preferiti); }
function statusFor(playerId) {
  const assignment = assignmentFor(state.local, playerId);
  const mine = assignment?.squadra_id === MY_TEAM_ID
    ? { player_id: assignment.player_id, prezzo_pagato: assignment.prezzo_pagato }
    : null;
  return { mine, assignment, taken: Boolean(assignment) };
}
function filteredPlayers({ favoritesOnly = false, hideTaken = false } = {}) {
  const favorites = favoriteIds();
  const taken = takenPlayerIds(state.local);
  return sortPlayers(searchPlayers(state.searchIndex, state.query).filter((player) =>
    (!state.role || player.role === state.role)
    && (!state.team || player.team === state.team)
    && (!state.tier || player.fvm_tier === state.tier)
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
  const purchases = myPurchases(next);
  const spent = purchases.reduce((total, { prezzo_pagato }) => total + prezzo_pagato, 0);
  if (spent > state.auction.budget) throw new Error("Il backup supera i " + state.auction.budget + " crediti disponibili.");
  const playersById = new Map(state.players.map((player) => [player.id, player]));
  const counts = { GK: 0, DEF: 0, MID: 0, FWD: 0 };
  next.assegnazioni.forEach(({ player_id }) => {
    const player = playersById.get(player_id);
    if (!player) throw new Error("Il backup contiene un giocatore che non è presente nel dataset attuale.");
  });
  purchases.forEach(({ player_id }) => {
    const player = playersById.get(player_id);
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
      const teammates = myPurchases(state.local).filter(({ player_id }) => player_id !== picked.id)
        .map(({ player_id }) => state.players.find(({ id }) => id === player_id))
        .filter((teammate) => teammate?.role === picked.role);
      if (!Number.isInteger(price) || price < 1) return "Inserisci un prezzo intero maggiore di zero.";
      if (price > available) return `Hai ${available} crediti disponibili per questo acquisto.`;
      if (teammates.length >= (state.auction.squad_composition[picked.role] || 0)) return `Hai già riempito gli slot per il ruolo ${roleLabel(picked.role)}.`;
      persist(markBought(state.local, picked.id, price)); renderCurrent(); return true;
    },
    onMarkTaken: (picked, price) => {
      if (!Number.isInteger(price) || price < 1) return "Inserisci il prezzo intero pagato dalla squadra avversaria.";
      if (!state.local.squadre.length) return "Aggiungi prima una squadra avversaria nella sezione Asta.";
      openOptionsSheet({
        title: "A quale squadra è stato assegnato?",
        selected: statusFor(picked.id).assignment?.squadra_id,
        options: state.local.squadre.map(({ id, nome }) => ({ value: id, label: nome })),
        onSelect: (teamId) => { persist(markTaken(state.local, picked.id, teamId, price)); renderCurrent(); },
      });
      return true;
    },
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
  tierButton.classList.toggle("is-active", Boolean(state.tier));
  tierButton.textContent = state.tier || "Fascia FVM";
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
  if (state.tier) parent.append(chip(state.tier, () => { state.tier = ""; renderPlayers(); }));
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
  const spent = state.auction.budget - remaining;
  const summary = element("section", "auction-summary");
  const summaryCopy = element("div", "auction-summary__copy");
  summaryCopy.append(element("span", "auction-summary__label", "Crediti residui"), element("span", "auction-summary__sub", `${spent} cr spesi · ${percent(spent, state.auction.budget)}% del budget`));
  summary.append(summaryCopy, element("strong", "auction-summary__value", `${remaining} / ${state.auction.budget}`));
  content.append(summary);
  if (state.backupMessage) {
    content.append(element("p", "backup-feedback", state.backupMessage));
    state.backupMessage = "";
  }
  if (state.persistenceWarning) content.append(element("p", "backup-warning", state.persistenceWarning));
  if (state.local.assegnazioni.length) {
    content.append(element("aside", "backup-callout", "Asta in corso: Safari può eliminare lo stato locale dopo un periodo di inattività. Salva ora un backup JSON qui sotto."));
  }
  if (state.local.assegnazioni.length && state.local.assegnazioni.length % 5 === 0) {
    content.append(element("aside", "backup-callout", `Hai registrato ${state.local.assegnazioni.length} assegnazioni: scarica un backup per non rischiare di perderle.`));
  }
  const hideTaken = element("label", "taken-toggle");
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox"; checkbox.checked = state.local.nascondi_gia_presi;
  checkbox.addEventListener("change", () => { persist(setHideTaken(state.local, checkbox.checked)); renderAuction(); });
  hideTaken.append(checkbox, document.createTextNode(" Nascondi già presi nella lista Giocatori")); content.append(hideTaken);
  const playersById = new Map(state.players.map((player) => [player.id, player]));
  const teamById = new Map(state.local.squadre.map((team) => [team.id, team]));
  const roster = element("section", "roster");
  roster.append(element("h2", "section-title", "La mia rosa"));
  ROLE_ORDER.forEach((role) => {
    const purchases = myPurchases(state.local).map((purchase) => ({ ...purchase, player: playersById.get(purchase.player_id) })).filter(({ player }) => player?.role === role);
    const slots = state.auction.squad_composition[role] || 0;
    const roleSpent = purchases.reduce((total, { prezzo_pagato }) => total + prezzo_pagato, 0);
    const section = element("section", "roster-role");
    section.append(element("h3", "roster-role__title", `${roleLabel(role)} · ${purchases.length}/${slots} · ${Math.max(0, slots - purchases.length)} liberi`), element("p", "roster-role__budget", `${roleSpent} cr · ${percent(roleSpent, state.auction.budget)}% del budget`));
    if (!purchases.length) section.append(element("p", "roster-role__empty", "Nessun giocatore acquistato."));
    purchases.forEach(({ player, prezzo_pagato }) => {
      const row = element("button", "roster-player"); row.type = "button";
      row.append(element("span", "roster-player__name", `${player.name} · ${player.team}`), element("strong", "roster-player__price", `${prezzo_pagato} cr`));
      row.addEventListener("click", () => openDetail(player)); section.append(row);
    });
    roster.append(section);
  });
  content.append(roster);
  const opponents = element("section", "opponents-section");
  opponents.append(element("h2", "section-title", "Acquisti avversari"));
  const opponentGroups = new Map();
  state.local.assegnazioni.filter(({ squadra_id }) => squadra_id !== MY_TEAM_ID).forEach((assignment) => {
    const groupId = assignment.squadra_id || "non-assegnata";
    if (!opponentGroups.has(groupId)) opponentGroups.set(groupId, []);
    opponentGroups.get(groupId).push({ ...assignment, player: playersById.get(assignment.player_id) });
  });
  if (!opponentGroups.size) opponents.append(element("p", "roster-role__empty", "Nessun acquisto avversario registrato."));
  opponentGroups.forEach((purchases, teamId) => {
    const name = teamId === "non-assegnata" ? "Squadra da definire" : teamById.get(teamId)?.nome || "Squadra non disponibile";
    const total = purchases.reduce((sum, { prezzo_pagato }) => sum + (prezzo_pagato || 0), 0);
    const group = element("section", "opponent-group");
    group.append(element("h3", "roster-role__title", name), element("p", "roster-role__budget", `${purchases.length} ${purchases.length === 1 ? "acquisto" : "acquisti"} · ${total ? `${total} cr` : "prezzo non disponibile"}`));
    purchases.filter(({ player }) => player).forEach(({ player, prezzo_pagato }) => {
      const row = element("button", "roster-player"); row.type = "button";
      row.append(element("span", "roster-player__name", `${player.name} · ${player.team}`), element("strong", "roster-player__price", prezzo_pagato ? `${prezzo_pagato} cr` : "—"));
      row.addEventListener("click", () => openDetail(player)); group.append(row);
    });
    opponents.append(group);
  });
  content.append(opponents);
  const teams = element("section", "teams-section");
  teams.append(element("h2", "section-title", "Squadre avversarie"), element("p", "backup-section__copy", `Configura fino a ${MAX_OPPONENT_TEAMS} squadre. Dopo il primo acquisto, il nome resta bloccato.`));
  const teamForm = element("form", "team-form");
  const teamName = document.createElement("input");
  teamName.className = "auction-price-input"; teamName.type = "text"; teamName.maxLength = 48; teamName.placeholder = "Nome squadra"; teamName.setAttribute("aria-label", "Nome nuova squadra avversaria");
  const addButton = element("button", "action-button", "Aggiungi"); addButton.type = "submit"; addButton.disabled = state.local.squadre.length >= MAX_OPPONENT_TEAMS;
  teamForm.append(teamName, addButton);
  const teamFeedback = element("p", "auction-action-error");
  teamForm.addEventListener("submit", (event) => {
    event.preventDefault();
    try { persist(addTeam(state.local, teamName.value)); renderAuction(); }
    catch (error) { teamFeedback.textContent = error.message || "Non riesco ad aggiungere la squadra."; }
  });
  teams.append(teamForm, teamFeedback);
  state.local.squadre.forEach((team) => {
    const assigned = state.local.assegnazioni.filter(({ squadra_id }) => squadra_id === team.id).length;
    const row = element("form", "team-row");
    const nameInput = document.createElement("input");
    nameInput.className = "auction-price-input"; nameInput.type = "text"; nameInput.maxLength = 48; nameInput.value = team.nome; nameInput.disabled = assigned > 0; nameInput.setAttribute("aria-label", `Nome ${team.nome}`);
    const saveButton = element("button", "action-button", "Salva"); saveButton.type = "submit"; saveButton.disabled = assigned > 0;
    const removeButton = element("button", "action-button action-button--quiet", "Elimina"); removeButton.type = "button"; removeButton.disabled = assigned > 0;
    row.append(nameInput, saveButton, removeButton, element("span", "team-row__status", assigned ? `${assigned} ${assigned === 1 ? "acquisto: nome bloccato" : "acquisti: nome bloccato"}` : "Nessun acquisto"));
    row.addEventListener("submit", (event) => { event.preventDefault(); try { persist(renameTeam(state.local, team.id, nameInput.value)); renderAuction(); } catch (error) { teamFeedback.textContent = error.message || "Non riesco a rinominare la squadra."; } });
    removeButton.addEventListener("click", () => { try { persist(deleteTeam(state.local, team.id)); renderAuction(); } catch (error) { teamFeedback.textContent = error.message || "Non riesco a eliminare la squadra."; } });
    teams.append(row);
  });
  content.append(teams);
  const backup = element("section", "backup-section");
  backup.append(element("h2", "section-title", "Backup"), element("p", "backup-section__copy", "Scarica un file di backup o genera un link di ripristino da salvare fuori dal telefono (nelle Note, in una chat con te stesso). Sopravvivono anche se i dati del sito vengono cancellati."));
  const fileActions = element("div", "backup-actions");
  const downloadButton = element("button", "action-button action-button--primary", "Scarica backup"); downloadButton.type = "button";
  const linkButton = element("button", "action-button", "Copia link di ripristino"); linkButton.type = "button";
  const fileFeedback = element("p", "backup-feedback");
  downloadButton.addEventListener("click", () => {
    try { downloadStateFile(state.local); fileFeedback.textContent = "Backup scaricato."; }
    catch (error) { fileFeedback.textContent = error.message || "Non riesco a scaricare il backup."; }
  });
  linkButton.addEventListener("click", () => {
    copyRestoreLink(state.local)
      .then(() => { fileFeedback.textContent = "Link di ripristino copiato negli appunti."; })
      .catch((error) => { fileFeedback.textContent = error.message || "Non riesco a generare o copiare il link di ripristino."; });
  });
  fileActions.append(downloadButton, linkButton);
  backup.append(fileActions, fileFeedback);
  backup.append(element("h3", "section-title", "Ripristino manuale"), element("p", "backup-section__copy", "In alternativa esporta lo stato in JSON o incolla un backup valido per ripristinarlo."));
  const textarea = document.createElement("textarea");
  textarea.className = "backup-textarea"; textarea.rows = 8; textarea.spellcheck = false; textarea.placeholder = "Il backup JSON comparirà qui…"; textarea.setAttribute("aria-label", "Backup JSON");
  const actions = element("div", "backup-actions");
  const exportButton = element("button", "action-button", "Esporta JSON"); exportButton.type = "button";
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
function openTierPicker() {
  openOptionsSheet({ title: "Fascia FVM", selected: state.tier, options: [{ value: "", label: "Tutte le fasce" }, ...tierOptions()], onSelect: (tier) => { state.tier = tier; renderPlayers(); } });
}
function openSortPicker() { openOptionsSheet({ title: "Ordina per", options: SORT_OPTIONS, selected: state.sort, onSelect: (sort) => { state.sort = sort; renderPlayers(); } }); }
function isEmptyLocalState(local) {
  return !local.preferiti.length && !local.squadre.length && !local.assegnazioni.length;
}
function downloadStateFile(local) {
  const json = serializeState(local);
  const blob = new Blob([json], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const link = document.createElement("a");
  link.href = url; link.download = `fanta-backup-${stamp}.json`;
  document.body.append(link); link.click(); link.remove();
  URL.revokeObjectURL(url);
}
function copyRestoreLink(local) {
  let encoded;
  try { encoded = encodeStateForLink(local); }
  catch (_) { return Promise.reject(new Error("Non riesco a generare il link di ripristino.")); }
  const link = `${location.origin}${location.pathname}#restore=${encoded}`;
  if (!navigator.clipboard?.writeText) return Promise.reject(new Error("La copia negli appunti non è disponibile su questo browser."));
  return navigator.clipboard.writeText(link);
}
function clearRestoreFragment() {
  history.replaceState(null, "", `${location.pathname}${location.search}#/${route()}`);
}
function attemptLinkRestore() {
  const prefix = "#restore=";
  if (!location.hash.startsWith(prefix)) return;
  const encoded = location.hash.slice(prefix.length);
  let restoredState;
  try {
    restoredState = decodeStateFromLink(encoded);
    validateAuctionState(restoredState);
  } catch (error) {
    clearRestoreFragment();
    state.backupMessage = "Il link di ripristino non è valido: " + (error.message || "");
    renderCurrent();
    return;
  }
  const confirmed = window.confirm("Ripristinare lo stato da questo link? I dati locali attuali verranno sovrascritti.");
  clearRestoreFragment();
  if (!confirmed) return;
  persist(restoredState);
  state.backupMessage = "Stato ripristinato dal link.";
  renderCurrent();
}
function attemptIndexedDbRecovery() {
  if (!isEmptyLocalState(state.local)) return;
  readFromIndexedDB().then((recovered) => {
    if (!recovered) return;
    let normalized;
    try { normalized = normalizeState(recovered); } catch (_) { return; }
    if (isEmptyLocalState(normalized)) return;
    persist(normalized);
    state.backupMessage = "Stato recuperato dalla copia locale.";
    renderCurrent();
  }).catch(() => {});
}
async function loadPlayers() {
  try {
    const response = await fetch("data/players.json");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    state.players = payload.players || []; state.generatedAt = payload.generated_at || ""; state.searchIndex = buildSearchIndex(state.players);
    state.auction = { ...DEFAULT_AUCTION, ...(payload.config?.auction || {}), squad_composition: { ...DEFAULT_AUCTION.squad_composition, ...(payload.config?.squad_composition || {}) } };
    mount(route());
    if (location.hash.startsWith("#restore=")) attemptLinkRestore(); else attemptIndexedDbRecovery();
  } catch (_) {
    view.replaceChildren(element("div", "empty-state", "Impossibile caricare i giocatori. Riprova quando sei connesso."));
  }
}
roleFilter.addEventListener("click", (event) => { const button = event.target.closest("button[data-role]"); if (button) { state.role = button.dataset.role; renderPlayers(); } });
searchInput.addEventListener("input", () => { state.query = searchInput.value; renderPlayers(); });
teamButton.addEventListener("click", openTeamPicker); tierButton.addEventListener("click", openTierPicker); sortButton.addEventListener("click", openSortPicker);
tabButtons.forEach((button) => button.addEventListener("click", () => navigate(button.dataset.route)));
window.addEventListener("hashchange", () => mount(route()));
if (!location.hash) location.hash = "/giocatori";
loadPlayers();
if ("serviceWorker" in navigator) window.addEventListener("load", () => navigator.serviceWorker.register("service-worker.js").catch(() => {}));
