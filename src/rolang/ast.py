"""AST node definitions for RoLang."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Union


# ========================= Base Types =========================
@dataclass
class Span:
    """Source location information."""
    line: int
    column: int
    end_line: int = 0
    end_column: int = 0


@dataclass
class Node:
    """Base class for all AST nodes."""
    span: Optional[Span] = None
    pass


# ========================= Program =========================
@dataclass
class Program(Node):
    """Root node of the AST."""
    items: list[TopLevelItem] = field(default_factory=list)


# ========================= Types =========================
@dataclass
class Type(Node):
    """Base class for all type nodes."""
    pass


@dataclass
class BuiltinType(Type):
    """Built-in primitive types: i32, f64, Bool, etc."""
    name: str = ""


@dataclass
class NamedType(Type):
    """Named type reference with optional generic arguments."""
    name: str = ""
    module_path: list[str] = field(default_factory=list)
    generic_args: list[Type] = field(default_factory=list)


@dataclass
class OptionalType(Type):
    """Optional type: T?"""
    inner: Optional[Type] = None


@dataclass
class ArrayType(Type):
    """Array type: [T]"""
    element: Optional[Type] = None


@dataclass
class DictType(Type):
    """Dictionary type: [K: V]"""
    key: Optional[Type] = None
    value: Optional[Type] = None


@dataclass
class TupleType(Type):
    """Tuple type: (T1, T2, ...)"""
    elements: list[tuple[Optional[str], Type]] = field(default_factory=list)


@dataclass
class FunctionType(Type):
    """Function type: (Args) -> Return"""
    params: list[Type] = field(default_factory=list)
    return_type: Optional[Type] = None
    is_async: bool = False
    throws: bool = False


@dataclass
class AnyType(Type):
    """Existential type: any Protocol"""
    protocol: Optional[NamedType] = None


@dataclass
class PointerType(Type):
    """Raw pointer type: RawPtr"""
    pass


# ========================= Patterns =========================
@dataclass
class Pattern(Node):
    """Base class for all pattern nodes."""
    pass


@dataclass
class WildcardPattern(Pattern):
    """Wildcard pattern: _"""
    pass


@dataclass
class IdentifierPattern(Pattern):
    """Identifier pattern: let x, var x, or just x"""
    name: str = ""
    binding: Optional[str] = None  # "let", "var", or None


@dataclass
class LiteralPattern(Pattern):
    """Literal pattern for matching values."""
    value: Optional[Literal] = None


@dataclass
class TuplePattern(Pattern):
    """Tuple pattern: (p1, p2, ...)"""
    elements: list[tuple[Optional[str], Pattern]] = field(default_factory=list)


@dataclass
class EnumCasePattern(Pattern):
    """Enum case pattern: .case(patterns)"""
    case_name: str = ""
    payload: list[Pattern] = field(default_factory=list)


@dataclass
class TypedPattern(Pattern):
    """Typed pattern: pattern : Type"""
    pattern: Optional[Pattern] = None
    type_annotation: Optional[Type] = None


@dataclass
class OrPattern(Pattern):
    """Or pattern: p1 | p2"""
    patterns: list[Pattern] = field(default_factory=list)


# ========================= Expressions =========================
@dataclass
class Expr(Node):
    """Base class for all expression nodes."""
    pass


@dataclass
class Literal(Expr):
    """Literal expression."""
    value: Union[int, float, bool, str, None] = None
    kind: str = ""  # "int", "float", "bool", "string", "nil"


@dataclass
class Identifier(Expr):
    """Identifier expression."""
    name: str = ""


@dataclass
class TypeReference(Expr):
    """Type used as an expression receiver, e.g. Result<T, E>.ok."""
    type_name: Optional[NamedType] = None


@dataclass
class BinaryOp(Expr):
    """Binary operation: a op b"""
    left: Optional[Expr] = None
    op: str = ""
    right: Optional[Expr] = None


@dataclass
class UnaryOp(Expr):
    """Unary operation: op a"""
    op: str = ""
    operand: Optional[Expr] = None


@dataclass
class TernaryOp(Expr):
    """Ternary expression: cond ? then : else"""
    condition: Optional[Expr] = None
    then_expr: Optional[Expr] = None
    else_expr: Optional[Expr] = None


@dataclass
class Argument(Node):
    """Function argument with optional label."""
    label: Optional[str] = None
    value: Optional[Expr] = None


@dataclass
class Call(Expr):
    """Function call: func(args)"""
    callee: Optional[Expr] = None
    arguments: list[Argument] = field(default_factory=list)


@dataclass
class TryExpr(Expr):
    """Try expression: expr? - propagates error via return"""
    value: Optional[Expr] = None


@dataclass
class MemberAccess(Expr):
    """Member access: obj.member"""
    object: Optional[Expr] = None
    member: str = ""


@dataclass
class OptionalChain(Expr):
    """Optional chaining: obj?.member"""
    object: Optional[Expr] = None
    member: str = ""
    suffix: Optional[Union[list[Argument], Expr]] = None  # Call args or subscript


@dataclass
class Subscript(Expr):
    """Subscript access: obj[index]"""
    object: Optional[Expr] = None
    indices: list[Expr] = field(default_factory=list)


@dataclass
class TupleExpr(Expr):
    """Tuple expression: (a, b, c)"""
    elements: list[tuple[Optional[str], Expr]] = field(default_factory=list)


@dataclass
class ArrayLiteral(Expr):
    """Array literal: [a, b, c]"""
    elements: list[Expr] = field(default_factory=list)


@dataclass
class DictLiteral(Expr):
    """Dictionary literal: [k1: v1, k2: v2]"""
    entries: list[tuple[Expr, Expr]] = field(default_factory=list)


@dataclass
class Lambda(Expr):
    """Lambda expression: { params in body }"""
    params: list[tuple[Pattern, Optional[Type]]] = field(default_factory=list)
    body: list[Stmt] = field(default_factory=list)


@dataclass
class StructLiteral(Expr):
    """Struct literal: Type { field: value }"""
    type_name: Optional[NamedType] = None
    arguments: list[Argument] = field(default_factory=list)


@dataclass
class SizeOfExpr(Expr):
    """size_of(T) — storage size of T in bytes, as i32."""
    type_arg: Optional[Type] = None


@dataclass
class TypeIdExpr(Expr):
    """type_id(T) — runtime type-descriptor index, as i32."""
    type_arg: Optional[Type] = None


@dataclass
class AlignOfExpr(Expr):
    """align_of(T) — alignment of T in bytes, as i32."""
    type_arg: Optional[Type] = None


@dataclass
class DropOfExpr(Expr):
    """drop_of(T) — true if T has a __release__ destructor, false otherwise."""
    type_arg: Optional[Type] = None


@dataclass
class CloneOfExpr(Expr):
    """clone_of(T) — true if T supports .clone(), false otherwise."""
    type_arg: Optional[Type] = None


@dataclass
class Cast(Expr):
    """Type cast: expr as Type / expr as? Type / expr as! Type.

    ``kind`` selects between the three flavors:
        * ``"safe"``     — the original ``as`` cast (numeric / identity /
          implicit-widening cast). Compile-time-checked.
        * ``"optional"`` — runtime-checked downcast (``as?``). Produces
          ``Optional<Target>``: ``Some`` on a successful type match,
          ``None`` otherwise.
        * ``"forced"``   — force-unwrapping downcast (``as!``). Yields
          ``Target`` directly, panicking at runtime on a type mismatch.
    """
    expr: Optional[Expr] = None
    target_type: Optional[Type] = None
    kind: str = "safe"


@dataclass
class TypeCheck(Expr):
    """Type check: expr is Type"""
    expr: Optional[Expr] = None
    checked_type: Optional[Type] = None


# ========================= Statements =========================
@dataclass
class Stmt(Node):
    """Base class for all statement nodes."""
    pass


@dataclass
class VarDecl(Stmt):
    """Variable declaration: let/var pattern = expr"""
    pattern: Optional[Pattern] = None
    type_annotation: Optional[Type] = None
    initializer: Optional[Expr] = None
    is_mutable: bool = False


@dataclass
class Assignment(Stmt):
    """Assignment: lvalue op= expr"""
    target: Optional[Expr] = None
    op: str = "="  # "=", "+=", "-=", etc.
    value: Optional[Expr] = None


@dataclass
class ExprStmt(Stmt):
    """Expression statement."""
    expr: Optional[Expr] = None


@dataclass
class ReturnStmt(Stmt):
    """Return statement."""
    value: Optional[Expr] = None
    # True when this return was synthesised from a block's trailing expression
    # (implicit return / block value) rather than written as an explicit
    # `return`. Loop bodies demote such trailing expressions to plain
    # expression-statements, since a loop body is never in function-tail
    # position.
    implicit: bool = False


@dataclass
class BreakStmt(Stmt):
    """Break statement."""
    pass


@dataclass
class ContinueStmt(Stmt):
    """Continue statement."""
    pass


@dataclass
class Block(Stmt):
    """Block of statements."""
    statements: list[Stmt] = field(default_factory=list)
    is_unsafe: bool = False


@dataclass
class IfStmt(Stmt):
    """If statement."""
    condition: Union[Expr, tuple[Pattern, Expr], None] = None  # expr or (let pattern = expr)
    then_block: Optional[Block] = None
    else_block: Optional[Union[Block, IfStmt]] = None


@dataclass
class GuardStmt(Stmt):
    """Guard statement: guard condition else { }"""
    condition: Optional[Expr] = None
    else_block: Optional[Block] = None


@dataclass
class WhileStmt(Stmt):
    """While loop."""
    condition: Optional[Expr] = None
    body: Optional[Block] = None


@dataclass
class ForStmt(Stmt):
    """For-in loop."""
    pattern: Optional[Pattern] = None
    iterable: Optional[Expr] = None
    body: Optional[Block] = None


@dataclass
class SwitchCase(Node):
    """A case in a switch statement."""
    patterns: list[tuple[Pattern, Optional[Expr]]] = field(default_factory=list)  # (pattern, where_guard)
    body: list[Stmt] = field(default_factory=list)
    is_default: bool = False


@dataclass
class SwitchStmt(Stmt):
    """Switch statement."""
    value: Optional[Expr] = None
    cases: list[SwitchCase] = field(default_factory=list)


@dataclass
class DeferStmt(Stmt):
    """Defer statement."""
    body: Optional[Block] = None


# ========================= Declarations =========================
@dataclass
class GenericParam(Node):
    """Generic parameter: T: Constraint"""
    name: str = ""
    bounds: list[NamedType] = field(default_factory=list)


@dataclass
class Constraint(Node):
    """Type constraint in where clause."""
    subject: Union[NamedType, str, None] = None  # Type or "Self"
    kind: str = "conforms"  # "conforms" (: bounds) or "equals" (== type)
    bounds: list[NamedType] = field(default_factory=list)
    equal_type: Optional[Type] = None


@dataclass
class TopLevelItem(Node):
    """Base class for top-level items."""
    visibility: str = "internal"


@dataclass
class ImportDecl(TopLevelItem):
    """Import declaration: import \"file.rl\" or import std.io or import \"file.rl\" as Alias."""
    path: str = ""            # The file path string (relative to importing file)
    module: list[str] = field(default_factory=list)  # Module path components for dotted imports
    alias: Optional[str] = None  # Optional namespace alias


@dataclass
class Param(Node):
    """Function parameter."""
    external_name: Optional[str] = None
    internal_name: str = ""
    type_annotation: Optional[Type] = None
    default_value: Optional[Expr] = None


@dataclass
class Accessor(Node):
    """Property accessor: get or set."""
    kind: str = "get"  # "get" or "set"
    param_name: Optional[str] = None  # For set(newValue)
    body: Optional[Block] = None


@dataclass
class StructMember(Node):
    """Base class for struct members."""
    visibility: str = "internal"


@dataclass
class PropertyDecl(StructMember):
    """Property declaration."""
    name: str = ""
    type_annotation: Optional[Type] = None
    initializer: Optional[Expr] = None
    is_mutable: bool = False
    accessors: Optional[list[Accessor]] = None


@dataclass
class FuncDecl(TopLevelItem):
    """Function declaration.

    Methods can freely update `self` through its heap reference. Use stdlib
    locks for cross-task synchronisation.

    `is_unsafe` marks the function as unsafe to call from safe code; the
    call site must be inside an `unsafe { ... }` block. Used by stdlib
    wrappers that expose unchecked C primitives.
    """
    name: str = ""
    generic_params: list[GenericParam] = field(default_factory=list)
    params: list[Param] = field(default_factory=list)
    return_type: Optional[Type] = None
    constraints: list[Constraint] = field(default_factory=list)
    body: Optional[Block] = None
    is_async: bool = False
    throws: bool = False
    is_static: bool = False
    is_unsafe: bool = False


@dataclass
class ExternFuncDecl(TopLevelItem):
    """External function declaration."""
    abi: str = ""  # e.g., "C"
    name: str = ""
    generic_params: list[GenericParam] = field(default_factory=list)
    params: list[Param] = field(default_factory=list)
    return_type: Optional[Type] = None
    constraints: list[Constraint] = field(default_factory=list)
    is_async: bool = False
    throws: bool = False


@dataclass
class StructDecl(TopLevelItem):
    """Struct declaration."""
    name: str = ""
    generic_params: list[GenericParam] = field(default_factory=list)
    constraints: list[Constraint] = field(default_factory=list)
    members: list[StructMember] = field(default_factory=list)


@dataclass
class EnumCaseDef(Node):
    """A single enum case definition."""
    name: str = ""
    payload: list[tuple[Optional[str], Type]] = field(default_factory=list)


@dataclass
class EnumMember(Node):
    """Base class for enum members."""
    visibility: str = "internal"


@dataclass
class EnumCaseDecl(EnumMember):
    """Enum case declaration."""
    cases: list[EnumCaseDef] = field(default_factory=list)


@dataclass
class EnumDecl(TopLevelItem):
    """Enum declaration."""
    name: str = ""
    generic_params: list[GenericParam] = field(default_factory=list)
    constraints: list[Constraint] = field(default_factory=list)
    members: list[EnumMember] = field(default_factory=list)


@dataclass
class ProtocolMember(Node):
    """Base class for protocol members."""
    visibility: str = "internal"


@dataclass
class ProtocolFuncReq(ProtocolMember):
    """Protocol function requirement."""
    name: str = ""
    generic_params: list[GenericParam] = field(default_factory=list)
    params: list[Param] = field(default_factory=list)
    return_type: Optional[Type] = None
    is_async: bool = False
    throws: bool = False


@dataclass
class ProtocolPropReq(ProtocolMember):
    """Protocol property requirement."""
    name: str = ""
    type_annotation: Optional[Type] = None
    is_mutable: bool = False
    has_getter: bool = True
    has_setter: bool = False


@dataclass
class AssociatedTypeDecl(ProtocolMember):
    """Associated type declaration."""
    name: str = ""
    constraints: list[Constraint] = field(default_factory=list)


@dataclass
class ProtocolDecl(TopLevelItem):
    """Protocol declaration."""
    name: str = ""
    generic_params: list[GenericParam] = field(default_factory=list)
    constraints: list[Constraint] = field(default_factory=list)
    members: list[ProtocolMember] = field(default_factory=list)


@dataclass
class ExtensionDecl(TopLevelItem):
    """Extension declaration."""
    extended_type: Optional[NamedType] = None
    conformances: list[NamedType] = field(default_factory=list)
    constraints: list[Constraint] = field(default_factory=list)
    members: list[StructMember] = field(default_factory=list)


@dataclass
class TypeAliasDecl(TopLevelItem):
    """Type alias declaration."""
    name: str = ""
    aliased_type: Optional[Type] = None
