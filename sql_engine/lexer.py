"""
Tokenizer for a minimal SQL dialect.

Turns a SQL string into a flat list of Tokens: keywords, identifiers,
numbers, single-quoted strings, comparison operators and punctuation.
No knowledge of grammar lives here -- that belongs to parser.py.
"""

from dataclasses import dataclass
from enum import Enum, auto
from typing import List


class TokenType(Enum):
    KEYWORD = auto()
    IDENT = auto()
    NUMBER = auto()
    STRING = auto()
    OP = auto()
    PUNCT = auto()
    STAR = auto()
    EOF = auto()


KEYWORDS = {
    "select", "from", "where", "join", "inner", "left", "on",
    "group", "by", "order", "asc", "desc", "limit", "and", "or",
    "not", "as", "count", "sum", "avg", "min", "max", "distinct",
    "null", "is", "in",
}

# Longest match first so "<=" is not tokenized as "<" then "=".
MULTI_CHAR_OPS = ["<=", ">=", "!=", "<>", "=", "<", ">"]


@dataclass
class Token:
    type: TokenType
    value: str
    pos: int

    def __repr__(self):
        return f"Token({self.type.name}, {self.value!r}, pos={self.pos})"


class LexError(Exception):
    pass


def tokenize(sql: str) -> List[Token]:
    tokens: List[Token] = []
    i = 0
    n = len(sql)

    while i < n:
        c = sql[i]

        if c.isspace():
            i += 1
            continue

        if c == "-" and i + 1 < n and sql[i + 1] == "-":
            while i < n and sql[i] != "\n":
                i += 1
            continue

        if c == "'":
            j = i + 1
            buf = []
            while j < n and sql[j] != "'":
                if sql[j] == "\\" and j + 1 < n:
                    buf.append(sql[j + 1])
                    j += 2
                    continue
                buf.append(sql[j])
                j += 1
            if j >= n:
                raise LexError(f"Unterminated string literal starting at position {i}")
            tokens.append(Token(TokenType.STRING, "".join(buf), i))
            i = j + 1
            continue

        if c.isdigit():
            j = i
            seen_dot = False
            while j < n and (sql[j].isdigit() or (sql[j] == "." and not seen_dot)):
                if sql[j] == ".":
                    seen_dot = True
                j += 1
            tokens.append(Token(TokenType.NUMBER, sql[i:j], i))
            i = j
            continue

        if c.isalpha() or c == "_":
            j = i
            while j < n and (sql[j].isalnum() or sql[j] == "_"):
                j += 1
            word = sql[i:j]
            lower = word.lower()
            if lower in KEYWORDS:
                tokens.append(Token(TokenType.KEYWORD, lower, i))
            else:
                tokens.append(Token(TokenType.IDENT, word, i))
            i = j
            continue

        if c == "*":
            tokens.append(Token(TokenType.STAR, "*", i))
            i += 1
            continue

        matched_op = None
        for op in MULTI_CHAR_OPS:
            if sql.startswith(op, i):
                matched_op = op
                break
        if matched_op:
            tokens.append(Token(TokenType.OP, matched_op, i))
            i += len(matched_op)
            continue

        if c in ".,()":
            tokens.append(Token(TokenType.PUNCT, c, i))
            i += 1
            continue

        raise LexError(f"Unexpected character {c!r} at position {i}")

    tokens.append(Token(TokenType.EOF, "", n))
    return tokens
