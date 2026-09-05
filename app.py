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


ROOT = Path(__file__).resolve().parent
PUBLISHED_DATABASE_PATH = ROOT / "fantacalcio_app.db"
DEVELOPMENT_DATABASE_PATH = ROOT / "fantacalcio.db"
DATABASE_PATH = PUBLISHED_DATABASE_PATH if PUBLISHED_DATABASE_PATH.exists() else DEVELOPMENT_DATABASE_PATH
CONFIG = json.loads((ROOT / "app_config.json").read_text(encoding="utf-8"))
ROLE_LABELS = {"GK": "Portieri", "DEF": "Difensori", "MID": "Centrocampisti", "FWD": "Attaccanti"}
PAGE_SIZE = 30


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


def render_card(row, filters: FilterState, selected: bool = False) -> None:
    role = ROLE_LABELS.get(row["role"], row["role"] or "Ruolo da definire")
    fvm, fvm_help = metric_value(row["fvm"], row["fvm_status"])
    price, price_help = metric_value(row["average_auction_price"], row["auction_price_status"])
    is_value, is_help = metric_value(row["is_pct"], row["is_status"], "%")

    with st.container(border=True):
        title, action = st.columns([4, 1])
        with title:
            st.markdown(f"### {escape(row['player_name'])}")
            st.caption(f"{escape(row['team_name'])} · {escape(role)}")
        with action:
            if selected:
                st.markdown("<span class='selected-pill'>Selezionato</span>", unsafe_allow_html=True)
            else:
                st.link_button("Apri", player_url(filters, row["player_id"]), use_container_width=True)

        columns = st.columns(3)
        for column, label, value, help_text in zip(
            columns,
            ("FVM", "Prezzo medio", "IS"),
            (fvm, price, is_value),
            (fvm_help, price_help, is_help),
        ):
            column.metric(label, value, help=help_text)


def render_player_detail(row) -> None:
    """Scheda completa per decidere durante l'asta, ordinata per priorità."""
    role = ROLE_LABELS.get(row["role"], row["role"] or "Ruolo da definire")
    st.markdown(f"## {escape(row['player_name'])}")
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
    @media (max-width: 480px) {
      .block-container {padding-left: .75rem; padding-right: .75rem;}
      [data-testid="stHorizontalBlock"] {gap: .45rem;}
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("Asta Fanta")
st.caption(
    f"{CONFIG['competition']} · {CONFIG['auction']['teams']} squadre · "
    f"{CONFIG['auction']['budget']} crediti"
)

if not DATABASE_PATH.exists():
    st.error("Database dell’app non trovato. Esegui prima `python3 importa_database.py`.")
    st.stop()

with connect_read_only(DATABASE_PATH) as connection:
    teams, roles = available_filters(connection)

    initial = FilterState.from_mapping(st.query_params)
    selected_player = st.query_params.get("player")

    search = st.text_input("Cerca giocatore", value=initial.search, placeholder="Nome o cognome")
    team_col, role_col = st.columns(2)
    team_options = ["Tutte", *teams]
    role_options = ["Tutti", *roles]
    with team_col:
        team = st.selectbox(
            "Squadra", team_options,
            index=team_options.index(initial.team) if initial.team in team_options else 0,
        )
    with role_col:
        role = st.selectbox(
            "Ruolo", role_options,
            format_func=lambda value: ROLE_LABELS.get(value, value),
            index=role_options.index(initial.role) if initial.role in role_options else 0,
        )

    sort_labels = list(SORT_OPTIONS)
    current_sort_label = next((label for label, value in SORT_OPTIONS.items() if value == initial.sort), sort_labels[0])
    sort_label = st.selectbox("Ordina per", sort_labels, index=sort_labels.index(current_sort_label))
    filters = FilterState(search=search, team=team, role=role, sort=SORT_OPTIONS[sort_label])
    rows = query_players(connection, filters)

    filter_signature = (filters.search, filters.team, filters.role, filters.sort)
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
            render_card(row, filters)
        if visible_count < len(rows):
            remaining = len(rows) - visible_count
            if st.button(f"Mostra altri ({remaining})", use_container_width=True):
                st.session_state.visible_players = visible_count + PAGE_SIZE
                st.rerun()
