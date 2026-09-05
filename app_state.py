"""Stato locale e persistente di preferiti, squadre e asta."""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


SCHEMA_VERSION = 2
MY_TEAM_ID = "mia"
MAX_OPPONENT_TEAMS = 9


def empty_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "preferiti": [],
        "squadre": [],
        "assegnazioni": [],
        "nascondi_gia_presi": False,
    }


def _unique_player_ids(values: Any) -> list[str]:
    if not isinstance(values, list):
        raise ValueError("L'elenco dei giocatori non è valido.")
    result = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Ogni player_id deve essere una stringa non vuota.")
        player_id = value.strip()
        if player_id not in result:
            result.append(player_id)
    return result


def _migrate_v1(value: dict[str, Any]) -> dict[str, Any]:
    auction = value.get("asta")
    if not isinstance(auction, dict) or not isinstance(auction.get("miei"), list):
        raise ValueError("Lo stato v1 deve contenere un'asta valida.")
    mine = [
        {
            "player_id": item.get("player_id") if isinstance(item, dict) else None,
            "squadra_id": MY_TEAM_ID,
            "prezzo_pagato": item.get("prezzo_pagato") if isinstance(item, dict) else None,
        }
        for item in auction["miei"]
    ]
    mine_ids = {
        item["player_id"].strip()
        for item in mine
        if isinstance(item["player_id"], str)
    }
    others = [
        {"player_id": player_id, "squadra_id": None, "prezzo_pagato": None}
        for player_id in _unique_player_ids(auction.get("presi"))
        if player_id not in mine_ids
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "preferiti": value.get("preferiti"),
        "squadre": [],
        "assegnazioni": [*mine, *others],
        "nascondi_gia_presi": value.get("nascondi_gia_presi", False),
    }


def _migrate(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Lo stato deve essere un oggetto JSON.")
    version = value.get("schema_version", 1)
    if version == 1:
        return _migrate_v1(value)
    if version != SCHEMA_VERSION:
        raise ValueError("Questo backup usa una versione non supportata dell'app.")
    return value


def _normalize_teams(values: Any) -> list[dict[str, str]]:
    if not isinstance(values, list):
        raise ValueError("Le squadre avversarie devono essere un elenco.")
    if len(values) > MAX_OPPONENT_TEAMS:
        raise ValueError(f"Puoi configurare al massimo {MAX_OPPONENT_TEAMS} squadre avversarie.")
    teams: list[dict[str, str]] = []
    ids: set[str] = set()
    names: set[str] = set()
    for value in values:
        if not isinstance(value, dict):
            raise ValueError("Ogni squadra deve contenere id e nome.")
        team_id = value.get("id")
        name = value.get("nome")
        clean_id = team_id.strip() if isinstance(team_id, str) else ""
        clean_name = name.strip() if isinstance(name, str) else ""
        if not clean_id or clean_id == MY_TEAM_ID:
            raise ValueError("Ogni squadra avversaria deve avere un id valido.")
        if not clean_name:
            raise ValueError("Ogni squadra avversaria deve avere un nome.")
        if clean_id in ids:
            raise ValueError("Gli id delle squadre avversarie devono essere univoci.")
        if clean_name.casefold() in names:
            raise ValueError("I nomi delle squadre avversarie devono essere univoci.")
        ids.add(clean_id)
        names.add(clean_name.casefold())
        teams.append({"id": clean_id, "nome": clean_name})
    return teams


def _normalize_assignments(values: Any, teams: list[dict[str, str]]) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        raise ValueError("Le assegnazioni devono essere un elenco.")
    team_ids = {team["id"] for team in teams}
    by_player: dict[str, dict[str, Any]] = {}
    for value in values:
        if not isinstance(value, dict):
            raise ValueError("Ogni assegnazione deve indicare un giocatore e una squadra.")
        if "squadra_id" not in value or "prezzo_pagato" not in value:
            raise ValueError("Ogni assegnazione deve indicare squadra_id e prezzo_pagato.")
        player_id = value.get("player_id")
        clean_player_id = player_id.strip() if isinstance(player_id, str) else ""
        if not clean_player_id:
            raise ValueError("Ogni assegnazione deve indicare un giocatore valido.")
        team_id = value.get("squadra_id")
        if team_id is not None:
            team_id = team_id.strip() if isinstance(team_id, str) else ""
        if team_id is not None and team_id != MY_TEAM_ID and team_id not in team_ids:
            raise ValueError("Un'assegnazione fa riferimento a una squadra inesistente.")
        price = value.get("prezzo_pagato")
        if price is not None and (isinstance(price, bool) or not isinstance(price, int) or price < 1):
            raise ValueError("Ogni prezzo_pagato deve essere nullo oppure un intero positivo.")
        if team_id is not None and price is None:
            raise ValueError("Le assegnazioni a una squadra devono avere un prezzo_pagato.")
        by_player[clean_player_id] = {
            "player_id": clean_player_id,
            "squadra_id": team_id,
            "prezzo_pagato": price,
        }
    return list(by_player.values())


def normalize_state(value: Any) -> dict[str, Any]:
    """Migra, valida e normalizza il formato usato da file, sessione e ripristino."""
    state = _migrate(value)
    teams = _normalize_teams(state.get("squadre"))
    return {
        "schema_version": SCHEMA_VERSION,
        "preferiti": _unique_player_ids(state.get("preferiti")),
        "squadre": teams,
        "assegnazioni": _normalize_assignments(state.get("assegnazioni"), teams),
        "nascondi_gia_presi": bool(state.get("nascondi_gia_presi", False)),
    }


def serialize_state(state: dict[str, Any]) -> str:
    return json.dumps(normalize_state(state), ensure_ascii=False, indent=2)


def parse_state(serialized: str) -> dict[str, Any]:
    try:
        value = json.loads(serialized)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON non valido: {exc.msg} (riga {exc.lineno}).") from exc
    return normalize_state(value)


def _atomic_write(path: Path, serialized: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as temporary:
            temporary.write(serialized)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def load_state(path: Path) -> tuple[dict[str, Any], str | None]:
    if not path.exists():
        return empty_state(), None
    try:
        serialized = path.read_text(encoding="utf-8")
        raw = json.loads(serialized)
        is_v1 = isinstance(raw, dict) and raw.get("schema_version", 1) == 1
        state = normalize_state(raw)
        if is_v1:
            try:
                backup_path = path.with_name(f"{path.name}.v1.bak")
                if not backup_path.exists():
                    _atomic_write(backup_path, serialized)
                _atomic_write(path, serialize_state(state) + "\n")
            except OSError as exc:
                return state, f"Stato v1 recuperato, ma la migrazione locale non è stata salvata: {exc}"
        return state, None
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return empty_state(), f"Stato locale non caricato: {exc}"


def save_state(path: Path, state: dict[str, Any]) -> None:
    """Scrive lo stato in modo atomico per non lasciare JSON parziali."""
    _atomic_write(path, serialize_state(state) + "\n")


def toggle_favorite(state: dict[str, Any], player_id: str) -> dict[str, Any]:
    normalized = normalize_state(state)
    favorites = normalized["preferiti"]
    if player_id in favorites:
        favorites.remove(player_id)
    else:
        favorites.append(player_id)
    return normalized


def _next_team_id(teams: list[dict[str, str]]) -> str:
    index = 1
    ids = {team["id"] for team in teams}
    while f"avversaria-{index}" in ids:
        index += 1
    return f"avversaria-{index}"


def add_team(state: dict[str, Any], name: str) -> dict[str, Any]:
    normalized = normalize_state(state)
    if len(normalized["squadre"]) >= MAX_OPPONENT_TEAMS:
        raise ValueError(f"Puoi configurare al massimo {MAX_OPPONENT_TEAMS} squadre avversarie.")
    normalized["squadre"].append({"id": _next_team_id(normalized["squadre"]), "nome": name.strip() if isinstance(name, str) else ""})
    return normalize_state(normalized)


def rename_team(state: dict[str, Any], team_id: str, name: str) -> dict[str, Any]:
    normalized = normalize_state(state)
    team = next((team for team in normalized["squadre"] if team["id"] == team_id), None)
    if team is None:
        raise ValueError("Squadra avversaria non trovata.")
    if any(item["squadra_id"] == team_id for item in normalized["assegnazioni"]):
        raise ValueError("Non puoi modificare una squadra che ha già dei giocatori assegnati.")
    team["nome"] = name.strip() if isinstance(name, str) else ""
    return normalize_state(normalized)


def delete_team(state: dict[str, Any], team_id: str) -> dict[str, Any]:
    normalized = normalize_state(state)
    if not any(team["id"] == team_id for team in normalized["squadre"]):
        raise ValueError("Squadra avversaria non trovata.")
    if any(item["squadra_id"] == team_id for item in normalized["assegnazioni"]):
        raise ValueError("Non puoi eliminare una squadra che ha già dei giocatori assegnati.")
    normalized["squadre"] = [team for team in normalized["squadre"] if team["id"] != team_id]
    return normalized


def assign_player(
    state: dict[str, Any], player_id: str, team_id: str | None, price: int | None = None
) -> dict[str, Any]:
    normalized = normalize_state(state)
    clean_player_id = player_id.strip() if isinstance(player_id, str) else ""
    normalized["assegnazioni"] = [
        item for item in normalized["assegnazioni"] if item["player_id"] != clean_player_id
    ]
    normalized["assegnazioni"].append(
        {"player_id": clean_player_id, "squadra_id": team_id, "prezzo_pagato": price}
    )
    return normalize_state(normalized)


def mark_bought(state: dict[str, Any], player_id: str, price: int) -> dict[str, Any]:
    return assign_player(state, player_id, MY_TEAM_ID, price)


def mark_taken(
    state: dict[str, Any], player_id: str, team_id: str | None = None, price: int | None = None
) -> dict[str, Any]:
    return assign_player(state, player_id, team_id, price)


def cancel_auction_status(state: dict[str, Any], player_id: str) -> dict[str, Any]:
    normalized = normalize_state(state)
    normalized["assegnazioni"] = [
        item for item in normalized["assegnazioni"] if item["player_id"] != player_id
    ]
    return normalized


def assignment_for(state: dict[str, Any], player_id: str) -> dict[str, Any] | None:
    return next(
        (item for item in normalize_state(state)["assegnazioni"] if item["player_id"] == player_id),
        None,
    )


def my_purchases(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"player_id": item["player_id"], "prezzo_pagato": item["prezzo_pagato"]}
        for item in normalize_state(state)["assegnazioni"]
        if item["squadra_id"] == MY_TEAM_ID
    ]


def taken_player_ids(state: dict[str, Any]) -> set[str]:
    return {item["player_id"] for item in normalize_state(state)["assegnazioni"]}
