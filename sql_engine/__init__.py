"""
sql-engine: a small SQL query engine built from scratch.

Public API:
    from sql_engine import load_csv, parse, execute
"""

from .table import Table, load_csv
from .parser import parse
from .executor import execute, QueryResult, ExecutionError

__all__ = [
    "Table",
    "load_csv",
    "parse",
    "execute",
    "QueryResult",
    "ExecutionError",
]

__version__ = "0.1.0"
