#!/usr/bin/env python3
"""
Run lint queries for the concept package knowledge DB.

This is a sqlite3-CLI-free equivalent of:
  sqlite3 -header -column knowledge/knowledge.db < knowledge/migrations/004_lint_queries.sql
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "knowledge" / "knowledge.db"
LINT_SQL_PATH = ROOT / "knowledge" / "migrations" / "004_lint_queries.sql"


def main() -> None:
    sql = LINT_SQL_PATH.read_text(encoding="utf-8")
    statements = [s.strip() for s in sql.split(";") if s.strip()]

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.row_factory = sqlite3.Row
        failures: list[str] = []

        for stmt in statements:
            rows = conn.execute(stmt).fetchall()
            if rows:
                failures.append(stmt)
                print("\n[FAIL] Lint query returned rows:\n")
                print(stmt + ";\n")
                for row in rows:
                    print(dict(row))

        if failures:
            raise SystemExit(f"\nLint failed: {len(failures)} query set(s) returned rows.")

        print("[OK] Lint passed (all query result sets empty).")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

