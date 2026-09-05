"""Interfaccia mobile per esplorare i giocatori durante l'asta."""

from __future__ import annotations

from html import escape
import json
from pathlib import Path
from urllib.parse import urlencode

import streamlit as st

from app_data import (
    SORT_OPTIONS,
    FilterState,
    available_filters,
    connect_read_only,
    exact_search_result,
    get_player_detail,
    player_url,
    query_players,
)
from app_state import (
    cancel_auction_status,
    load_state,
    mark_bought,
    mark_taken,
    normalize_state,
    parse_state,
    save_state,
    serialize_state,
    taken_player_ids,
    toggle_favorite,
)


ROOT = Path(__file__).resolve().parent
PUBLISHED_DATABASE_PATH = ROOT / "fantacalcio_app.db"
DEVELOPMENT_DATABASE_PATH = ROOT / "fantacalcio.db"
DATABASE_PATH = PUBLISHED_DATABASE_PATH if PUBLISHED_DATABASE_PATH.exists() else DEVELOPMENT_DATABASE_PATH
CONFIG = json.loads((ROOT / "app_config.json").read_text(encoding="utf-8"))
ROLE_LABELS = {"GK": "Portieri", "DEF": "Difensori", "MID": "Centrocampisti", "FWD": "Attaccanti"}
ROLE_FILTER_LABELS = {"GK": "P", "DEF": "D", "MID": "C", "FWD": "A"}
PAGE_SIZE = 15
STATE_PATH = ROOT / ".fanta_state.json"
STATE_KEY = "fanta_state"


def initialize_state() -> None:
    if STATE_KEY in st.session_state:
        return
    state, warning = load_state(STATE_PATH)
    st.session_state[STATE_KEY] = state
    st.session_state["state_backup"] = serialize_state(state)
    if warning:
        st.session_state["state_warning"] = warning


def replace_state(state, *, sync_backup: bool = True) -> bool:
    """Salva prima su disco e pubblica poi lo stesso stato in sessione."""
    normalized = normalize_state(state)
    try:
        save_state(STATE_PATH, normalized)
    except OSError as exc:
        st.error(f"Modifica non salvata: {exc}")
        return False
    st.session_state[STATE_KEY] = normalized
    if sync_backup:
        st.session_state["state_backup"] = serialize_state(normalized)
    return True


def set_flash(message: str) -> None:
    st.session_state["state_flash"] = message


def favorite_button(player_id: str, player_name: str, key_prefix: str) -> None:
    favorites = st.session_state[STATE_KEY]["preferiti"]
    is_favorite = player_id in favorites
    label = "★" if is_favorite else "☆"
    help_text = "Rimuovi dai preferiti" if is_favorite else "Aggiungi ai preferiti"
    if st.button(label, key=f"{key_prefix}_favorite_{player_id}", help=help_text):
        if replace_state(toggle_favorite(st.session_state[STATE_KEY], player_id)):
            action = "rimosso dai" if is_favorite else "aggiunto ai"
            set_flash(f"{player_name} {action} preferiti.")
            st.rerun()


def metric_value(value, status: str, suffix: str = "") -> tuple[str, str]:
    if value is None or status == "missing":
        return "—", "Dato non disponibile"
    rendered = f"{value:.1f}".replace(".0", "") + suffix
    if status == "verify":
        return rendered, "Da verificare"
    return rendered, "Disponibile"


def display_value(value, suffix: str = "", decimals: int = 1) -> str:
    if value is None:
        return "—"
    if isinstance(value, (int, float)):
        rendered = f"{value:.{decimals}f}".rstrip("0").rstrip(".")
    else:
        rendered = str(value)
    return rendered + suffix


def status_label(status: str) -> str:
    return {
        "available": "Disponibile",
        "missing": "Non disponibile",
        "verify": "Da verificare",
    }.get(status, "Da verificare")


def render_value_grid(items: list[tuple[str, str, str | None]]) -> None:
    """Mostra valori secondari in una griglia leggibile anche su telefono."""
    for start in range(0, len(items), 2):
        columns = st.columns(2)
        for column, (label, value, help_text) in zip(columns, items[start:start + 2]):
            column.metric(label, value, help=help_text)


def sync_query_params(filters: FilterState, selected_player: str | None) -> None:
    desired = filters.as_query_params(selected_player)
    if dict(st.query_params) != desired:
        st.query_params.clear()
        st.query_params.update(desired)


def render_card(row, filters: FilterState, key_prefix: str, selected: bool = False) -> None:
    role = ROLE_LABELS.get(row["role"], row["role"] or "Ruolo da definire")
    fvm, fvm_help = metric_value(row["fvm"], row["fvm_status"])
    price, price_help = metric_value(row["average_auction_price"], row["auction_price_status"])
    is_value, is_help = metric_value(row["is_pct"], row["is_status"], "%")

    with st.container(border=True):
        title, favorite = st.columns([5, 1])
        title.markdown(f"### {escape(row['player_name'])}")
        with favorite:
            favorite_button(row["player_id"], row["player_name"], key_prefix)
        st.caption(f"{escape(row['team_name'])} · {escape(role)}")

        columns = st.columns(2)
        for column, label, value, help_text in zip(
            columns,
            ("FVM", "Prezzo medio"),
            (fvm, price),
            (fvm_help, price_help),
        ):
            column.metric(label, value, help=help_text)

        st.markdown(
            f"<span class='secondary-pill' title='{escape(is_help)}'>IS {escape(is_value)} · {escape(is_help)}</span>",
            unsafe_allow_html=True,
        )
        if selected:
            st.markdown("<span class='selected-pill'>Selezionato</span>", unsafe_allow_html=True)
        else:
            st.link_button(
                f"Apri scheda di {row['player_name']}",
                player_url(filters, row["player_id"]),
                use_container_width=True,
            )


def render_player_detail(row) -> None:
    """Scheda completa per decidere durante l'asta, ordinata per priorità."""
    role = ROLE_LABELS.get(row["role"], row["role"] or "Ruolo da definire")
    title, favorite = st.columns([5, 1])
    title.markdown(f"## {escape(row['player_name'])}")
    with favorite:
        favorite_button(row["player_id"], row["player_name"], "detail")
    st.caption(f"{escape(row['team_name'])} · {escape(role)}")

    summary = st.columns(3)
    summary[0].metric(
        f"FVM {row['fvm_budget'] or CONFIG['auction']['budget']}",
        display_value(row["fvm_parametrized"]),
        help="FVM riparametrato sul budget della lega",
    )
    summary[1].metric("Prezzo medio", display_value(row["average_auction_price"]))
    summary[2].metric("IS", display_value(row["is_pct"], "%"))

    st.markdown("### Valutazione economica")
    render_value_grid([
        ("FVM", display_value(row["fvm"]), "FVM ufficiale su base 1000"),
        (
            f"FVM su {row['fvm_budget'] or CONFIG['auction']['budget']}",
            display_value(row["fvm_parametrized"]),
            "FVM riparametrato sul budget della lega",
        ),
        (
            "Prezzo medio",
            display_value(row["average_auction_price"]),
            f"Media per {row['auction_teams'] or CONFIG['auction']['teams']} squadre e "
            f"{row['auction_budget'] or CONFIG['auction']['budget']} crediti",
        ),
    ])

    st.markdown("### Rendimento")
    render_value_grid([
        ("Presenze", display_value(row["appearances"], decimals=0), None),
        ("Media voto", display_value(row["average_rating"]), None),
        ("Fantamedia", display_value(row["fantasy_average"]), None),
        ("IS", display_value(row["is_pct"], "%"), "Indicatore sintetico percentuale"),
    ])

    st.markdown("### Profilo")
    render_value_grid([
        ("Età", display_value(row["age"], " anni", decimals=0), None),
        ("Rating", display_value(row["rating"]), None),
        ("Potenziale", display_value(row["potential"]), None),
        ("Ruolo", role, None),
    ])

    st.markdown("### Affidabilità")
    reliability = [
        ("FVM", row["fvm_status"], row["fvm_updated_at"]),
        ("Prezzo medio", row["auction_price_status"], row["auction_price_updated_at"]),
        ("IS", row["is_status"], row["is_updated_at"]),
    ]
    for label, status, updated_at in reliability:
        update = f" · aggiornato {updated_at}" if updated_at else ""
        st.markdown(f"**{label}:** {status_label(status)}{escape(update)}")

    sources = [source.strip() for source in (row["source_names"] or "").split(",") if source.strip()]
    st.caption("Fonti collegate: " + (", ".join(sources) if sources else "nessuna fonte disponibile"))
    if row["data_status"] == "verify":
        st.warning("Alcuni dati dipendono da un abbinamento da verificare.")
    elif row["data_status"] == "missing":
        st.info("La scheda contiene dati mancanti, mostrati con —.")

    st.markdown("### Stato asta")
    purchase = next(
        (
            item
            for item in st.session_state[STATE_KEY]["asta"]["miei"]
            if item["player_id"] == row["player_id"]
        ),
        None,
    )
    is_taken = row["player_id"] in st.session_state[STATE_KEY]["asta"]["presi"]
    if purchase:
        st.success(f"Preso da me per {purchase['prezzo_pagato']} crediti")
    elif is_taken:
        st.info("Preso da altri")
    else:
        st.caption("Giocatore disponibile")

    price = st.number_input(
        "Prezzo pagato",
        min_value=1,
        step=1,
        value=purchase["prezzo_pagato"] if purchase else 1,
        key=f"auction_price_{row['player_id']}",
    )
    if st.button("Preso da me", use_container_width=True, key=f"mine_{row['player_id']}"):
        if replace_state(mark_bought(st.session_state[STATE_KEY], row["player_id"], int(price))):
            set_flash(f"{row['player_name']} aggiunto alla tua rosa.")
            st.rerun()
    if st.button("Preso da altri", use_container_width=True, key=f"taken_{row['player_id']}"):
        if replace_state(mark_taken(st.session_state[STATE_KEY], row["player_id"])):
            set_flash(f"{row['player_name']} segnato come preso da altri.")
            st.rerun()
    if st.button(
        "Annulla",
        use_container_width=True,
        disabled=not purchase and not is_taken,
        key=f"cancel_{row['player_id']}",
    ):
        if replace_state(cancel_auction_status(st.session_state[STATE_KEY], row["player_id"])):
            set_flash(f"Stato asta annullato per {row['player_name']}.")
            st.rerun()


def render_auction(rows) -> None:
    state = st.session_state[STATE_KEY]
    purchases = state["asta"]["miei"]
    total_spent = sum(item["prezzo_pagato"] for item in purchases)
    budget = CONFIG["auction"]["budget"]
    st.metric("Crediti residui", f"{budget - total_spent} / {budget}")

    players_by_id = {row["player_id"]: row for row in rows}
    purchases_by_id = {item["player_id"]: item for item in purchases}
    st.markdown("### La mia rosa")
    for role, maximum in CONFIG["squad_composition"].items():
        role_players = [
            players_by_id[player_id]
            for player_id in purchases_by_id
            if player_id in players_by_id and players_by_id[player_id]["role"] == role
        ]
        st.markdown(f"**{ROLE_LABELS.get(role, role)} · {len(role_players)}/{maximum} slot**")
        if role_players:
            for player in role_players:
                price = purchases_by_id[player["player_id"]]["prezzo_pagato"]
                st.write(f"{player['player_name']} · {player['team_name']} — {price} crediti")
        else:
            st.caption("Nessun acquisto")

    missing_players = [item for item in purchases if item["player_id"] not in players_by_id]
    if missing_players:
        st.warning(f"{len(missing_players)} acquisti salvati non sono presenti nel database attuale.")

    st.markdown("### Copia o ripristina lo stato")
    st.caption("Conserva questo JSON come copia di sicurezza. Incollane uno valido e premi Ripristina.")
    serialized = st.text_area("Stato JSON", height=260, key="state_backup")
    if st.button("Ripristina stato", use_container_width=True):
        try:
            restored = parse_state(serialized)
        except ValueError as exc:
            st.error(str(exc))
        else:
            if replace_state(restored, sync_backup=False):
                set_flash("Stato ripristinato dal JSON.")
                st.rerun()


st.set_page_config(page_title="Fanta · Asta", page_icon="⚽", layout="centered")
st.markdown(
    """
    <style>
    .block-container {max-width: 760px; padding: 1rem 1rem 4rem;}
    h1 {font-size: 1.75rem !important; margin-bottom: 0 !important;}
    h3 {font-size: 1.05rem !important; margin: 0 !important;}
    [data-testid="stMetric"] {background: #f6f7f9; border-radius: .7rem; padding: .65rem;}
    [data-testid="stMetricLabel"] {font-size: .75rem;}
    [data-testid="stMetricValue"] {font-size: 1.35rem;}
    .selected-pill {display: inline-block; padding: .3rem .5rem; border-radius: 1rem;
      color: #0b6b3a; background: #dff5e8; font-size: .72rem; font-weight: 700;}
    .secondary-pill {display: inline-block; margin: .65rem 0; padding: .3rem .55rem;
      border-radius: 1rem; color: #475467; background: #eef1f4; font-size: .75rem;
      font-weight: 650;}
    @media (max-width: 480px) {
      .block-container {padding-left: .75rem; padding-right: .75rem;}
      [data-testid="stHorizontalBlock"] {gap: .45rem;}
    }
    </style>
    """,
    unsafe_allow_html=True,
)

initialize_state()
st.title("Asta Fanta")
st.caption(
    f"{CONFIG['competition']} · {CONFIG['auction']['teams']} squadre · "
    f"{CONFIG['auction']['budget']} crediti"
)

section = st.radio(
    "Sezione",
    ("Giocatori", "Preferiti", "Asta"),
    horizontal=True,
    label_visibility="collapsed",
    key="main_section",
)

if warning := st.session_state.pop("state_warning", None):
    st.warning(warning)
if flash := st.session_state.pop("state_flash", None):
    st.success(flash)

if not DATABASE_PATH.exists():
    st.error("Database dell’app non trovato. Esegui prima `python3 importa_database.py`.")
    st.stop()

with connect_read_only(DATABASE_PATH) as connection:
    teams, roles = available_filters(connection)
    all_rows = query_players(connection, FilterState())

    if section == "Preferiti":
        st.subheader("Preferiti")
        favorite_ids = set(st.session_state[STATE_KEY]["preferiti"])
        favorite_rows = [row for row in all_rows if row["player_id"] in favorite_ids]
        favorite_selected = st.query_params.get("player")
        selected_row = (
            get_player_detail(connection, favorite_selected)
            if favorite_selected in favorite_ids
            else None
        )
        if selected_row is not None:
            st.link_button("← Torna ai preferiti", "?sort=fvm")
            render_player_detail(selected_row)
        elif not favorite_rows:
            st.info("Non hai ancora aggiunto giocatori ai preferiti.")
        else:
            favorite_filters = FilterState()
            for row in favorite_rows:
                render_card(row, favorite_filters, "favorites")
        st.stop()

    if section == "Asta":
        st.subheader("Asta")
        render_auction(all_rows)
        st.stop()

    initial = FilterState.from_mapping(st.query_params)
    selected_player = st.query_params.get("player")

    search = st.text_input("Cerca giocatore", value=initial.search, placeholder="Nome o cognome")
    role_options = ["Tutti", *roles]
    role = st.radio(
        "Ruolo",
        role_options,
        format_func=lambda value: ROLE_FILTER_LABELS.get(value, value),
        index=role_options.index(initial.role) if initial.role in role_options else 0,
        horizontal=True,
    )

    team_options = ["Tutte", *teams]
    sort_labels = list(SORT_OPTIONS)
    current_sort_label = next((label for label, value in SORT_OPTIONS.items() if value == initial.sort), sort_labels[0])
    with st.expander("Squadra e ordinamento", expanded=False):
        team = st.selectbox(
            "Squadra", team_options,
            index=team_options.index(initial.team) if initial.team in team_options else 0,
        )
        sort_label = st.selectbox("Ordina per", sort_labels, index=sort_labels.index(current_sort_label))
    filters = FilterState(search=search, team=team, role=role, sort=SORT_OPTIONS[sort_label])
    rows = query_players(connection, filters)
    hide_taken = st.toggle("Nascondi i giocatori già presi", value=False)
    if hide_taken:
        unavailable_ids = taken_player_ids(st.session_state[STATE_KEY])
        rows = [row for row in rows if row["player_id"] not in unavailable_ids]

    filter_signature = (filters.search, filters.team, filters.role, filters.sort, hide_taken)
    if st.session_state.get("filter_signature") != filter_signature:
        st.session_state.filter_signature = filter_signature
        st.session_state.visible_players = PAGE_SIZE

    exact_player = exact_search_result(rows, search)
    if exact_player:
        selected_player = exact_player
    if selected_player and not any(row["player_id"] == selected_player for row in rows):
        selected_player = None
    sync_query_params(filters, selected_player)

    if selected_player:
        selected_row = get_player_detail(connection, selected_player)
        list_filters = FilterState(team=filters.team, role=filters.role, sort=filters.sort)
        st.link_button("← Torna alla lista", "?" + urlencode(list_filters.as_query_params()))
        render_player_detail(selected_row)
    else:
        st.markdown(f"**{len(rows)} giocatori**")
        if not rows:
            st.info("Nessun giocatore corrisponde ai filtri scelti.")
        visible_count = st.session_state.get("visible_players", PAGE_SIZE)
        for row in rows[:visible_count]:
            render_card(row, filters, "players")
        if visible_count < len(rows):
            remaining = len(rows) - visible_count
            if st.button(f"Mostra altri ({remaining})", use_container_width=True):
                st.session_state.visible_players = visible_count + PAGE_SIZE
                st.rerun()
