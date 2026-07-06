"""
Recursive-descent parser for the minimal SQL dialect.

Grammar (informal, uppercase = literal keyword):

    select_stmt := SELECT select_list FROM table_ref join*
                   [WHERE expr]
                   [GROUP BY column_ref (',' column_ref)*]
                   [ORDER BY order_item (',' order_item)*]
                   [LIMIT NUMBER]

    select_list := '*' | select_item (',' select_item)*
    select_item := (column_ref | aggregate) [[AS] IDENT]
    aggregate   := (COUNT|SUM|AVG|MIN|MAX) '(' (['*'] | [DISTINCT] column_ref) ')'

    table_ref   := IDENT [[AS] IDENT]
    join        := [INNER|LEFT] JOIN table_ref ON expr

    expr        := or_expr
    or_expr     := and_expr (OR and_expr)*
    and_expr    := not_expr (AND not_expr)*
    not_expr    := [NOT] comparison
    comparison  := '(' expr ')'
                 | operand (OP operand | IS [NOT] NULL | IN '(' operand (',' operand)* ')')?
    operand     := column_ref | NUMBER | STRING
    column_ref  := IDENT ['.' IDENT]

This engine intentionally supports a single FROM table plus zero or more
JOINs (no subqueries, no UNION) -- enough to demonstrate a real parser and
executor without ballooning into a full RDBMS.
"""

from dataclasses import dataclass, field
from typing import Any, List, Optional

from .lexer import Token, TokenType, tokenize


class ParseError(Exception):
    pass


# ---------------------------------------------------------------- AST --

@dataclass
class ColumnRef:
    table: Optional[str]
    name: str

    def __str__(self):
        return f"{self.table}.{self.name}" if self.table else self.name


@dataclass
class Literal:
    value: Any


@dataclass
class AggregateCall:
    func: str                    # COUNT, SUM, AVG, MIN, MAX
    arg: Optional[ColumnRef]     # None means '*' (only valid for COUNT)
    distinct: bool = False


@dataclass
class BinaryOp:
    op: str
    left: Any
    right: Any


@dataclass
class UnaryNot:
    expr: Any


@dataclass
class IsNull:
    expr: Any
    negated: bool


@dataclass
class InList:
    expr: Any
    values: List[Any]


@dataclass
class SelectItem:
    expr: Any
    alias: Optional[str] = None


@dataclass
class JoinClause:
    table: str
    alias: Optional[str]
    kind: str                    # 'INNER' or 'LEFT'
    on: Any


@dataclass
class OrderItem:
    expr: ColumnRef
    descending: bool = False


@dataclass
class SelectStatement:
    columns: List[SelectItem]
    is_star: bool
    table: str
    table_alias: Optional[str]
    joins: List[JoinClause] = field(default_factory=list)
    where: Optional[Any] = None
    group_by: List[ColumnRef] = field(default_factory=list)
    order_by: List[OrderItem] = field(default_factory=list)
    limit: Optional[int] = None


AGG_FUNCS = {"count", "sum", "avg", "min", "max"}


class Parser:
    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.pos = 0

    # -- low level helpers -------------------------------------------
    def peek(self) -> Token:
        return self.tokens[self.pos]

    def advance(self) -> Token:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def check_keyword(self, *words) -> bool:
        tok = self.peek()
        return tok.type == TokenType.KEYWORD and tok.value in words

    def expect_keyword(self, word) -> Token:
        if not self.check_keyword(word):
            raise ParseError(f"Expected keyword {word!r}, got {self.peek()}")
        return self.advance()

    def expect_punct(self, value) -> Token:
        tok = self.peek()
        if tok.type != TokenType.PUNCT or tok.value != value:
            raise ParseError(f"Expected {value!r}, got {tok}")
        return self.advance()

    def expect_ident(self) -> str:
        tok = self.peek()
        if tok.type != TokenType.IDENT:
            raise ParseError(f"Expected identifier, got {tok}")
        self.advance()
        return tok.value

    def _at_comma(self) -> bool:
        tok = self.peek()
        return tok.type == TokenType.PUNCT and tok.value == ","

    # -- entry point ----------------------------------------------------
    def parse_select(self) -> SelectStatement:
        self.expect_keyword("select")
        is_star, columns = self._parse_select_list()
        self.expect_keyword("from")
        table, table_alias = self._parse_table_ref()

        joins = []
        while self.check_keyword("join", "inner", "left"):
            joins.append(self._parse_join())

        where = None
        if self.check_keyword("where"):
            self.advance()
            where = self._parse_expr()

        group_by: List[ColumnRef] = []
        if self.check_keyword("group"):
            self.advance()
            self.expect_keyword("by")
            group_by.append(self._parse_column_ref())
            while self._at_comma():
                self.advance()
                group_by.append(self._parse_column_ref())

        order_by: List[OrderItem] = []
        if self.check_keyword("order"):
            self.advance()
            self.expect_keyword("by")
            order_by.append(self._parse_order_item())
            while self._at_comma():
                self.advance()
                order_by.append(self._parse_order_item())

        limit = None
        if self.check_keyword("limit"):
            self.advance()
            tok = self.peek()
            if tok.type != TokenType.NUMBER:
                raise ParseError(f"Expected number after LIMIT, got {tok}")
            self.advance()
            limit = int(float(tok.value))

        if self.peek().type != TokenType.EOF:
            raise ParseError(f"Unexpected trailing input at {self.peek()}")

        if is_star and group_by:
            raise ParseError("SELECT * cannot be combined with GROUP BY")

        return SelectStatement(
            columns=columns, is_star=is_star, table=table,
            table_alias=table_alias, joins=joins, where=where,
            group_by=group_by, order_by=order_by, limit=limit,
        )

    # -- select list ------------------------------------------------
    def _parse_select_list(self):
        if self.peek().type == TokenType.STAR:
            self.advance()
            return True, []
        items = [self._parse_select_item()]
        while self._at_comma():
            self.advance()
            items.append(self._parse_select_item())
        return False, items

    def _parse_select_item(self) -> SelectItem:
        expr = self._parse_select_expr()
        alias = None
        if self.check_keyword("as"):
            self.advance()
            alias = self.expect_ident()
        elif self.peek().type == TokenType.IDENT:
            alias = self.advance().value
        return SelectItem(expr=expr, alias=alias)

    def _parse_select_expr(self):
        tok = self.peek()
        if tok.type == TokenType.KEYWORD and tok.value in AGG_FUNCS:
            return self._parse_aggregate()
        return self._parse_column_ref()

    def _parse_aggregate(self) -> AggregateCall:
        func = self.advance().value
        self.expect_punct("(")
        distinct = False
        if self.check_keyword("distinct"):
            self.advance()
            distinct = True
        if self.peek().type == TokenType.STAR:
            self.advance()
            if func != "count":
                raise ParseError(f"{func.upper()}(*) is not supported, only COUNT(*)")
            arg = None
        else:
            arg = self._parse_column_ref()
        self.expect_punct(")")
        return AggregateCall(func=func.upper(), arg=arg, distinct=distinct)

    # -- table refs / joins ------------------------------------------
    def _parse_table_ref(self):
        name = self.expect_ident()
        alias = None
        if self.check_keyword("as"):
            self.advance()
            alias = self.expect_ident()
        elif self.peek().type == TokenType.IDENT:
            alias = self.advance().value
        return name, alias

    def _parse_join(self) -> JoinClause:
        kind = "INNER"
        if self.check_keyword("inner"):
            self.advance()
        elif self.check_keyword("left"):
            self.advance()
            kind = "LEFT"
        self.expect_keyword("join")
        table, alias = self._parse_table_ref()
        self.expect_keyword("on")
        on = self._parse_expr()
        return JoinClause(table=table, alias=alias, kind=kind, on=on)

    # -- expressions --------------------------------------------------
    def _parse_expr(self):
        return self._parse_or()

    def _parse_or(self):
        left = self._parse_and()
        while self.check_keyword("or"):
            self.advance()
            left = BinaryOp(op="OR", left=left, right=self._parse_and())
        return left

    def _parse_and(self):
        left = self._parse_not()
        while self.check_keyword("and"):
            self.advance()
            left = BinaryOp(op="AND", left=left, right=self._parse_not())
        return left

    def _parse_not(self):
        if self.check_keyword("not"):
            self.advance()
            return UnaryNot(expr=self._parse_not())
        return self._parse_comparison()

    def _parse_comparison(self):
        if self.peek().type == TokenType.PUNCT and self.peek().value == "(":
            self.advance()
            expr = self._parse_expr()
            self.expect_punct(")")
            return expr

        left = self._parse_operand()

        if self.check_keyword("is"):
            self.advance()
            negated = False
            if self.check_keyword("not"):
                self.advance()
                negated = True
            self.expect_keyword("null")
            return IsNull(expr=left, negated=negated)

        if self.check_keyword("in"):
            self.advance()
            self.expect_punct("(")
            values = [self._parse_operand()]
            while self._at_comma():
                self.advance()
                values.append(self._parse_operand())
            self.expect_punct(")")
            return InList(expr=left, values=values)

        if self.peek().type == TokenType.OP:
            op = self.advance().value
            right = self._parse_operand()
            return BinaryOp(op=op, left=left, right=right)

        return left

    def _parse_operand(self):
        tok = self.peek()
        if tok.type == TokenType.NUMBER:
            self.advance()
            value = float(tok.value) if "." in tok.value else int(tok.value)
            return Literal(value=value)
        if tok.type == TokenType.STRING:
            self.advance()
            return Literal(value=tok.value)
        return self._parse_column_ref()

    def _parse_column_ref(self) -> ColumnRef:
        name = self.expect_ident()
        if self.peek().type == TokenType.PUNCT and self.peek().value == ".":
            self.advance()
            col = self.expect_ident()
            return ColumnRef(table=name, name=col)
        return ColumnRef(table=None, name=name)

    def _parse_order_item(self) -> OrderItem:
        expr = self._parse_column_ref()
        descending = False
        if self.check_keyword("desc"):
            self.advance()
            descending = True
        elif self.check_keyword("asc"):
            self.advance()
        return OrderItem(expr=expr, descending=descending)


def parse(sql: str) -> SelectStatement:
    """Parse a single SELECT statement into a SelectStatement AST."""
    tokens = tokenize(sql)
    return Parser(tokens).parse_select()
