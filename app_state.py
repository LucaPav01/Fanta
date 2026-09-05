"""Stato locale e persistente di preferiti e asta."""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


def empty_state() -> dict[str, Any]:
    return {"preferiti": [], "asta": {"miei": [], "presi": []}}


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


def normalize_state(value: Any) -> dict[str, Any]:
    """Valida e normalizza il formato usato da file, sessione e ripristino."""
    if not isinstance(value, dict) or not isinstance(value.get("asta"), dict):
        raise ValueError("Lo stato deve contenere preferiti e asta.")

    favorites = _unique_player_ids(value.get("preferiti"))
    taken = _unique_player_ids(value["asta"].get("presi"))
    bought_values = value["asta"].get("miei")
    if not isinstance(bought_values, list):
        raise ValueError("L'elenco asta.miei non è valido.")

    bought_by_id: dict[str, dict[str, Any]] = {}
    for item in bought_values:
        if not isinstance(item, dict):
            raise ValueError("Ogni acquisto deve contenere player_id e prezzo_pagato.")
        player_id = item.get("player_id")
        price = item.get("prezzo_pagato")
        if not isinstance(player_id, str) or not player_id.strip():
            raise ValueError("Ogni acquisto deve avere un player_id valido.")
        if isinstance(price, bool) or not isinstance(price, int) or price < 1:
            raise ValueError("Ogni prezzo_pagato deve essere un intero positivo.")
        clean_id = player_id.strip()
        bought_by_id[clean_id] = {"player_id": clean_id, "prezzo_pagato": price}

    bought_ids = set(bought_by_id)
    return {
        "preferiti": favorites,
        "asta": {
            "miei": list(bought_by_id.values()),
            "presi": [player_id for player_id in taken if player_id not in bought_ids],
        },
    }


def serialize_state(state: dict[str, Any]) -> str:
    return json.dumps(normalize_state(state), ensure_ascii=False, indent=2)


def parse_state(serialized: str) -> dict[str, Any]:
    try:
        value = json.loads(serialized)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON non valido: {exc.msg} (riga {exc.lineno}).") from exc
    return normalize_state(value)


def load_state(path: Path) -> tuple[dict[str, Any], str | None]:
    if not path.exists():
        return empty_state(), None
    try:
        return parse_state(path.read_text(encoding="utf-8")), None
    except (OSError, ValueError) as exc:
        return empty_state(), f"Stato locale non caricato: {exc}"


def save_state(path: Path, state: dict[str, Any]) -> None:
    """Scrive lo stato in modo atomico per non lasciare JSON parziali."""
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = serialize_state(state) + "\n"
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


def toggle_favorite(state: dict[str, Any], player_id: str) -> dict[str, Any]:
    normalized = normalize_state(state)
    favorites = normalized["preferiti"]
    if player_id in favorites:
        favorites.remove(player_id)
    else:
        favorites.append(player_id)
    return normalized


def mark_bought(state: dict[str, Any], player_id: str, price: int) -> dict[str, Any]:
    normalized = normalize_state(state)
    purchases = normalized["asta"]["miei"]
    purchases[:] = [item for item in purchases if item["player_id"] != player_id]
    purchases.append({"player_id": player_id, "prezzo_pagato": price})
    normalized["asta"]["presi"] = [value for value in normalized["asta"]["presi"] if value != player_id]
    return normalized


def mark_taken(state: dict[str, Any], player_id: str) -> dict[str, Any]:
    normalized = normalize_state(state)
    normalized["asta"]["miei"] = [
        item for item in normalized["asta"]["miei"] if item["player_id"] != player_id
    ]
    if player_id not in normalized["asta"]["presi"]:
        normalized["asta"]["presi"].append(player_id)
    return normalized


def cancel_auction_status(state: dict[str, Any], player_id: str) -> dict[str, Any]:
    normalized = normalize_state(state)
    normalized["asta"]["miei"] = [
        item for item in normalized["asta"]["miei"] if item["player_id"] != player_id
    ]
    normalized["asta"]["presi"] = [value for value in normalized["asta"]["presi"] if value != player_id]
    return normalized


def taken_player_ids(state: dict[str, Any]) -> set[str]:
    normalized = normalize_state(state)
    return {
        *normalized["asta"]["presi"],
        *(item["player_id"] for item in normalized["asta"]["miei"]),
    }
