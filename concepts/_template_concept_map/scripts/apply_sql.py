#!/usr/bin/env python3
"""
Apply a SQL patch file to the concept package DB using Python's sqlite3 module.

Usage:
  python scripts/apply_sql.py knowledge/patches/001_my_patch.sql
"""

from __future__ import annotations

import sys
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "knowledge" / "knowledge.db"


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python scripts/apply_sql.py <path-to-sql-file>")

    sql_path = Path(sys.argv[1]).resolve()
    sql = sql_path.read_text(encoding="utf-8")

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(sql)
        conn.commit()
    finally:
        conn.close()

    print("Applied:", sql_path)
    print("DB:", DB_PATH)


if __name__ == "__main__":
    main()

