"""Core type-checker types that both checker and expr_checker need.

Extracted to avoid circular imports between checker.py and expr_checker.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, Dict, List

from . import ast
from .symbols import SymbolId
from .types import TypeId, TypeTable


class CalleeKind(Enum):
    """Kind of call target."""

    STATIC = auto()    # Direct function call
    METHOD = auto()    # Method on concrete type
    VTABLE = auto()    # Dynamic dispatch (any P)
    WITNESS = auto()   # Generic constraint dispatch
    ENUM_CTOR = auto() # Enum case construction: EnumName.case(...) or EnumName.case


@dataclass
class CalleeId:
    """Resolved call target."""

    kind: CalleeKind
    symbol_id: Optional[SymbolId] = None
    case_name: Optional[str] = None       # For ENUM_CTOR: which case


class TypeErrorKind(Enum):
    """Kind of type error."""

    TYPE_MISMATCH = auto()
    UNDEFINED_MEMBER = auto()
    NOT_CALLABLE = auto()
    WRONG_ARG_COUNT = auto()
    WRONG_ARG_TYPE = auto()
    CANNOT_INFER = auto()
    NOT_ASSIGNABLE = auto()
    INVALID_OPERATION = auto()
    NON_EXHAUSTIVE_MATCH = auto()
    NOT_A_TYPE = auto()
    GENERIC_ARG_COUNT = auto()
    NOT_A_PROTOCOL = auto()
    PROTOCOL_NOT_SATISFIED = auto()
    DUPLICATE_MEMBER = auto()


@dataclass
class TypeError:
    """A type checking error."""

    kind: TypeErrorKind
    message: str
    span: Optional[ast.Span] = None

    def __str__(self) -> str:
        location = ""
        if self.span:
            location = f" at line {self.span.line}, column {self.span.column}"
        return f"{self.kind.name}: {self.message}{location}"


@dataclass
class TypeCheckResult:
    """Result of type checking."""

    type_table: TypeTable
    expr_types: Dict[int, TypeId]      # id(node) -> TypeId
    call_targets: Dict[int, CalleeId]  # id(call) -> CalleeId
    operator_targets: Dict[int, CalleeId]  # id(BinaryOp) -> CalleeId
    errors: List[TypeError]
    # id(MemberAccess) -> SymbolId of the resolved method. Used by the
    # HIR builder to disambiguate same-named methods across modules.
    member_method_symbols: Dict[int, "SymbolId"] = field(default_factory=dict)

    def has_errors(self) -> bool:
        return len(self.errors) > 0

    def get_expr_type(self, expr: ast.Expr) -> Optional[TypeId]:
        return self.expr_types.get(id(expr))
