"""Shared operator metadata used across semantic and lowering phases."""

from __future__ import annotations

from typing import Final


ARITHMETIC_OPERATORS: Final[frozenset[str]] = frozenset({"+", "-", "*", "/", "%"})
ORDERING_OPERATORS: Final[frozenset[str]] = frozenset({"<", ">", "<=", ">="})
EQUALITY_OPERATORS: Final[frozenset[str]] = frozenset({"==", "!="})
LOGICAL_OPERATORS: Final[frozenset[str]] = frozenset({"&&", "||"})
BITWISE_OPERATORS: Final[frozenset[str]] = frozenset({"&", "|", "^", "<<", ">>"})

SHORT_CIRCUIT_AND_OPERATORS: Final[frozenset[str]] = frozenset({"&&", "and"})
SHORT_CIRCUIT_OR_OPERATORS: Final[frozenset[str]] = frozenset({"||", "or"})
NIL_COALESCING_OPERATOR: Final[str] = "??"

OPERATOR_TO_METHOD: Final[dict[str, str]] = {
    "+": "__add__",
    "-": "__sub__",
    "*": "__mul__",
    "/": "__truediv__",
    "%": "__mod__",
    "==": "__eq__",
    "!=": "__ne__",
    "<": "__lt__",
    ">": "__gt__",
    "<=": "__le__",
    ">=": "__ge__",
    "&": "__and__",
    "|": "__or__",
    "^": "__xor__",
    "<<": "__lshift__",
    ">>": "__rshift__",
}

# Maps from source operator tokens to MIR operator kinds.
# Imported here so lowering phases can share one canonical table.
from .mir import BinOpKind, CmpOpKind, UnaryOpKind  # noqa: E402

BINOP_MAP: Final[dict[str, BinOpKind]] = {
    "+": BinOpKind.ADD,
    "-": BinOpKind.SUB,
    "*": BinOpKind.MUL,
    "/": BinOpKind.DIV,
    "%": BinOpKind.MOD,
    "&": BinOpKind.BIT_AND,
    "|": BinOpKind.BIT_OR,
    "^": BinOpKind.BIT_XOR,
    "<<": BinOpKind.SHL,
    ">>": BinOpKind.SHR,
}

CMPOP_MAP: Final[dict[str, CmpOpKind]] = {
    "==": CmpOpKind.EQ,
    "!=": CmpOpKind.NE,
    "<": CmpOpKind.LT,
    "<=": CmpOpKind.LE,
    ">": CmpOpKind.GT,
    ">=": CmpOpKind.GE,
}

UNARYOP_MAP: Final[dict[str, UnaryOpKind]] = {
    "-": UnaryOpKind.NEG,
    "!": UnaryOpKind.NOT,
    "~": UnaryOpKind.BIT_NOT,
}

# --- Helper queries ---

def is_arithmetic_op(op: str) -> bool:
    """True for +, -, *, /, %."""
    return op in ARITHMETIC_OPERATORS


def is_order_comparison_op(op: str) -> bool:
    """True for <, >, <=, >=."""
    return op in ORDERING_OPERATORS


def is_equality_op(op: str) -> bool:
    """True for ==, !=."""
    return op in EQUALITY_OPERATORS


def is_logical_op(op: str) -> bool:
    """True for &&, ||."""
    return op in LOGICAL_OPERATORS


def is_short_circuit_op(op: str) -> bool:
    """True for &&, ||, and, or."""
    return op in SHORT_CIRCUIT_AND_OPERATORS or op in SHORT_CIRCUIT_OR_OPERATORS


def is_short_circuit_and_op(op: str) -> bool:
    """True for &&, and."""
    return op in SHORT_CIRCUIT_AND_OPERATORS


def is_short_circuit_or_op(op: str) -> bool:
    """True for ||, or."""
    return op in SHORT_CIRCUIT_OR_OPERATORS


def is_bitwise_op(op: str) -> bool:
    """True for &, |, ^, <<, >>."""
    return op in BITWISE_OPERATORS


def is_nil_coalescing_op(op: str) -> bool:
    """True for ??."""
    return op == NIL_COALESCING_OPERATOR


def to_method_name(op: str) -> str:
    """Map an operator to its protocol method name, or empty string."""
    return OPERATOR_TO_METHOD.get(op, "")


def compound_to_base_op(op: str) -> str:
    """Strip the trailing = from a compound-assignment operator.

    >>> compound_to_base_op("+=")
    '+'
    """
    return op[:-1] if op.endswith("=") else op
