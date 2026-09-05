const ROLE_LABELS = { GK: "P", DEF: "D", MID: "C", FWD: "A" };

function element(tagName, className, text) {
  const node = document.createElement(tagName);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function value(value, suffix = "") {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  return `${Number.isFinite(number) ? new Intl.NumberFormat("it-IT", { maximumFractionDigits: 2 }).format(number) : value}${suffix}`;
}

function date(value) {
  if (!value) return "non disponibile";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString("it-IT", { dateStyle: "medium", timeStyle: "short" });
}

export function roleLabel(role) {
  return ROLE_LABELS[role] || role || "—";
}

function tierClass(tier) {
  const match = /^Fascia ([1-5])$/.exec(tier || "");
  return match ? `badge--tier-${match[1]}` : "badge--tier-missing";
}

function tierBadge(tier) {
  return element("span", `badge badge--tier ${tierClass(tier)}`, tier || "Fascia n.d.");
}

export function createPlayerCard(player, { onOpen, onToggleFavorite, isFavorite = false, auctionStatus = "" }) {
  const card = element("article", "player-card");
  if (auctionStatus) card.classList.add("player-card--taken");
  const openButton = element("button", "player-card__open");
  openButton.type = "button";
  openButton.setAttribute("aria-label", `Apri la scheda di ${player.name}`);
  openButton.addEventListener("click", () => onOpen(player));

  const main = element("span", "player-card__main");
  main.append(element("span", "player-card__name", player.name));
  main.append(element("span", "player-card__meta", `${player.team} · ${roleLabel(player.role)}`));

  const values = element("span", "player-card__values");
  [["FVM", value(player.fvm)], ["Prezzo", value(player.price)]].forEach(([label, metric]) => {
    const item = element("span", "player-card__metric");
    item.append(element("span", "player-card__metric-label", label));
    item.append(element("span", "player-card__metric-value", metric));
    values.append(item);
  });

  const side = element("span", "player-card__side");
  side.append(tierBadge(player.fvm_tier));
  const isBadge = element("span", `badge badge--${player.is_status === "available" ? "ok" : player.is_status === "verify" ? "warn" : "missing"}`, `IS ${value(player.is_pct, "%")}`);
  side.append(isBadge);
  if (player.data_status !== "available") {
    const dot = element("span", "status-dot");
    dot.title = "Dati da verificare o incompleti";
    dot.setAttribute("aria-label", "Dati da verificare o incompleti");
    side.append(dot);
  }
  openButton.append(main, values, side);
  card.append(openButton);
  if (onToggleFavorite) {
    const favorite = element("button", `favorite-button${isFavorite ? " is-active" : ""}`, isFavorite ? "★" : "☆");
    favorite.type = "button";
    favorite.setAttribute("aria-label", isFavorite ? `Rimuovi ${player.name} dai preferiti` : `Aggiungi ${player.name} ai preferiti`);
    favorite.setAttribute("aria-pressed", String(isFavorite));
    favorite.addEventListener("click", () => {
      const active = onToggleFavorite(player);
      favorite.classList.toggle("is-active", active);
      favorite.textContent = active ? "★" : "☆";
      favorite.setAttribute("aria-pressed", String(active));
      favorite.setAttribute("aria-label", active ? `Rimuovi ${player.name} dai preferiti` : `Aggiungi ${player.name} ai preferiti`);
    });
    card.append(favorite);
  }
  return card;
}

export function openSheet({ title, content, subtitle = "" }) {
  const backdrop = element("div", "sheet-backdrop");
  const sheet = element("section", "sheet");
  sheet.setAttribute("role", "dialog");
  sheet.setAttribute("aria-modal", "true");
  sheet.setAttribute("aria-label", title);
  const close = () => backdrop.remove();
  const closeButton = element("button", "sheet__close", "×");
  closeButton.type = "button";
  closeButton.setAttribute("aria-label", "Chiudi");
  closeButton.addEventListener("click", close);
  backdrop.addEventListener("click", (event) => { if (event.target === backdrop) close(); });
  sheet.append(element("div", "sheet__handle"));
  const header = element("div", "sheet__header");
  const heading = document.createElement("div");
  heading.append(element("h2", "sheet__title", title));
  if (subtitle) heading.append(element("p", "detail-subtitle", subtitle));
  header.append(heading, closeButton);
  sheet.append(header, content);
  backdrop.append(sheet);
  document.body.append(backdrop);
  closeButton.focus();
  return close;
}

export function openOptionsSheet({ title, options, selected, onSelect }) {
  const list = element("div");
  let close;
  options.forEach(({ value: optionValue, label }) => {
    const option = element("button", "sheet__option", label);
    option.type = "button";
    option.classList.toggle("is-selected", optionValue === selected);
    option.addEventListener("click", () => { onSelect(optionValue); close(); });
    list.append(option);
  });
  close = openSheet({ title, content: list });
}

export function openPlayerDetail(player, actions = {}) {
  const content = element("div");
  let close;
  if (actions.onToggleFavorite || actions.onMarkBought || actions.onMarkTaken || actions.onCancelAuction) {
    const controls = element("section", "detail-actions");
    if (actions.onToggleFavorite) {
      const favorite = element("button", `action-button action-button--favorite${actions.isFavorite ? " is-active" : ""}`, actions.isFavorite ? "★ Nei preferiti" : "☆ Aggiungi ai preferiti");
      favorite.type = "button";
      favorite.addEventListener("click", () => {
        const active = actions.onToggleFavorite(player);
        favorite.classList.toggle("is-active", active);
        favorite.textContent = active ? "★ Nei preferiti" : "☆ Aggiungi ai preferiti";
      });
      controls.append(favorite);
    }
    if (actions.onMarkBought || actions.onMarkTaken || actions.onCancelAuction) {
      const priceLabel = element("label", "auction-price-label", "Prezzo pagato");
      const price = document.createElement("input");
      price.className = "auction-price-input";
      price.type = "number";
      price.min = "1";
      price.step = "1";
      price.inputMode = "numeric";
      price.placeholder = "Crediti";
      const currentPurchase = actions.auctionStatus?.assignment;
      if (currentPurchase) price.value = currentPurchase.prezzo_pagato;
      priceLabel.append(price);
      const error = element("p", "auction-action-error");
      const auctionActions = element("div", "auction-actions");
      const addAction = (label, className, callback) => {
        const button = element("button", `action-button ${className}`, label);
        button.type = "button";
        button.addEventListener("click", () => {
          const result = callback();
          if (typeof result === "string") { error.textContent = result; return; }
          close();
        });
        auctionActions.append(button);
      };
      if (actions.onMarkBought) addAction("Preso da me", "action-button--primary", () => actions.onMarkBought(player, Number(price.value)));
      if (actions.onMarkTaken) addAction("Preso da altri", "", () => actions.onMarkTaken(player, Number(price.value)));
      if (actions.onCancelAuction) addAction("Annulla stato asta", "action-button--quiet", () => actions.onCancelAuction(player));
      controls.append(priceLabel, auctionActions, error);
    }
    content.append(controls);
  }
  const metrics = element("div", "detail-grid");
  [
    ["FVM", value(player.fvm)],
    ["Fascia FVM", player.fvm_tier || "non disponibile"],
    ["Percentile nel ruolo", value(player.fvm_percentile, "%")],
    [`FVM su ${player.fvm_budget || "—"} cr`, value(player.fvm_parametrized)],
    ["Prezzo medio", value(player.price)],
    ["Formato prezzo", `${player.auction_teams || "—"} squadre · ${player.auction_budget || "—"} cr`],
    ["IS", value(player.is_pct, "%")],
    ["Presenze", value(player.appearances)],
    ["Media voto", value(player.average_rating)],
    ["Fantamedia", value(player.fantasy_average)],
    ["Età", value(player.age)],
    ["Rating", value(player.rating)],
    ["Potential", value(player.potential)],
  ].forEach(([label, metric]) => {
    const item = element("div", "detail-metric");
    item.append(element("span", "detail-metric__label", label), element("span", "detail-metric__value", metric));
    metrics.append(item);
  });
  content.append(metrics);

  const sourceSection = element("section", "detail-section");
  sourceSection.append(element("h3", "", "Fonti"), element("p", "", player.sources?.length ? player.sources.join(", ") : "Nessuna fonte disponibile"));
  content.append(sourceSection);

  const updates = element("section", "detail-section");
  updates.append(element("h3", "", "Ultimo aggiornamento"));
  const grid = element("div", "detail-updates");
  [["FVM", player.fvm_updated_at], ["Prezzo medio", player.price_updated_at], ["IS", player.is_updated_at]].forEach(([label, updated]) => {
    const row = element("div");
    row.append(element("span", "detail-updates__label", label), element("span", "", date(updated)));
    grid.append(row);
  });
  updates.append(grid);
  content.append(updates);
  close = openSheet({ title: player.name, subtitle: `${player.team} · ${roleLabel(player.role)}`, content });
}
