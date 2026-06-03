"""Symbol table infrastructure for RoLang name resolution."""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, Dict, List, TYPE_CHECKING

if TYPE_CHECKING:
    from .ast import Node, Span


class Namespace(Enum):
    """Namespace for symbol lookup."""
    TYPE = auto()   # Types: struct, enum, protocol, type alias, generic params
    VALUE = auto()  # Values: variables, functions, parameters, enum cases


class SymbolKind(Enum):
    """Kind of symbol."""
    # Value namespace
    VARIABLE = auto()
    FUNCTION = auto()
    EXTERN_FUNC = auto()
    PARAMETER = auto()
    ENUM_CASE = auto()
    FIELD = auto()  # Struct field
    # Type namespace
    STRUCT = auto()
    ENUM = auto()
    PROTOCOL = auto()
    TYPE_ALIAS = auto()
    GENERIC_PARAM = auto()
    ASSOCIATED_TYPE = auto()
    BUILTIN_TYPE = auto()
    EXTENSION = auto()  # extension X { ... } block


class ScopeKind(Enum):
    """Kind of scope."""
    MODULE = auto()       # Top-level module scope
    TYPE = auto()         # Struct/enum/protocol body
    FUNCTION = auto()     # Function body
    BLOCK = auto()        # Generic block { }
    LAMBDA = auto()       # Lambda expression
    FOR_LOOP = auto()     # For loop (pattern binding)
    SWITCH_CASE = auto()  # Switch case (pattern bindings)


@dataclass(frozen=True)
class SymbolId:
    """Unique identifier for a symbol."""
    id: int

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, SymbolId):
            return self.id == other.id
        return False

    def __repr__(self) -> str:
        return f"SymbolId({self.id})"


@dataclass
class Symbol:
    """A named entity in the program."""
    id: SymbolId
    name: str
    kind: SymbolKind
    namespace: Namespace
    span: Optional[Span] = None
    decl_node: Optional[Node] = None
    is_mutable: bool = False
    visibility: str = "internal"  # "pub", "private", "internal"
    is_extension_method: bool = False  # True for methods defined inside extension blocks

    def __repr__(self) -> str:
        return f"Symbol({self.name!r}, {self.kind.name}, id={self.id.id})"


@dataclass
class Scope:
    """A lexical scope containing symbol bindings."""
    kind: ScopeKind
    parent: Optional[Scope] = None
    types: Dict[str, SymbolId] = field(default_factory=dict)    # Type namespace
    values: Dict[str, SymbolId] = field(default_factory=dict)   # Value namespace

    def lookup_type(self, name: str) -> Optional[SymbolId]:
        """Look up a type in this scope and parent scopes."""
        if name in self.types:
            return self.types[name]
        if self.parent:
            return self.parent.lookup_type(name)
        return None

    def lookup_value(self, name: str) -> Optional[SymbolId]:
        """Look up a value in this scope and parent scopes."""
        if name in self.values:
            return self.values[name]
        if self.parent:
            return self.parent.lookup_value(name)
        return None

    def define_type(self, name: str, symbol_id: SymbolId) -> bool:
        """Define a type in this scope. Returns False if already defined."""
        if name in self.types:
            return False
        self.types[name] = symbol_id
        return True

    def define_value(self, name: str, symbol_id: SymbolId) -> bool:
        """Define a value in this scope. Returns False if already defined."""
        if name in self.values:
            return False
        self.values[name] = symbol_id
        return True

    def has_type_local(self, name: str) -> bool:
        """Check if a type is defined in this scope (not parents)."""
        return name in self.types

    def has_value_local(self, name: str) -> bool:
        """Check if a value is defined in this scope (not parents)."""
        return name in self.values


class SymbolTable:
    """Central registry of all symbols."""

    def __init__(self) -> None:
        self._next_id = 0
        self._next_synthetic_id = -1000
        self.symbols: Dict[SymbolId, Symbol] = {}
        self.builtins: Dict[str, SymbolId] = {}
        # Specialized -> (original symbol, concrete type args).
        # Populated by the monomorphizer; used by member resolution to
        # recover the original generic context for substitution.
        self.specialization_origin: Dict[SymbolId, tuple] = {}
        # O(1) reverse lookups to eliminate AST-scans.
        # id(decl_node) -> SymbolId for nodes that were associated with a symbol.
        self.node_to_symbol: Dict[int, SymbolId] = {}
        # name -> SymbolId for TYPE-namespace symbols (STRUCT, ENUM, PROTOCOL, BUILTIN_TYPE).
        self._type_index: Dict[str, SymbolId] = {}
        self._init_builtins()

    def record_specialization(
        self,
        specialized_id: SymbolId,
        original_id: SymbolId,
        type_args: tuple,
    ) -> None:
        """Record that ``specialized_id`` is a monomorphized instance of
        ``original_id`` with the given concrete type arguments."""
        self.specialization_origin[specialized_id] = (original_id, type_args)

    def find_specialization(
        self,
        original_id: SymbolId,
        type_args: tuple,
    ) -> Optional[SymbolId]:
        """Reverse lookup: which specialized symbol corresponds to
        (original_id, type_args), if any."""
        for spec_id, (orig, args) in self.specialization_origin.items():
            if orig == original_id and args == type_args:
                return spec_id
        return None

    def _init_builtins(self) -> None:
        """Initialize builtin types and constructors."""
        builtin_types = [
            # Signed integers
            "i8", "i16", "i32", "i64",
            # Unsigned integers
            "u8", "u16", "u32", "u64",
            # Floating point
            "f32", "f64",
            # Other primitives
            "Bool", "Void",
            # Pointer types
            "RawPtr",
            # Special types
            "Self",  # Self type for protocols
        ]

        # Builtin protocols for iteration
        builtin_protocols = [
            "Iterator",   # protocol Iterator { associatedtype Element; def __next__() -> Element?; }
            "Iterable",   # protocol Iterable { associatedtype IteratorType: Iterator; def __iter__() -> IteratorType; }
        ]
        for name in builtin_protocols:
            symbol_id = self._create_symbol_id()
            symbol = Symbol(
                id=symbol_id,
                name=name,
                kind=SymbolKind.PROTOCOL,
                namespace=Namespace.TYPE,
                is_mutable=False,
            )
            self.symbols[symbol_id] = symbol
            self.builtins[name] = symbol_id
            self._type_index[name] = symbol_id
        for name in builtin_types:
            symbol_id = self._create_symbol_id()
            symbol = Symbol(
                id=symbol_id,
                name=name,
                kind=SymbolKind.BUILTIN_TYPE,
                namespace=Namespace.TYPE,
                is_mutable=False,
            )
            self.symbols[symbol_id] = symbol
            self.builtins[name] = symbol_id
            self._type_index[name] = symbol_id

    def _create_symbol_id(self) -> SymbolId:
        """Create a new unique symbol ID."""
        symbol_id = SymbolId(self._next_id)
        self._next_id += 1
        return symbol_id

    def create_symbol(
        self,
        name: str,
        kind: SymbolKind,
        namespace: Namespace,
        span: Optional[Span] = None,
        decl_node: Optional[Node] = None,
        is_mutable: bool = False,
        visibility: str = "internal",
    ) -> Symbol:
        """Create and register a new symbol."""
        symbol_id = self._create_symbol_id()
        symbol = Symbol(
            id=symbol_id,
            name=name,
            kind=kind,
            namespace=namespace,
            span=span,
            decl_node=decl_node,
            is_mutable=is_mutable,
            visibility=visibility,
        )
        self.symbols[symbol_id] = symbol

        # Populate O(1) lookup indices.
        if decl_node is not None:
            self.node_to_symbol[id(decl_node)] = symbol_id
        if kind in (SymbolKind.STRUCT, SymbolKind.ENUM, SymbolKind.PROTOCOL, SymbolKind.BUILTIN_TYPE, SymbolKind.GENERIC_PARAM):
            self._type_index[name] = symbol_id

        return symbol

    def get_symbol(self, symbol_id: SymbolId) -> Optional[Symbol]:
        """Get a symbol by its ID."""
        return self.symbols.get(symbol_id)

    def get_builtin(self, name: str) -> Optional[SymbolId]:
        """Get a builtin type symbol ID by name."""
        return self.builtins.get(name)

    def get_type_symbol(self, name: str) -> Optional[SymbolId]:
        """Get a type symbol (STRUCT, ENUM, PROTOCOL, BUILTIN_TYPE) by name in O(1)."""
        return self._type_index.get(name)

    def get_symbol_by_node(self, node: object) -> Optional[SymbolId]:
        """Get the symbol ID associated with an AST node in O(1)."""
        return self.node_to_symbol.get(id(node))

    def create_synthetic_symbol_id(self) -> SymbolId:
        """Create a synthetic (negative) SymbolId for generated constructs.

        Used for async frame structs and other compiler-generated entities
        that live in the TypeTable but not in the SymbolTable.
        """
        sid = SymbolId(self._next_synthetic_id)
        self._next_synthetic_id -= 1
        return sid


class ResolutionErrorKind(Enum):
    """Kind of resolution error."""
    UNDEFINED_VALUE = auto()
    UNDEFINED_TYPE = auto()
    DUPLICATE_VALUE = auto()
    DUPLICATE_TYPE = auto()


@dataclass
class ResolutionError:
    """An error during name resolution."""
    kind: ResolutionErrorKind
    name: str
    message: str
    span: Optional[Span] = None

    def __str__(self) -> str:
        location = ""
        if self.span:
            location = f" at line {self.span.line}, column {self.span.column}"
        return f"{self.kind.name}: {self.message}{location}"


@dataclass
class ResolutionResult:
    """Result of name resolution."""
    symbol_table: SymbolTable
    node_symbols: Dict[int, SymbolId]  # id(node) -> SymbolId
    errors: List[ResolutionError]
    self_symbols: Dict[SymbolId, SymbolId] = field(default_factory=dict)  # func_symbol -> self_symbol
    imported_symbols: Dict[str, SymbolId] = field(default_factory=dict)  # name -> symbol_id for imports
    # (type_name, method_name, method_symbol_id, visibility)
    # `visibility` is the visibility of the *extension block* declaring the
    # method ("pub" / "internal" / "private"). Methods from non-pub extensions
    # are not exposed across module boundaries — see ExtensionExport in
    # module.py and the filter in NameResolver._resolve_import.
    extension_methods: List[tuple[str, str, SymbolId, str]] = field(default_factory=list)
    imported_extension_methods: Dict[str, List[tuple[str, SymbolId]]] = field(default_factory=dict)  # type_name -> [(method_name, symbol_id)]
    # Symbols pulled in via `pub import "x.rl"` and meant to be re-exported
    # to anyone who imports *this* module. Populated by NameResolver and
    # merged into the owning Module's `exports` by the driver. The values
    # carry the original symbol's name, id, and kind.
    re_exports: List[tuple[str, SymbolId, str]] = field(default_factory=list)
    # Extension exports pulled in via `pub import` (re-exported across).
    re_exported_extension_methods: List[tuple[str, str, SymbolId, str]] = field(default_factory=list)

    def has_errors(self) -> bool:
        """Check if there are any resolution errors."""
        return len(self.errors) > 0

    def get_symbol_for_node(self, node: Node) -> Optional[SymbolId]:
        """Get the symbol ID associated with an AST node."""
        return self.node_symbols.get(id(node))
