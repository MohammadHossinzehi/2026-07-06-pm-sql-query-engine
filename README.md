# sql-engine

A SQL query engine built from scratch in Python: a hand-written lexer,
recursive-descent parser, and row-at-a-time execution engine that runs
real `SELECT` queries, including joins, `GROUP BY`, aggregates, and
`ORDER BY`, directly against CSV files. No SQLite, no pandas, no
parser-generator library. Just Python's standard library.

## Why this exists

Most "toy database" projects stop at a single-table filter. The
interesting part of a query engine is everything past that: resolving
`table.column` references across joined tables, deciding whether a
column reference in `GROUP BY` output is safe, and making `WHERE`,
aggregation, and sorting compose correctly. This project implements
that full pipeline end to end, closely mirroring how a real database's
logical query plan is structured:

```
FROM/JOIN  ->  WHERE  ->  GROUP BY (+ aggregates)  ->  SELECT projection  ->  ORDER BY  ->  LIMIT
```

## What it supports

- `SELECT col, col AS alias, *` projections
- `FROM table [AS] alias`
- `[INNER | LEFT] JOIN table [AS] alias ON <condition>`
- `WHERE` with `AND` / `OR` / `NOT`, comparisons (`= != <> < <= > >=`),
  `IN (...)`, and `IS [NOT] NULL`
- `GROUP BY col [, col ...]` with `COUNT(*)`, `COUNT([DISTINCT] col)`,
  `SUM`, `AVG`, `MIN`, `MAX`
- `ORDER BY col [ASC|DESC] [, col ...]`
- `LIMIT n`
- CSV loading with per-cell type inference (`int` / `float` / `str` /
  `NULL` for empty cells)

It deliberately does *not* support subqueries, `UNION`, `HAVING`, or
`INSERT`/`UPDATE`/`DELETE`; the goal was a correct, well-tested read
path, not a full RDBMS.

## How to run it

Requires Python 3.8+, no dependencies to install.

**Run the test suite:**

```bash
python -m unittest discover -s tests -v
# or, if pytest is installed:
python -m pytest tests/ -v
```

**Try it programmatically:**

```python
from sql_engine.table import load_csv
from sql_engine.parser import parse
from sql_engine.executor import execute

tables = {
    "employees": load_csv("sample_data/employees.csv"),
    "departments": load_csv("sample_data/departments.csv"),
}

sql = """
SELECT d.name AS dept, COUNT(*) AS headcount, AVG(e.salary) AS avg_salary
FROM employees e
JOIN departments d ON e.department_id = d.id
GROUP BY d.name
ORDER BY avg_salary DESC
"""

result = execute(parse(sql), tables)
print(result.to_table_string())
```

**Or use the interactive REPL:**

```bash
python -m sql_engine.cli sample_data/employees.csv sample_data/departments.csv
sql> SELECT name, salary FROM employees WHERE salary > 95000 ORDER BY salary DESC
sql> .tables
sql> .exit
```

Sample data (`sample_data/employees.csv`, `sample_data/departments.csv`)
is included so every example above runs immediately with no setup.

## Design decisions

- **Combined-row representation.** During `FROM`/`JOIN`/`WHERE`, a row
  is a dict keyed by table name or alias, e.g.
  `{"e": {"id": 1, ...}, "d": {"id": 10, ...}}`, rather than a flat
  dict. This is what makes `e.id` vs `d.id` unambiguous when two
  joined tables share a column name, and it's how the engine detects
  and rejects genuinely ambiguous unqualified references (see
  `test_ambiguous_column_raises`).
- **Nested-loop joins.** `JOIN` is implemented as a nested loop over
  the right table per left row, evaluating the `ON` condition per
  pair. It is the simplest correct join algorithm, the same fallback a
  real database uses when it can't use an index or hash join, chosen
  deliberately here to keep the executor's logic easy to verify, at
  the cost of O(n*m) instead of a hash join.
- **`LEFT JOIN` via a synthetic NULL row.** When no right-side row
  matches, a row of `NULL`s (one per right-table column) is spliced in
  so unmatched left rows still appear, matching standard SQL
  semantics.
- **Type inference at CSV load time, not query time.** Each CSV cell
  is coerced to `int`, `float`, or left as `str` (empty string ->
  `None`) once, when the table is loaded, so comparisons and
  aggregates downstream don't have to special-case strings that look
  like numbers.
- **Aggregates without `GROUP BY`.** A plain `SELECT COUNT(*) FROM t`
  is treated as a single implicit group over all rows, so the
  aggregation code path has one implementation instead of two.
- **Testing strategy.** 28 unit and integration tests cover each
  layer independently (lexer token streams, parser AST shape) plus
  the full pipeline against a realistic two-table dataset (joins,
  `LEFT JOIN` NULL-padding, `GROUP BY` + aggregates, `ORDER BY` +
  `LIMIT`, and error cases like unknown/ambiguous columns). Testing
  each layer in isolation made it possible to pin down exactly where
  a bug lived (lexer vs. parser vs. executor) while building this.

## Project structure

```
sql_engine/
  lexer.py      tokenizer: SQL text -> list of Tokens
  parser.py     recursive-descent parser: Tokens -> SelectStatement AST
  table.py      Table dataclass + CSV loader with type inference
  executor.py   AST + Tables -> QueryResult (the actual query engine)
  cli.py        interactive REPL
tests/
  test_sql_engine.py   28 tests across all four layers
sample_data/
  employees.csv, departments.csv
```
