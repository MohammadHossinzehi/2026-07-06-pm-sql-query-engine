"""
In-memory table representation plus a naive type-inferring CSV loader.

Real databases store typed columns; this toy engine keeps things simple
by inferring a type per *cell* (int, float, or str, with empty string
treated as NULL/None) when a CSV is loaded.
"""

import csv
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class Table:
    name: str
    columns: List[str]
    rows: List[Dict[str, Any]]

    def __len__(self) -> int:
        return len(self.rows)

    def __repr__(self) -> str:
        return f"Table({self.name!r}, columns={self.columns}, rows={len(self.rows)})"


def _infer(value: str):
    if value == "":
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def load_csv(path: str, table_name: Optional[str] = None) -> Table:
    """Load a CSV file into a Table, inferring int/float/str per cell."""
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        columns = reader.fieldnames or []
        rows = [{col: _infer(row[col]) for col in columns} for row in reader]
    if table_name is None:
        table_name = os.path.splitext(os.path.basename(path))[0]
    return Table(name=table_name, columns=list(columns), rows=rows)


def table_from_rows(name: str, columns: List[str], rows: List[Dict[str, Any]]) -> Table:
    """Build a Table directly from Python data (mainly useful for tests)."""
    return Table(name=name, columns=list(columns), rows=[dict(r) for r in rows])
