"""HIR (High-level Intermediate Representation) node definitions for RoLang.

HIR is a desugared, explicitly typed representation of the AST. All syntactic
sugar (optional chaining, nil coalescing) is lowered to explicit operations.
Every expression carries its resolved TypeId.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Union, List, Tuple, TYPE_CHECKING
from abc import ABC

if TYPE_CHECKING:
    from .types import TypeId
    from .symbols import SymbolId


# ========================= Base Types =========================

@dataclass
class HirNode(ABC):
    """Base class for all HIR nodes."""
    pass


# ========================= HIR Program =========================

@dataclass
class HirProgram(HirNode):
    """Root node of the HIR."""
    items: List[HirItem] = field(default_factory=list)


# ========================= HIR Items =========================

@dataclass
class HirItem(HirNode):
    """Base class for top-level items."""
    pass


@dataclass
class HirFunction(HirItem):
    """Function declaration in HIR.

    Methods can update `self` through its heap reference.
    """
    name: str
    symbol_id: SymbolId
    params: List[HirParam]
    return_type: TypeId
    body: Optional[HirBlock]
    is_async: bool = False
    is_method: bool = False
    is_static: bool = False


@dataclass
class HirExternFunc(HirItem):
    """External function declaration in HIR."""
    name: str
    symbol_id: SymbolId
    abi: str
    params: List[HirParam]
    return_type: TypeId


@dataclass
class HirParam(HirNode):
    """Function parameter in HIR."""
    name: str
    symbol_id: SymbolId
    type_id: TypeId
    external_name: Optional[str] = None
    has_default: bool = False


@dataclass
class HirStruct(HirItem):
    """Struct declaration in HIR.

    The two Python-style dunder methods recognised by the compiler are
    expressed as ordinary entries in ``methods``:

      * ``def __release__() -> Void`` — the destructor. Codegen installs
        the mangled method as ``TypeDescriptor.deinit_fn`` so the runtime
        invokes it on the final reference-count decrement (and during
        GC sweeps).
      * ``static def __gc_trace__(payload: RawPtr, cb: RawPtr, ctx: RawPtr) -> Void``
        — the cycle-collector trace hook for containers whose managed
        pointers live inside an opaque buffer reached via ``RawPtr``.
        Codegen installs it as ``TypeDescriptor.trace_fn``.
    """
    name: str
    symbol_id: SymbolId
    fields: List[HirField]
    methods: List[HirFunction]


@dataclass
class HirField(HirNode):
    """Struct field in HIR."""
    name: str
    symbol_id: SymbolId
    type_id: TypeId
    is_mutable: bool = False
    default_value: Optional[HirExpr] = None


@dataclass
class HirEnum(HirItem):
    """Enum declaration in HIR."""
    name: str
    symbol_id: SymbolId
    cases: List[HirEnumCase]
    methods: List[HirFunction]


@dataclass
class HirEnumCase(HirNode):
    """Enum case in HIR."""
    name: str
    symbol_id: SymbolId
    payload: List[Tuple[Optional[str], TypeId]]  # (label, type)


@dataclass
class HirProtocol(HirItem):
    """Protocol declaration in HIR."""
    name: str
    symbol_id: SymbolId
    func_requirements: List[HirFuncRequirement]
    prop_requirements: List[HirPropRequirement]


@dataclass
class HirFuncRequirement(HirNode):
    """Protocol function requirement."""
    name: str
    params: List[Tuple[Optional[str], TypeId]]  # (label, type)
    return_type: TypeId
    is_async: bool = False


@dataclass
class HirPropRequirement(HirNode):
    """Protocol property requirement."""
    name: str
    type_id: TypeId
    has_getter: bool = True
    has_setter: bool = False


@dataclass
class HirExtension(HirItem):
    """Extension declaration in HIR."""
    extended_type: TypeId
    methods: List[HirFunction]


# ========================= HIR Statements =========================

@dataclass
class HirStmt(HirNode):
    """Base class for all HIR statements."""
    pass


@dataclass
class HirBlock(HirStmt):
    """Block of statements."""
    statements: List[HirStmt] = field(default_factory=list)


@dataclass
class HirVarDecl(HirStmt):
    """Variable declaration."""
    name: str
    symbol_id: SymbolId
    type_id: TypeId
    initializer: Optional[HirExpr] = None
    is_mutable: bool = False


@dataclass
class HirAssign(HirStmt):
    """Assignment statement."""
    target: HirExpr
    value: HirExpr
    compound_op: Optional[str] = None  # For +=, -=, etc.


@dataclass
class HirExprStmt(HirStmt):
    """Expression statement."""
    expr: HirExpr


@dataclass
class HirReturn(HirStmt):
    """Return statement."""
    value: Optional[HirExpr] = None


@dataclass
class HirBreak(HirStmt):
    """Break statement."""
    pass


@dataclass
class HirContinue(HirStmt):
    """Continue statement."""
    pass


@dataclass
class HirIf(HirStmt):
    """If statement."""
    condition: HirExpr
    then_block: HirBlock
    else_block: Optional[Union[HirBlock, HirIf]] = None


@dataclass
class HirIfLet(HirStmt):
    """If-let statement (pattern matching on optional)."""
    pattern: HirPattern
    scrutinee: HirExpr
    then_block: HirBlock
    else_block: Optional[Union[HirBlock, HirIf]] = None


@dataclass
class HirGuard(HirStmt):
    """Guard statement."""
    condition: HirExpr
    else_block: HirBlock


@dataclass
class HirWhile(HirStmt):
    """While loop."""
    condition: HirExpr
    body: HirBlock


@dataclass
class HirFor(HirStmt):
    """For-in loop."""
    pattern: HirPattern
    iterable: HirExpr
    body: HirBlock


@dataclass
class HirSwitchCase(HirNode):
    """A case in a switch statement."""
    patterns: List[Tuple[HirPattern, Optional[HirExpr]]]  # (pattern, guard)
    body: HirBlock
    is_default: bool = False


@dataclass
class HirSwitch(HirStmt):
    """Switch statement."""
    scrutinee: HirExpr
    scrutinee_type: TypeId
    cases: List[HirSwitchCase]


@dataclass
class HirDefer(HirStmt):
    """Defer statement."""
    body: HirBlock


# ========================= HIR Expressions =========================

@dataclass
class HirExpr(HirNode):
    """Base class for all HIR expressions. Every expression is typed."""
    type_id: TypeId


@dataclass
class HirLiteral(HirExpr):
    """Literal value."""
    value: Union[int, float, bool, str, None, TypeId]
    kind: str  # "int", "float", "bool", "string", "nil", "size_of", "type_id", "align_of"


@dataclass
class HirVar(HirExpr):
    """Variable reference."""
    name: str
    symbol_id: SymbolId


@dataclass
class HirBinaryOp(HirExpr):
    """Binary operation."""
    left: HirExpr
    op: str
    right: HirExpr


@dataclass
class HirUnaryOp(HirExpr):
    """Unary operation."""
    op: str
    operand: HirExpr


@dataclass
class HirTernary(HirExpr):
    """Ternary expression: cond ? then : else"""
    condition: HirExpr
    then_expr: HirExpr
    else_expr: HirExpr


@dataclass
class HirCall(HirExpr):
    """Function call."""
    callee: HirExpr
    arguments: List[Tuple[Optional[str], HirExpr]]  # (label, value)
    callee_symbol: Optional[SymbolId] = None


@dataclass
class HirMethodCall(HirExpr):
    """Method call on an object."""
    receiver: HirExpr
    method_name: str
    arguments: List[Tuple[Optional[str], HirExpr]]
    method_symbol: Optional[SymbolId] = None
    is_static: bool = False


@dataclass
class HirFieldAccess(HirExpr):
    """Field access on a struct."""
    object: HirExpr
    field_name: str
    field_symbol: Optional[SymbolId] = None


@dataclass
class HirSubscript(HirExpr):
    """Subscript access: obj[index]"""
    object: HirExpr
    indices: List[HirExpr]


@dataclass
class HirTuple(HirExpr):
    """Tuple expression."""
    elements: List[Tuple[Optional[str], HirExpr]]


@dataclass
class HirArray(HirExpr):
    """Array literal."""
    elements: List[HirExpr]
    element_type: TypeId


@dataclass
class HirDict(HirExpr):
    """Dictionary literal."""
    entries: List[Tuple[HirExpr, HirExpr]]
    key_type: TypeId
    value_type: TypeId


@dataclass
class HirLambda(HirExpr):
    """Lambda expression."""
    params: List[HirParam]
    body: HirBlock
    captures: List[SymbolId] = field(default_factory=list)


@dataclass
class HirClone(HirExpr):
    """Deep-clone of a heap value via .clone() — lowers to rt_obj_clone."""
    value: HirExpr


@dataclass
class HirStructInit(HirExpr):
    """Struct literal: Type { field: value }"""
    struct_type: TypeId
    struct_symbol: SymbolId
    arguments: List[Tuple[Optional[str], HirExpr]]


@dataclass
class HirEnumConstruct(HirExpr):
    """Enum case construction: .case(args)"""
    enum_type: TypeId
    case_name: str
    case_symbol: Optional[SymbolId] = None
    payload: List[Tuple[Optional[str], HirExpr]] = field(default_factory=list)


@dataclass
class HirTryExpr(HirExpr):
    """Try expression: expr? - propagates error via early return."""
    expr: HirExpr
    result_type: TypeId  # T from Result<T, E>

@dataclass
class HirCast(HirExpr):
    """Type cast: expr as Type / expr as? Type / expr as! Type.

    See :class:`rolang.ast.Cast` for the meaning of ``kind``.
    """
    expr: HirExpr
    target_type: TypeId
    kind: str = "safe"


@dataclass
class HirTypeCheck(HirExpr):
    """Type check: expr is Type"""
    expr: HirExpr
    checked_type: TypeId


# ========================= Desugared Optional Operations =========================

@dataclass
class HirOptionalSome(HirExpr):
    """Wrap a value in Some. Used in desugared optional operations."""
    value: HirExpr
    inner_type: TypeId


@dataclass
class HirOptionalNone(HirExpr):
    """The None value for an optional. Used in desugared optional operations."""
    inner_type: TypeId


@dataclass
class HirOptionalMatch(HirExpr):
    """Desugared optional operation (from ?. or ??).

    Evaluates scrutinee. If Some(value), binds value to some_binding
    and evaluates some_expr. If None, evaluates none_expr.

    This is the core lowering for:
    - Optional chaining: a?.b becomes match(a, some: Some(tmp.b), none: None)
    - Nil coalescing: a ?? b becomes match(a, some: tmp, none: b)
    """
    scrutinee: HirExpr
    inner_type: TypeId  # The T in T?
    some_binding: SymbolId  # Temporary variable for unwrapped value
    some_expr: HirExpr
    none_expr: HirExpr


# ========================= HIR Patterns =========================

@dataclass
class HirPattern(HirNode):
    """Base class for all HIR patterns."""
    pass


@dataclass
class HirWildcardPattern(HirPattern):
    """Wildcard pattern: _"""
    pass


@dataclass
class HirBindingPattern(HirPattern):
    """Binding pattern: let x or var x"""
    name: str
    symbol_id: SymbolId
    type_id: TypeId
    is_mutable: bool = False


@dataclass
class HirLiteralPattern(HirPattern):
    """Literal pattern for matching values."""
    value: Union[int, float, bool, str]
    type_id: TypeId


@dataclass
class HirTuplePattern(HirPattern):
    """Tuple pattern: (p1, p2, ...)"""
    elements: List[Tuple[Optional[str], HirPattern]]
    type_id: TypeId


@dataclass
class HirEnumCasePattern(HirPattern):
    """Enum case pattern: .case(patterns)"""
    case_name: str
    case_symbol: Optional[SymbolId]
    payload: List[HirPattern]
    enum_type: TypeId


@dataclass
class HirOrPattern(HirPattern):
    """Or pattern: p1 | p2"""
    patterns: List[HirPattern]
    type_id: TypeId


# ========================= Type Aliases =========================

# Union of all HIR items
HirItemType = Union[
    HirFunction, HirExternFunc, HirStruct, HirEnum,
    HirProtocol, HirExtension
]

# Union of all HIR statements
HirStmtType = Union[
    HirBlock, HirVarDecl, HirAssign, HirExprStmt, HirReturn,
    HirBreak, HirContinue, HirIf, HirIfLet, HirGuard,
    HirWhile, HirFor, HirSwitch, HirDefer
]

# Union of all HIR expressions
HirExprType = Union[
    HirLiteral, HirVar, HirBinaryOp, HirUnaryOp, HirTernary,
    HirCall, HirMethodCall, HirFieldAccess, HirSubscript,
    HirTuple, HirArray, HirDict, HirLambda,
    HirStructInit, HirEnumConstruct, HirCast, HirTypeCheck, HirTryExpr,
    HirOptionalSome, HirOptionalNone, HirOptionalMatch
]

# Union of all HIR patterns
HirPatternType = Union[
    HirWildcardPattern, HirBindingPattern, HirLiteralPattern,
    HirTuplePattern, HirEnumCasePattern, HirOrPattern
]
