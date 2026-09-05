#!/usr/bin/env python3
"""Pubblica il contratto dati per la PWA (web/data/players.json).

Legge fantacalcio_app.db in sola lettura e app_config.json, non tocca mai
il database: se il contratto app_players cambia, questo file va aggiornato
di conseguenza, mai il contrario.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app_data import connect_read_only, normalize_name

ROOT = Path(__file__).resolve().parent
DATABASE_PATH = ROOT / "fantacalcio_app.db"
CONFIG_PATH = ROOT / "app_config.json"
OUTPUT_PATH = ROOT / "web" / "data" / "players.json"

REQUIRED_FIELDS = ("player_id", "player_name", "team_name", "role")


def build_search_blob(row) -> str:
    parts = [row["player_name"], row["first_name"], row["last_name"], row["team_name"]]
    parts.extend((row["name_aliases"] or "").split(","))
    tokens = []
    for part in parts:
        normalized = normalize_name(part or "")
        if normalized:
            tokens.append(normalized)
    return " ".join(dict.fromkeys(tokens))


def build_player(row) -> dict:
    aliases = [alias.strip() for alias in (row["name_aliases"] or "").split(",") if alias.strip()]
    return {
        "id": row["player_id"],
        "name": row["player_name"],
        "first_name": row["first_name"],
        "last_name": row["last_name"],
        "team": row["team_name"],
        "role": row["role"],
        "fvm": row["fvm"],
        "fvm_parametrized": row["fvm_parametrized"],
        "fvm_budget": row["fvm_budget"],
        "fvm_percentile": row["fvm_percentile"],
        "fvm_tier": row["fvm_tier"],
        "price": row["average_auction_price"],
        "auction_teams": row["auction_teams"],
        "auction_budget": row["auction_budget"],
        "is_pct": row["is_pct"],
        "age": row["age"],
        "rating": row["rating"],
        "potential": row["potential"],
        "appearances": row["appearances"],
        "average_rating": row["average_rating"],
        "fantasy_average": row["fantasy_average"],
        "fvm_status": row["fvm_status"],
        "auction_price_status": row["auction_price_status"],
        "is_status": row["is_status"],
        "data_status": row["data_status"],
        "aliases": aliases,
        "sources": [source.strip() for source in (row["source_names"] or "").split(",") if source.strip()],
        "fvm_updated_at": row["fvm_updated_at"],
        "price_updated_at": row["auction_price_updated_at"],
        "is_updated_at": row["is_updated_at"],
        "search_blob": build_search_blob(row),
    }


def export_players_json(database_path: Path = DATABASE_PATH, config_path: Path = CONFIG_PATH,
                         target_path: Path = OUTPUT_PATH) -> int:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    with connect_read_only(database_path) as connection:
        rows = connection.execute(
            "SELECT player_id, player_name, first_name, last_name, name_aliases, team_name, role, "
            "fvm, fvm_parametrized, fvm_budget, fvm_percentile, fvm_tier, "
            "average_auction_price, auction_teams, auction_budget, "
            "is_pct, age, rating, potential, appearances, average_rating, fantasy_average, "
            "fvm_updated_at, auction_price_updated_at, is_updated_at, "
            "fvm_status, auction_price_status, is_status, data_status, source_names "
            "FROM app_players ORDER BY player_name COLLATE NOCASE"
        ).fetchall()

    for row in rows:
        for field in REQUIRED_FIELDS:
            if row[field] is None:
                raise ValueError(f"Campo obbligatorio nullo: {field} per player_id={row['player_id']}")

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "season": config["season"],
            "competition": config["competition"],
            "auction": config["auction"],
            "squad_composition": config["squad_composition"],
        },
        "players": [build_player(row) for row in rows],
    }

    target_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = target_path.with_suffix(target_path.suffix + ".tmp")
    temporary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary_path.replace(target_path)
    return len(rows)


def main() -> None:
    n_exported = export_players_json()
    print(f"{n_exported} giocatori esportati in {OUTPUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
