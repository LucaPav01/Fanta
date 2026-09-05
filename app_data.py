"""Query e stato della lista giocatori, separati dalla UI Streamlit."""

from __future__ import annotations

import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode


SORT_OPTIONS = {
    "FVM (alto-basso)": "fvm",
    "Prezzo medio (alto-basso)": "price",
    "IS (alto-basso)": "is",
    "Nome (A-Z)": "name",
    "Squadra (A-Z)": "team",
}

SORT_SQL = {
    "fvm": "fvm IS NULL, fvm DESC, player_name COLLATE NOCASE",
    "price": "average_auction_price IS NULL, average_auction_price DESC, player_name COLLATE NOCASE",
    "is": "is_pct IS NULL, is_pct DESC, player_name COLLATE NOCASE",
    "name": "player_name COLLATE NOCASE",
    "team": "team_name COLLATE NOCASE, player_name COLLATE NOCASE",
}


@dataclass(frozen=True)
class FilterState:
    search: str = ""
    team: str = "Tutte"
    role: str = "Tutti"
    sort: str = "fvm"

    @classmethod
    def from_mapping(cls, values) -> "FilterState":
        sort = str(values.get("sort", "fvm"))
        return cls(
            search=str(values.get("search", "")).strip(),
            team=str(values.get("team", "Tutte")),
            role=str(values.get("role", "Tutti")),
            sort=sort if sort in SORT_SQL else "fvm",
        )

    def as_query_params(self, selected_player: str | None = None) -> dict[str, str]:
        params = {"sort": self.sort}
        if self.search:
            params["search"] = self.search
        if self.team != "Tutte":
            params["team"] = self.team
        if self.role != "Tutti":
            params["role"] = self.role
        if selected_player:
            params["player"] = selected_player
        return params


def connect_read_only(database_path: Path) -> sqlite3.Connection:
    """Apre il database senza consentire scritture accidentali dalla UI."""
    connection = sqlite3.connect(f"file:{database_path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.create_function("app_normalize", 1, lambda value: normalize_name(value or ""))
    return connection


def available_filters(connection: sqlite3.Connection) -> tuple[list[str], list[str]]:
    teams = [row[0] for row in connection.execute(
        "SELECT DISTINCT team_name FROM app_players WHERE team_name IS NOT NULL ORDER BY team_name COLLATE NOCASE"
    )]
    roles = [row[0] for row in connection.execute(
        "SELECT DISTINCT role FROM app_players WHERE role IS NOT NULL ORDER BY role"
    )]
    return teams, roles


def query_players(connection: sqlite3.Connection, filters: FilterState) -> list[sqlite3.Row]:
    connection.create_function("app_normalize", 1, lambda value: normalize_name(value or ""))
    clauses = []
    parameters: list[str] = []
    if filters.search:
        clauses.append("(app_normalize(player_name) LIKE ? OR app_normalize(name_aliases) LIKE ?)")
        term = f"%{normalize_name(filters.search)}%"
        parameters.extend((term, term))
    if filters.team != "Tutte":
        clauses.append("team_name = ?")
        parameters.append(filters.team)
    if filters.role != "Tutti":
        clauses.append("role = ?")
        parameters.append(filters.role)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    order = SORT_SQL.get(filters.sort, SORT_SQL["fvm"])
    return list(connection.execute(
        "SELECT player_id, player_name, name_aliases, team_name, role, fvm, "
        "average_auction_price, is_pct, fvm_status, auction_price_status, is_status "
        f"FROM app_players {where} ORDER BY {order}",
        parameters,
    ))


def get_player_detail(connection: sqlite3.Connection, player_id: str) -> sqlite3.Row | None:
    """Restituisce il contratto completo usato dalla scheda asta."""
    return connection.execute(
        "SELECT player_id, player_name, team_name, role, fvm, fvm_parametrized, fvm_budget, "
        "average_auction_price, auction_teams, auction_budget, is_pct, age, rating, potential, "
        "appearances, average_rating, fantasy_average, fvm_updated_at, auction_price_updated_at, "
        "is_updated_at, fvm_status, auction_price_status, is_status, data_status, source_names "
        "FROM app_players WHERE player_id = ?",
        (player_id,),
    ).fetchone()


def normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return " ".join("".join(c for c in normalized if not unicodedata.combining(c)).casefold().split())


def exact_search_result(rows: list[sqlite3.Row], search: str) -> str | None:
    """Restituisce l'id quando una ricerca identifica un unico giocatore.

    Il match esatto resta prioritario; una ricerca parziale apre comunque la
    scheda se l'elenco filtrato contiene una sola riga.
    """
    target = normalize_name(search)
    if not target:
        return None
    matches = []
    for row in rows:
        names = [row["player_name"], *(row["name_aliases"] or "").split(",")]
        if any(normalize_name(name) == target for name in names):
            matches.append(row["player_id"])
    unique_matches = list(dict.fromkeys(matches))
    if len(unique_matches) == 1:
        return unique_matches[0]
    return rows[0]["player_id"] if len(rows) == 1 else None


def player_url(filters: FilterState, player_id: str) -> str:
    return "?" + urlencode(filters.as_query_params(selected_player=player_id))
