"""
Query execution engine.

Turns a parsed SelectStatement plus a dict of in-memory Tables into a
QueryResult. The pipeline mirrors a real database's logical query plan:

    FROM/JOIN -> WHERE -> GROUP BY -> SELECT (projection/aggregation)
    -> ORDER BY -> LIMIT

A "combined row" during FROM/JOIN/WHERE is a dict keyed by table name
(or alias) mapping to that table's column dict, e.g.:

    {"e": {"id": 1, "name": "Ada"}, "d": {"id": 10, "name": "R&D"}}

This keeps `table.column` references unambiguous even when two joined
tables share a column name (both can have "id").
"""

from typing import Any, Dict, List, Optional

from .parser import (
    AggregateCall, BinaryOp, ColumnRef, InList, IsNull, JoinClause,
    Literal, SelectItem, SelectStatement, UnaryNot,
)
from .table import Table

CombinedRow = Dict[str, Dict[str, Any]]


class ExecutionError(Exception):
    pass


# ------------------------------------------------------------- helpers --

def _table_key(table: Table, alias: Optional[str]) -> str:
    return alias or table.name


def _resolve_column(row: CombinedRow, ref: ColumnRef) -> Any:
    if ref.table is not None:
        if ref.table not in row:
            raise ExecutionError(f"Unknown table or alias {ref.table!r}")
        table_row = row[ref.table]
        if ref.name not in table_row:
            raise ExecutionError(f"Unknown column {ref.table}.{ref.name}")
        return table_row[ref.name]

    matches = [t for t, cols in row.items() if ref.name in cols]
    if not matches:
        raise ExecutionError(f"Unknown column {ref.name!r}")
    if len(matches) > 1:
        raise ExecutionError(
            f"Ambiguous column {ref.name!r}; qualify it as "
            f"{'/'.join(f'{m}.{ref.name}' for m in matches)}"
        )
    return row[matches[0]][ref.name]


def _truthy(value: Any) -> bool:
    return bool(value)


def _compare(op: str, left: Any, right: Any) -> bool:
    if left is None or right is None:
        return False
    if op == "=":
        return left == right
    if op in ("!=", "<>"):
        return left != right
    if op == "<":
        return left < right
    if op == "<=":
        return left <= right
    if op == ">":
        return left > right
    if op == ">=":
        return left >= right
    raise ExecutionError(f"Unknown operator {op!r}")


def _eval_expr(row: CombinedRow, node: Any) -> Any:
    if isinstance(node, Literal):
        return node.value
    if isinstance(node, ColumnRef):
        return _resolve_column(row, node)
    if isinstance(node, UnaryNot):
        return not _truthy(_eval_expr(row, node.expr))
    if isinstance(node, IsNull):
        is_null = _eval_expr(row, node.expr) is None
        return (not is_null) if node.negated else is_null
    if isinstance(node, InList):
        value = _eval_expr(row, node.expr)
        options = [_eval_expr(row, v) for v in node.values]
        return value in options
    if isinstance(node, BinaryOp):
        if node.op == "AND":
            return _truthy(_eval_expr(row, node.left)) and _truthy(_eval_expr(row, node.right))
        if node.op == "OR":
            return _truthy(_eval_expr(row, node.left)) or _truthy(_eval_expr(row, node.right))
        return _compare(node.op, _eval_expr(row, node.left), _eval_expr(row, node.right))
    raise ExecutionError(f"Cannot evaluate AST node {node!r}")


def _base_rows(table: Table, key: str) -> List[CombinedRow]:
    return [{key: dict(row)} for row in table.rows]


def _apply_join(left_rows: List[CombinedRow], right_table: Table,
                 join: JoinClause) -> List[CombinedRow]:
    key = _table_key(right_table, join.alias)
    result: List[CombinedRow] = []
    for lrow in left_rows:
        matched = False
        for rrow_data in right_table.rows:
            candidate = dict(lrow)
            candidate[key] = dict(rrow_data)
            if _truthy(_eval_expr(candidate, join.on)):
                result.append(candidate)
                matched = True
        if not matched and join.kind == "LEFT":
            candidate = dict(lrow)
            candidate[key] = {col: None for col in right_table.columns}
            result.append(candidate)
    return result


def _group_key(row: CombinedRow, group_by: List[ColumnRef]) -> tuple:
    return tuple(_resolve_column(row, ref) for ref in group_by)


def _eval_aggregate(agg: AggregateCall, rows: List[CombinedRow]) -> Any:
    if agg.func == "COUNT":
        if agg.arg is None:
            return len(rows)
        values = [_resolve_column(r, agg.arg) for r in rows]
        values = [v for v in values if v is not None]
        if agg.distinct:
            values = list(set(values))
        return len(values)

    values = [_resolve_column(r, agg.arg) for r in rows]
    values = [v for v in values if v is not None]
    if agg.distinct:
        values = list(set(values))

    if agg.func == "SUM":
        return sum(values) if values else 0
    if agg.func == "AVG":
        return sum(values) / len(values) if values else None
    if agg.func == "MIN":
        return min(values) if values else None
    if agg.func == "MAX":
        return max(values) if values else None
    raise ExecutionError(f"Unknown aggregate function {agg.func}")


def _column_label(item: SelectItem, index: int) -> str:
    if item.alias:
        return item.alias
    expr = item.expr
    if isinstance(expr, ColumnRef):
        return expr.name
    if isinstance(expr, AggregateCall):
        arg = "*" if expr.arg is None else str(expr.arg)
        return f"{expr.func}({arg})"
    return f"col{index}"


def _flatten(row: CombinedRow) -> Dict[str, Any]:
    flat: Dict[str, Any] = {}
    for cols in row.values():
        flat.update(cols)
    return flat


def _sort_rows(rows: List[Dict[str, Any]], stmt: SelectStatement) -> List[Dict[str, Any]]:
    if not stmt.order_by:
        return rows
    # Stable-sort from the last ORDER BY key to the first so the first
    # key ends up as the primary sort criterion.
    for order_item in reversed(stmt.order_by):
        label = order_item.expr.name
        rows.sort(
            key=lambda r: (r.get(label) is None, r.get(label)),
            reverse=order_item.descending,
        )
    return rows


class QueryResult:
    def __init__(self, columns: List[str], rows: List[Dict[str, Any]]):
        self.columns = columns
        self.rows = rows

    def __repr__(self) -> str:
        return f"QueryResult(columns={self.columns}, rows={len(self.rows)})"

    def __eq__(self, other) -> bool:
        return (
            isinstance(other, QueryResult)
            and self.columns == other.columns
            and self.rows == other.rows
        )

    def to_table_string(self) -> str:
        if not self.columns:
            return "(no columns)"
        widths = [len(c) for c in self.columns]
        str_rows = []
        for row in self.rows:
            str_row = ["" if row[c] is None else str(row[c]) for c in self.columns]
            str_rows.append(str_row)
            for i, cell in enumerate(str_row):
                widths[i] = max(widths[i], len(cell))
        lines = [" | ".join(c.ljust(widths[i]) for i, c in enumerate(self.columns))]
        lines.append("-+-".join("-" * w for w in widths))
        for str_row in str_rows:
            lines.append(" | ".join(cell.ljust(widths[i]) for i, cell in enumerate(str_row)))
        return "\n".join(lines)


def execute(stmt: SelectStatement, tables: Dict[str, Table]) -> QueryResult:
    if stmt.table not in tables:
        raise ExecutionError(f"Unknown table {stmt.table!r}")
    base_table = tables[stmt.table]
    base_key = _table_key(base_table, stmt.table_alias)
    rows = _base_rows(base_table, base_key)

    for join in stmt.joins:
        if join.table not in tables:
            raise ExecutionError(f"Unknown table {join.table!r}")
        rows = _apply_join(rows, tables[join.table], join)

    if stmt.where is not None:
        rows = [r for r in rows if _truthy(_eval_expr(r, stmt.where))]

    if stmt.is_star:
        flat_rows = [_flatten(r) for r in rows]
        if flat_rows:
            columns = list(flat_rows[0].keys())
        else:
            columns = list(base_table.columns)
            for join in stmt.joins:
                columns += tables[join.table].columns
        result_rows = [{c: fr.get(c) for c in columns} for fr in flat_rows]
        return QueryResult(columns=columns, rows=_sort_rows(result_rows, stmt)[: stmt.limit])

    has_aggregates = any(isinstance(item.expr, AggregateCall) for item in stmt.columns)
    columns = [_column_label(item, i) for i, item in enumerate(stmt.columns)]

    if stmt.group_by or has_aggregates:
        buckets: Dict[tuple, List[CombinedRow]] = {}
        bucket_order: List[tuple] = []
        if stmt.group_by:
            for r in rows:
                key = _group_key(r, stmt.group_by)
                if key not in buckets:
                    buckets[key] = []
                    bucket_order.append(key)
                buckets[key].append(r)
        else:
            # A single implicit group over all rows (plain aggregate query).
            buckets[()] = rows
            bucket_order.append(())

        result_rows = []
        for key in bucket_order:
            bucket = buckets[key]
            representative = bucket[0] if bucket else {}
            out = {}
            for item, label in zip(stmt.columns, columns):
                if isinstance(item.expr, AggregateCall):
                    out[label] = _eval_aggregate(item.expr, bucket)
                else:
                    out[label] = _resolve_column(representative, item.expr) if bucket else None
            result_rows.append(out)
        result_rows = _sort_rows(result_rows, stmt)
        return QueryResult(columns=columns, rows=result_rows[: stmt.limit])

    result_rows = [
        {label: _eval_expr(r, item.expr) for item, label in zip(stmt.columns, columns)}
        for r in rows
    ]
    result_rows = _sort_rows(result_rows, stmt)
    return QueryResult(columns=columns, rows=result_rows[: stmt.limit])
