"""
End-to-end and unit tests for the from-scratch SQL engine.

Run with:  python -m pytest tests/ -v
(or python -m unittest discover -s tests -v if pytest isn't installed)
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sql_engine.executor import ExecutionError, execute
from sql_engine.lexer import LexError, tokenize
from sql_engine.parser import ParseError, parse
from sql_engine.table import load_csv, table_from_rows

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sample_data")


def load_tables():
    employees = load_csv(os.path.join(DATA_DIR, "employees.csv"))
    departments = load_csv(os.path.join(DATA_DIR, "departments.csv"))
    return {"employees": employees, "departments": departments}


class LexerTests(unittest.TestCase):
    def test_basic_tokens(self):
        tokens = tokenize("SELECT * FROM t WHERE a = 1")
        kinds = [t.type.name for t in tokens]
        self.assertEqual(
            kinds,
            ["KEYWORD", "STAR", "KEYWORD", "IDENT", "KEYWORD", "IDENT", "OP", "NUMBER", "EOF"],
        )

    def test_string_literal_with_escape(self):
        tokens = tokenize(r"SELECT 'it\'s fine'")
        self.assertEqual(tokens[1].value, "it's fine")

    def test_multi_char_operators(self):
        tokens = tokenize("a <= b AND c <> d")
        ops = [t.value for t in tokens if t.type.name == "OP"]
        self.assertEqual(ops, ["<=", "<>"])

    def test_unterminated_string_raises(self):
        with self.assertRaises(LexError):
            tokenize("SELECT 'oops")

    def test_line_comment_is_ignored(self):
        tokens = tokenize("SELECT 1 -- trailing comment\n")
        self.assertEqual([t.type.name for t in tokens], ["KEYWORD", "NUMBER", "EOF"])


class ParserTests(unittest.TestCase):
    def test_simple_select_star(self):
        stmt = parse("SELECT * FROM employees")
        self.assertTrue(stmt.is_star)
        self.assertEqual(stmt.table, "employees")

    def test_select_with_alias_and_where(self):
        stmt = parse("SELECT name AS n FROM employees WHERE salary > 90000")
        self.assertEqual(stmt.columns[0].alias, "n")
        self.assertIsNotNone(stmt.where)

    def test_join_parses(self):
        stmt = parse(
            "SELECT e.name, d.name FROM employees e "
            "JOIN departments d ON e.department_id = d.id"
        )
        self.assertEqual(len(stmt.joins), 1)
        self.assertEqual(stmt.joins[0].kind, "INNER")
        self.assertEqual(stmt.table_alias, "e")

    def test_left_join_parses(self):
        stmt = parse(
            "SELECT d.name, e.name FROM departments d "
            "LEFT JOIN employees e ON d.id = e.department_id"
        )
        self.assertEqual(stmt.joins[0].kind, "LEFT")

    def test_group_by_and_aggregate(self):
        stmt = parse(
            "SELECT department_id, COUNT(*) AS n, AVG(salary) AS avg_salary "
            "FROM employees GROUP BY department_id ORDER BY n DESC"
        )
        self.assertEqual(len(stmt.group_by), 1)
        self.assertEqual(stmt.order_by[0].descending, True)

    def test_in_and_is_null(self):
        stmt = parse("SELECT * FROM employees WHERE department_id IN (10, 20) AND manager_id IS NULL")
        self.assertIsNotNone(stmt.where)

    def test_invalid_sql_raises(self):
        with self.assertRaises(ParseError):
            parse("SELECT FROM employees")

    def test_star_with_group_by_rejected(self):
        with self.assertRaises(ParseError):
            parse("SELECT * FROM employees GROUP BY department_id")


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.tables = load_tables()

    def test_select_star_row_count(self):
        result = execute(parse("SELECT * FROM employees"), self.tables)
        self.assertEqual(len(result.rows), 8)
        self.assertIn("name", result.columns)

    def test_where_filters_rows(self):
        result = execute(
            parse("SELECT name FROM employees WHERE salary >= 97000"), self.tables
        )
        names = {r["name"] for r in result.rows}
        self.assertEqual(names, {"Alan Turing", "Barbara Liskov", "Margaret Hamilton", "Radia Perlman"})

    def test_where_with_and_or(self):
        result = execute(
            parse(
                "SELECT name FROM employees "
                "WHERE department_id = 10 AND salary > 96000 OR department_id = 30"
            ),
            self.tables,
        )
        names = {r["name"] for r in result.rows}
        self.assertEqual(names, {"Alan Turing", "Barbara Liskov", "Margaret Hamilton", "Radia Perlman"})

    def test_is_null(self):
        result = execute(
            parse("SELECT name FROM employees WHERE department_id IS NULL"), self.tables
        )
        self.assertEqual([r["name"] for r in result.rows], ["Edsger Dijkstra"])

    def test_in_list(self):
        result = execute(
            parse("SELECT name FROM employees WHERE department_id IN (20, 30)"), self.tables
        )
        names = {r["name"] for r in result.rows}
        self.assertEqual(
            names,
            {"Grace Hopper", "Katherine Johnson", "Margaret Hamilton", "Radia Perlman"},
        )

    def test_inner_join(self):
        result = execute(
            parse(
                "SELECT e.name, d.name AS dept FROM employees e "
                "JOIN departments d ON e.department_id = d.id "
                "WHERE d.name = 'Engineering'"
            ),
            self.tables,
        )
        self.assertEqual(len(result.rows), 3)
        self.assertTrue(all(r["dept"] == "Engineering" for r in result.rows))

    def test_left_join_includes_unmatched(self):
        result = execute(
            parse(
                "SELECT d.name AS dept, e.name AS emp FROM departments d "
                "LEFT JOIN employees e ON d.id = e.department_id"
            ),
            self.tables,
        )
        marketing_rows = [r for r in result.rows if r["dept"] == "Marketing"]
        self.assertEqual(len(marketing_rows), 1)
        self.assertIsNone(marketing_rows[0]["emp"])

    def test_group_by_count_and_avg(self):
        result = execute(
            parse(
                "SELECT department_id, COUNT(*) AS n, AVG(salary) AS avg_salary "
                "FROM employees WHERE department_id IS NOT NULL "
                "GROUP BY department_id ORDER BY department_id"
            ),
            self.tables,
        )
        rows_by_dept = {r["department_id"]: r for r in result.rows}
        self.assertEqual(rows_by_dept[10]["n"], 3)
        self.assertAlmostEqual(rows_by_dept[10]["avg_salary"], (95000 + 98000 + 97000) / 3)
        self.assertEqual(rows_by_dept[20]["n"], 2)
        self.assertEqual(rows_by_dept[30]["n"], 2)

    def test_plain_aggregate_without_group_by(self):
        result = execute(
            parse("SELECT COUNT(*) AS total, MAX(salary) AS top FROM employees"), self.tables
        )
        self.assertEqual(result.rows[0]["total"], 8)
        self.assertEqual(result.rows[0]["top"], 102000)

    def test_order_by_desc_and_limit(self):
        result = execute(
            parse("SELECT name, salary FROM employees ORDER BY salary DESC LIMIT 3"), self.tables
        )
        salaries = [r["salary"] for r in result.rows]
        self.assertEqual(salaries, sorted(salaries, reverse=True))
        self.assertEqual(len(result.rows), 3)

    def test_unknown_column_raises(self):
        with self.assertRaises(ExecutionError):
            execute(parse("SELECT nope FROM employees"), self.tables)

    def test_ambiguous_column_raises(self):
        with self.assertRaises(ExecutionError):
            execute(
                parse(
                    "SELECT name FROM employees e "
                    "JOIN departments d ON e.department_id = d.id"
                ),
                self.tables,
            )

    def test_distinct_count(self):
        result = execute(
            parse("SELECT COUNT(DISTINCT department_id) AS n FROM employees"), self.tables
        )
        # 10, 20, 30 -> 3 distinct non-null department ids
        self.assertEqual(result.rows[0]["n"], 3)


class TableTests(unittest.TestCase):
    def test_table_from_rows_roundtrip(self):
        table = table_from_rows("t", ["a", "b"], [{"a": 1, "b": 2}, {"a": 3, "b": 4}])
        result = execute(parse("SELECT a, b FROM t WHERE a > 1"), {"t": table})
        self.assertEqual(result.rows, [{"a": 3, "b": 4}])

    def test_csv_type_inference(self):
        table = load_csv(os.path.join(DATA_DIR, "employees.csv"))
        row = table.rows[0]
        self.assertIsInstance(row["id"], int)
        self.assertIsInstance(row["salary"], int)
        self.assertIsInstance(row["name"], str)


if __name__ == "__main__":
    unittest.main()
