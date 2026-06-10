#!/usr/bin/env python3
"""
Initialize (or reset) the concept-map knowledge DB from migrations.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "knowledge" / "knowledge.db"
MIGRATIONS_PATH = ROOT / "knowledge" / "migrations"


def run_migration(conn: sqlite3.Connection, filename: str) -> None:
    sql = (MIGRATIONS_PATH / filename).read_text(encoding="utf-8")
    conn.executescript(sql)
    print("Applied:", filename)


def seed_map_and_root(conn: sqlite3.Connection) -> None:
    # Map/root insertion must avoid circular FK (map.root_node_id -> node.id, node.map_id -> map.id).
    conn.execute(
        """
        INSERT INTO map (id, name, description, root_node_id, max_depth, max_children)
        VALUES (?, ?, ?, NULL, 5, 4)
        """,
        ("default", "Concept Flow Map", "Self-contained conceptual flow map (sense-making)."),
    )
    conn.execute(
        """
        INSERT INTO node (id, map_id, label, short_def, eli5, tags)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "node_root",
            "default",
            "Root Concept",
            "Top-level concept map root.",
            "The main starting idea.",
            "root",
        ),
    )
    conn.execute("UPDATE map SET root_node_id = ? WHERE id = ?", ("node_root", "default"))


def main() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()
        print("Removed existing:", DB_PATH)

    os.makedirs(DB_PATH.parent, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        run_migration(conn, "001_init.sql")
        run_migration(conn, "002_triggers.sql")
        run_migration(conn, "003_seed_rel_types.sql")
        seed_map_and_root(conn)
        conn.commit()
    finally:
        conn.close()

    print("Database initialized:", DB_PATH)


if __name__ == "__main__":
    main()

