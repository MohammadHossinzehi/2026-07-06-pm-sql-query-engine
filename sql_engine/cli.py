"""
A tiny REPL for running SQL SELECT statements against CSV files.

Usage:
    python -m sql_engine.cli sample_data/employees.csv sample_data/departments.csv

Each CSV is loaded as a table named after its filename (without the
extension), so employees.csv becomes table `employees`. Type SQL at the
prompt; type `.tables` to list loaded tables, or `.exit` / Ctrl-D to quit.
"""

import sys
from typing import Dict

from .executor import ExecutionError, execute
from .parser import ParseError, parse
from .table import Table, load_csv


def build_table_map(paths) -> Dict[str, Table]:
    tables: Dict[str, Table] = {}
    for path in paths:
        table = load_csv(path)
        tables[table.name] = table
    return tables


def run_query(sql: str, tables: Dict[str, Table]) -> str:
    stmt = parse(sql)
    result = execute(stmt, tables)
    return result.to_table_string()


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("Usage: python -m sql_engine.cli <csv-file> [<csv-file> ...]")
        return 1

    tables = build_table_map(argv)
    print(f"Loaded tables: {', '.join(sorted(tables))}")
    print("Enter a SQL SELECT statement, or .exit to quit.\n")

    while True:
        try:
            line = input("sql> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if not line:
            continue
        if line in (".exit", ".quit"):
            return 0
        if line == ".tables":
            for name, table in sorted(tables.items()):
                print(f"  {name} ({', '.join(table.columns)}) -- {len(table)} rows")
            continue

        try:
            print(run_query(line, tables))
        except (ParseError, ExecutionError) as exc:
            print(f"Error: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
