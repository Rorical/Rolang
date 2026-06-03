"""Type representation and type table for RoLang type checking."""

from __future__ import annotations
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional, Dict, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from .symbols import SymbolId, SymbolTable


@dataclass(frozen=True)
class TypeId:
    """Unique identifier for a type. Types are interned in TypeTable."""
    id: int

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, TypeId):
            return self.id == other.id
        return False

    def __repr__(self) -> str:
        return f"TypeId({self.id})"


class TypeKind(Enum):
    """Kind of type."""
    PRIMITIVE = auto()       # i32, f64, Bool, Void
    STRUCT = auto()          # Named or anonymous (tuple-backed) struct
    ENUM = auto()            # User-defined enum
    FUNCTION = auto()        # (Args) -> Return
    CLOSURE = auto()         # Closure with captured environment
    OPTIONAL = auto()        # T?
    PROTOCOL = auto()        # Protocol definition
    EXISTENTIAL = auto()     # any Protocol (dynamic dispatch)
    TYPE_VARIABLE = auto()   # Unresolved during inference
    ERROR = auto()           # Sentinel for error recovery
    NEVER = auto()           # Bottom type (never returns)


class PrimitiveType(Enum):
    """Primitive type kinds."""
    # Signed integers
    I8 = "i8"
    I16 = "i16"
    I32 = "i32"
    I64 = "i64"
    # Unsigned integers
    U8 = "u8"
    U16 = "u16"
    U32 = "u32"
    U64 = "u64"
    # Floating point
    F32 = "f32"
    F64 = "f64"
    # Other primitives
    BOOL = "Bool"
    VOID = "Void"
    # Pointer types
    RAW_PTR = "RawPtr"


# ========================= Type Data Classes =========================

@dataclass(frozen=True)
class PrimitiveTypeData:
    """Data for a primitive type."""
    primitive: PrimitiveType


@dataclass(frozen=True)
class StructTypeData:
    """Data for a struct type.

    Named structs have a non-None ``symbol_id``; anonymous structs (tuples,
    compiler-generated aggregates) have ``symbol_id=None`` and store their
    field list in ``anon_fields`` as ``((label_or_None, TypeId), ...)`` where
    the positional label defaults to the string representation of the index.
    """
    symbol_id: Optional[SymbolId]
    type_args: Tuple[TypeId, ...] = ()
    anon_fields: Optional[Tuple[Tuple[Optional[str], TypeId], ...]] = None


@dataclass(frozen=True)
class EnumTypeData:
    """Data for an enum type (possibly instantiated generic)."""
    symbol_id: SymbolId
    type_args: Tuple[TypeId, ...] = ()


@dataclass(frozen=True)
class FunctionTypeData:
    """Data for a function type."""
    params: Tuple[TypeId, ...]
    return_type: TypeId
    is_async: bool = False


@dataclass(frozen=True)
class ClosureTypeData:
    """Data for a closure type (function + captured environment)."""
    params: Tuple[TypeId, ...]
    return_type: TypeId
    captures: Tuple[TypeId, ...]  # Types of captured variables
    is_async: bool = False


@dataclass(frozen=True)
class FuncRequirement:
    """A function requirement in a protocol.

    Methods can update `self` through its heap reference, so protocol method
    requirements do not distinguish receiver update modes.
    """
    name: str
    params: Tuple[TypeId, ...]
    return_type: TypeId
    is_async: bool = False
    is_static: bool = False


@dataclass(frozen=True)
class PropRequirement:
    """A property requirement in a protocol."""
    name: str
    type_id: TypeId
    has_getter: bool = True
    has_setter: bool = False


@dataclass(frozen=True)
class ProtocolTypeData:
    """Data for a protocol type."""
    symbol_id: SymbolId
    func_requirements: Tuple[FuncRequirement, ...] = ()
    prop_requirements: Tuple[PropRequirement, ...] = ()


@dataclass(frozen=True)
class ExistentialTypeData:
    """Data for an existential type (any Protocol)."""
    protocol_id: TypeId  # The protocol this existential wraps


@dataclass(frozen=True)
class OptionalTypeData:
    """Data for an optional type."""
    inner: TypeId


@dataclass(frozen=True)
class TypeVariableData:
    """Data for a type variable (during inference)."""
    name: str
    id: int  # Unique ID for this type variable
    bounds: Tuple[TypeId, ...] = ()  # Protocol type IDs the variable conforms to


@dataclass(frozen=True)
class ErrorTypeData:
    """Data for the error sentinel type."""
    pass


@dataclass
class TypeDescriptorEntry:
    """Metadata for GC tracing of a struct/enum type.

    Maps field names to their byte offsets and whether they contain
    heap pointers that the GC must trace.
    """
    type_id: TypeId
    payload_size: int          # Size of the data after the ObjHeader
    fields: List["FieldDesc"]  # Field descriptors

    def pointer_fields(self) -> List["FieldDesc"]:
        """Return only fields that contain heap pointers."""
        return [f for f in self.fields if f.is_pointer]


@dataclass
class FieldDesc:
    """Descriptor for one field of a type."""
    name: str
    offset: int         # Byte offset within the payload
    is_pointer: bool    # True if this field holds a heap-object pointer
    inner_type_id: TypeId  # Type that this field holds


@dataclass(frozen=True)
class NeverTypeData:
    """Data for the never (bottom) type."""
    pass


# Union of all type data
TypeData = (
    PrimitiveTypeData | StructTypeData | EnumTypeData |
    FunctionTypeData | ClosureTypeData |
    OptionalTypeData |
    ProtocolTypeData | ExistentialTypeData | TypeVariableData |
    ErrorTypeData | NeverTypeData
)


@dataclass
class TypeInfo:
    """Complete type information."""
    id: TypeId
    kind: TypeKind
    data: TypeData

    def __repr__(self) -> str:
        return f"TypeInfo({self.kind.name}, {self.data})"


# ========================= Type Table =========================

class TypeTable:
    """Central registry for all types. Types are interned for efficient comparison."""

    def __init__(self) -> None:
        self._next_id = 0
        self._next_type_var_id = 0
        self.types: Dict[TypeId, TypeInfo] = {}
        self.builtins: Dict[str, TypeId] = {}

        # Optional SymbolTable for diagnostics; when set, format_type renders
        # struct/enum/protocol types by their source-level names instead of
        # opaque `struct#N` / `enum#N` / `protocol#N` placeholders. The checker
        # wires this in `Checker.__init__`.
        self._symbol_table: Optional["SymbolTable"] = None

        # Intern cache to avoid duplicate types
        self._intern_cache: Dict[Tuple[TypeKind, TypeData], TypeId] = {}

        # Pre-resolved member cache: base SymbolId -> TypeMembers
        # Populated by the checker; used by MemberResolver to avoid AST access.
        self._member_cache: Dict["SymbolId", "TypeMembers"] = {}

        # Generic param names for structs/enums: SymbolId -> (name, ...)
        self._generic_param_names: Dict["SymbolId", Tuple[str, ...]] = {}

        # Type descriptor registry for GC tracing
        # Maps TypeId -> TypeDescriptorEntry
        self._descriptors: Dict[TypeId, TypeDescriptorEntry] = {}

        # Special type IDs
        self.error_type: TypeId = TypeId(-1)  # Will be set in _init_builtins
        self.never_type: TypeId = TypeId(-1)
        self.void_type: TypeId = TypeId(-1)
        self.nil_type: TypeId = TypeId(-1)

        self._init_builtins()

    def _create_type_id(self) -> TypeId:
        """Create a new unique type ID."""
        type_id = TypeId(self._next_id)
        self._next_id += 1
        return type_id

    def _init_builtins(self) -> None:
        """Initialize builtin types."""
        # Create primitive types
        for prim in PrimitiveType:
            type_id = self._create_type_id()
            data = PrimitiveTypeData(primitive=prim)
            self.types[type_id] = TypeInfo(id=type_id, kind=TypeKind.PRIMITIVE, data=data)
            self.builtins[prim.value] = type_id
            self._intern_cache[(TypeKind.PRIMITIVE, data)] = type_id

            # Track special types
            if prim == PrimitiveType.VOID:
                self.void_type = type_id

        # Create error sentinel type
        self.error_type = self._create_type_id()
        error_data = ErrorTypeData()
        self.types[self.error_type] = TypeInfo(
            id=self.error_type,
            kind=TypeKind.ERROR,
            data=error_data
        )
        self._intern_cache[(TypeKind.ERROR, error_data)] = self.error_type

        # Create never type
        self.never_type = self._create_type_id()
        never_data = NeverTypeData()
        self.types[self.never_type] = TypeInfo(
            id=self.never_type,
            kind=TypeKind.NEVER,
            data=never_data
        )
        self._intern_cache[(TypeKind.NEVER, never_data)] = self.never_type

        # Create nil type (assignable only to optionals)
        self.nil_type = self._create_type_id()
        nil_data = TypeVariableData(name="__nil", id=self.nil_type.id)
        self.types[self.nil_type] = TypeInfo(
            id=self.nil_type,
            kind=TypeKind.TYPE_VARIABLE,
            data=nil_data
        )
        self._intern_cache[(TypeKind.TYPE_VARIABLE, nil_data)] = self.nil_type

    def get_type(self, type_id: TypeId) -> Optional[TypeInfo]:
        """Get type information by ID."""
        return self.types.get(type_id)

    def attach_symbol_table(self, symbol_table: "SymbolTable") -> None:
        """Wire a SymbolTable for human-readable diagnostics in format_type."""
        self._symbol_table = symbol_table

    def _symbol_name(self, symbol_id: Optional["SymbolId"], fallback_prefix: str) -> str:
        """Return the source-level name of a symbol or a unique placeholder."""
        if symbol_id is None:
            return f"{fallback_prefix}#anon"
        if self._symbol_table is not None:
            sym = self._symbol_table.get_symbol(symbol_id)
            if sym is not None and sym.name:
                return sym.name
        return f"{fallback_prefix}#{symbol_id.id}"

    def get_builtin(self, name: str) -> Optional[TypeId]:
        """Get a builtin type by name."""
        return self.builtins.get(name)

    def is_error(self, type_id: TypeId) -> bool:
        """Check if a type is the error sentinel."""
        return type_id == self.error_type

    def is_never(self, type_id: TypeId) -> bool:
        """Check if a type is the never type."""
        return type_id == self.never_type

    # ========================= Type Construction =========================

    def _intern_type(self, kind: TypeKind, data: TypeData) -> TypeId:
        """Get or create an interned type."""
        key = (kind, data)
        if key in self._intern_cache:
            return self._intern_cache[key]

        type_id = self._create_type_id()
        self.types[type_id] = TypeInfo(id=type_id, kind=kind, data=data)
        self._intern_cache[key] = type_id
        return type_id

    def make_struct(self, symbol_id: SymbolId, type_args: Tuple[TypeId, ...] = ()) -> TypeId:
        """Create or get a struct type."""
        data = StructTypeData(symbol_id=symbol_id, type_args=type_args)
        return self._intern_type(TypeKind.STRUCT, data)

    def make_enum(self, symbol_id: SymbolId, type_args: Tuple[TypeId, ...] = ()) -> TypeId:
        """Create or get an enum type."""
        data = EnumTypeData(symbol_id=symbol_id, type_args=type_args)
        return self._intern_type(TypeKind.ENUM, data)

    # ---- Member cache (pre-resolved struct/enum members) ----

    def set_type_members(self, symbol_id: SymbolId, members) -> None:
        """Store pre-resolved field and method info for a struct/enum symbol."""
        self._member_cache[symbol_id] = members

    def get_type_members(self, symbol_id: SymbolId):
        """Retrieve pre-resolved members for a struct/enum symbol, or None."""
        return self._member_cache.get(symbol_id)

    def set_generic_param_names(self, symbol_id: SymbolId, names: Tuple[str, ...]) -> None:
        """Store the generic parameter names for a struct/enum symbol."""
        self._generic_param_names[symbol_id] = names

    def get_generic_param_names(self, symbol_id: SymbolId) -> Tuple[str, ...]:
        """Retrieve generic parameter names for a struct/enum symbol."""
        return self._generic_param_names.get(symbol_id, ())

    def make_tuple(self, elements: Tuple[Tuple[Optional[str], TypeId], ...]) -> TypeId:
        """Create or get a tuple type as an anonymous struct.

        Each element label is used as the field name when present; otherwise
        the field name is the string representation of its zero-based index.
        """
        named = tuple(
            (label if label is not None else str(i), t)
            for i, (label, t) in enumerate(elements)
        )
        data = StructTypeData(symbol_id=None, type_args=(), anon_fields=named)
        return self._intern_type(TypeKind.STRUCT, data)

    def make_function(
        self,
        params: Tuple[TypeId, ...],
        return_type: TypeId,
        is_async: bool = False
    ) -> TypeId:
        """Create or get a function type."""
        data = FunctionTypeData(params=params, return_type=return_type, is_async=is_async)
        return self._intern_type(TypeKind.FUNCTION, data)

    def make_optional(self, inner: TypeId) -> TypeId:
        """Create or get an optional T? type."""
        data = OptionalTypeData(inner=inner)
        return self._intern_type(TypeKind.OPTIONAL, data)

    def make_type_variable(
        self,
        name: str,
        bounds: Tuple[TypeId, ...] = (),
    ) -> TypeId:
        """Create a fresh type variable, optionally with protocol bounds."""
        var_id = self._next_type_var_id
        self._next_type_var_id += 1
        data = TypeVariableData(name=name, id=var_id, bounds=bounds)
        # Type variables are NOT interned - each is unique
        type_id = self._create_type_id()
        self.types[type_id] = TypeInfo(id=type_id, kind=TypeKind.TYPE_VARIABLE, data=data)
        return type_id

    def make_closure(
        self,
        params: Tuple[TypeId, ...],
        return_type: TypeId,
        captures: Tuple[TypeId, ...],
        is_async: bool = False
    ) -> TypeId:
        """Create or get a closure type."""
        data = ClosureTypeData(
            params=params,
            return_type=return_type,
            captures=captures,
            is_async=is_async
        )
        return self._intern_type(TypeKind.CLOSURE, data)

    def make_protocol(
        self,
        symbol_id: SymbolId,
        func_requirements: Tuple[FuncRequirement, ...] = (),
        prop_requirements: Tuple[PropRequirement, ...] = ()
    ) -> TypeId:
        """Create or get a protocol type."""
        data = ProtocolTypeData(
            symbol_id=symbol_id,
            func_requirements=func_requirements,
            prop_requirements=prop_requirements
        )
        return self._intern_type(TypeKind.PROTOCOL, data)

    def make_existential(self, protocol_id: TypeId) -> TypeId:
        """Create or get an existential type (any Protocol)."""
        data = ExistentialTypeData(protocol_id=protocol_id)
        return self._intern_type(TypeKind.EXISTENTIAL, data)

    # ========================= Type Queries =========================

    def is_integer(self, type_id: TypeId) -> bool:
        """Check if a type is an integer type."""
        info = self.get_type(type_id)
        if info is None or info.kind != TypeKind.PRIMITIVE:
            return False
        data = info.data
        if not isinstance(data, PrimitiveTypeData):
            return False
        return data.primitive in {
            PrimitiveType.I8, PrimitiveType.I16, PrimitiveType.I32, PrimitiveType.I64,
            PrimitiveType.U8, PrimitiveType.U16, PrimitiveType.U32, PrimitiveType.U64,
        }

    def is_signed_integer(self, type_id: TypeId) -> bool:
        """Check if a type is a signed integer type."""
        info = self.get_type(type_id)
        if info is None or info.kind != TypeKind.PRIMITIVE:
            return False
        data = info.data
        if not isinstance(data, PrimitiveTypeData):
            return False
        return data.primitive in {
            PrimitiveType.I8, PrimitiveType.I16, PrimitiveType.I32, PrimitiveType.I64,
        }

    def is_float(self, type_id: TypeId) -> bool:
        """Check if a type is a floating point type."""
        info = self.get_type(type_id)
        if info is None or info.kind != TypeKind.PRIMITIVE:
            return False
        data = info.data
        if not isinstance(data, PrimitiveTypeData):
            return False
        return data.primitive in {PrimitiveType.F32, PrimitiveType.F64}

    def is_numeric(self, type_id: TypeId) -> bool:
        """Check if a type is numeric (integer or float)."""
        return self.is_integer(type_id) or self.is_float(type_id)

    def can_widen_int(self, source: TypeId, target: TypeId) -> bool:
        """Check if source integer type can be widened to target integer type."""
        src_info = self.get_type(source)
        tgt_info = self.get_type(target)
        if src_info is None or tgt_info is None:
            return False
        if src_info.kind != TypeKind.PRIMITIVE or tgt_info.kind != TypeKind.PRIMITIVE:
            return False
        src_data = src_info.data
        tgt_data = tgt_info.data
        if not isinstance(src_data, PrimitiveTypeData) or not isinstance(tgt_data, PrimitiveTypeData):
            return False

        # Define bit sizes
        sizes = {
            PrimitiveType.I8: 8, PrimitiveType.U8: 8,
            PrimitiveType.I16: 16, PrimitiveType.U16: 16,
            PrimitiveType.I32: 32, PrimitiveType.U32: 32,
            PrimitiveType.I64: 64, PrimitiveType.U64: 64,
        }
        signed = {PrimitiveType.I8, PrimitiveType.I16, PrimitiveType.I32, PrimitiveType.I64}

        src_prim = src_data.primitive
        tgt_prim = tgt_data.primitive

        if src_prim not in sizes or tgt_prim not in sizes:
            return False

        src_size = sizes[src_prim]
        tgt_size = sizes[tgt_prim]
        src_signed = src_prim in signed
        tgt_signed = tgt_prim in signed

        # Allow widening to larger size of same signedness
        if src_signed == tgt_signed and src_size < tgt_size:
            return True
        # Allow unsigned to signed of strictly larger size
        if not src_signed and tgt_signed and src_size < tgt_size:
            return True
        return False

    def is_bool(self, type_id: TypeId) -> bool:
        """Check if a type is Bool."""
        info = self.get_type(type_id)
        if info is None or info.kind != TypeKind.PRIMITIVE:
            return False
        data = info.data
        if not isinstance(data, PrimitiveTypeData):
            return False
        return data.primitive == PrimitiveType.BOOL

    def is_string(self, type_id: TypeId) -> bool:
        """Check if a type is String."""
        info = self.get_type(type_id)
        if info is None or info.kind != TypeKind.STRUCT:
            return False
        return self.format_type(type_id).startswith("String")

    def is_optional(self, type_id: TypeId) -> bool:
        """Check if a type is optional."""
        info = self.get_type(type_id)
        return info is not None and info.kind == TypeKind.OPTIONAL

    def get_optional_inner(self, type_id: TypeId) -> Optional[TypeId]:
        """Get the inner type of an optional, or None if not optional."""
        info = self.get_type(type_id)
        if info is None or info.kind != TypeKind.OPTIONAL:
            return None
        data = info.data
        if not isinstance(data, OptionalTypeData):
            return None
        return data.inner

    # ========================= Type Descriptors =========================

    def set_descriptor(self, type_id: TypeId, desc: TypeDescriptorEntry) -> None:
        """Register a type descriptor for GC tracing."""
        self._descriptors[type_id] = desc

    def get_descriptor(self, type_id: TypeId) -> Optional[TypeDescriptorEntry]:
        """Get the type descriptor for a type, or None."""
        return self._descriptors.get(type_id)

    def get_all_descriptors(self) -> Dict[TypeId, TypeDescriptorEntry]:
        """Get all registered type descriptors."""
        return dict(self._descriptors)

    def is_heap_type(self, type_id: TypeId) -> bool:
        """Check if a type is heap-allocated."""
        info = self.get_type(type_id)
        if info is None:
            return False
        return info.kind in (
            TypeKind.STRUCT,
            TypeKind.ENUM,
            TypeKind.CLOSURE,
            TypeKind.EXISTENTIAL,
        )

    def runtime_type_id(self, type_id: TypeId) -> int:
        """Return collection runtime metadata for a type.

        Runtime containers currently need a non-zero marker for elements that
        are heap references so they can retain/release copied slots. Zero means
        byte-only storage with no ARC hooks.
        """
        if self.is_heap_type(type_id):
            return 1
        inner = self.get_optional_inner(type_id)
        if inner is not None and self.is_heap_type(inner):
            return 1
        return 0

    def is_function(self, type_id: TypeId) -> bool:
        """Check if a type is a function type."""
        info = self.get_type(type_id)
        return info is not None and info.kind == TypeKind.FUNCTION

    def get_function_data(self, type_id: TypeId) -> Optional[FunctionTypeData]:
        """Get function type data, or None if not a function."""
        info = self.get_type(type_id)
        if info is None or info.kind != TypeKind.FUNCTION:
            return None
        data = info.data
        if not isinstance(data, FunctionTypeData):
            return None
        return data

    def is_closure(self, type_id: TypeId) -> bool:
        """Check if a type is a closure type."""
        info = self.get_type(type_id)
        return info is not None and info.kind == TypeKind.CLOSURE

    def get_closure_data(self, type_id: TypeId) -> Optional[ClosureTypeData]:
        """Get closure type data, or None if not a closure."""
        info = self.get_type(type_id)
        if info is None or info.kind != TypeKind.CLOSURE:
            return None
        data = info.data
        if not isinstance(data, ClosureTypeData):
            return None
        return data

    def is_callable(self, type_id: TypeId) -> bool:
        """Check if a type is callable (function or closure)."""
        info = self.get_type(type_id)
        return info is not None and info.kind in (TypeKind.FUNCTION, TypeKind.CLOSURE)

    def is_protocol(self, type_id: TypeId) -> bool:
        """Check if a type is a protocol type."""
        info = self.get_type(type_id)
        return info is not None and info.kind == TypeKind.PROTOCOL

    def get_protocol_data(self, type_id: TypeId) -> Optional[ProtocolTypeData]:
        """Get protocol type data, or None if not a protocol."""
        info = self.get_type(type_id)
        if info is None or info.kind != TypeKind.PROTOCOL:
            return None
        data = info.data
        if not isinstance(data, ProtocolTypeData):
            return None
        return data

    def is_existential(self, type_id: TypeId) -> bool:
        """Check if a type is an existential type."""
        info = self.get_type(type_id)
        return info is not None and info.kind == TypeKind.EXISTENTIAL

    def get_existential_data(self, type_id: TypeId) -> Optional[ExistentialTypeData]:
        """Get existential type data, or None if not existential."""
        info = self.get_type(type_id)
        if info is None or info.kind != TypeKind.EXISTENTIAL:
            return None
        data = info.data
        if not isinstance(data, ExistentialTypeData):
            return None
        return data

    def get_protocol_type(self, symbol_id: SymbolId) -> Optional[TypeId]:
        """Get or create the protocol type for a symbol."""
        # Check if we already have this protocol type
        for type_id, info in self.types.items():
            if info.kind == TypeKind.PROTOCOL:
                data = info.data
                if isinstance(data, ProtocolTypeData) and data.symbol_id == symbol_id:
                    return type_id

        # Create a new protocol type
        return self.make_protocol(symbol_id)

    # ========================= Type Formatting =========================

    def format_type(self, type_id: TypeId) -> str:
        """Format a type for display."""
        info = self.get_type(type_id)
        if info is None:
            return "<unknown>"

        if info.kind == TypeKind.PRIMITIVE:
            data = info.data
            if isinstance(data, PrimitiveTypeData):
                return data.primitive.value
        elif info.kind == TypeKind.STRUCT:
            data = info.data
            if isinstance(data, StructTypeData):
                if data.symbol_id is None:
                    # Anonymous struct (tuple) — display as (T0, T1, ...)
                    fields = data.anon_fields or ()
                    elems = []
                    for fname, t in fields:
                        # Numeric-looking names are positional; just show the type.
                        if fname.isdigit():
                            elems.append(self.format_type(t))
                        else:
                            elems.append(f"{fname}: {self.format_type(t)}")
                    return f"({', '.join(elems)})"
                name = self._symbol_name(data.symbol_id, "struct")
                if data.type_args:
                    args = ", ".join(self.format_type(t) for t in data.type_args)
                    return f"{name}<{args}>"
                return name
        elif info.kind == TypeKind.ENUM:
            data = info.data
            if isinstance(data, EnumTypeData):
                name = self._symbol_name(data.symbol_id, "enum")
                if data.type_args:
                    args = ", ".join(self.format_type(t) for t in data.type_args)
                    return f"{name}<{args}>"
                return name
        elif info.kind == TypeKind.FUNCTION:
            data = info.data
            if isinstance(data, FunctionTypeData):
                params = ", ".join(self.format_type(p) for p in data.params)
                ret = self.format_type(data.return_type)
                async_prefix = "async " if data.is_async else ""
                return f"{async_prefix}({params}) -> {ret}"
        elif info.kind == TypeKind.OPTIONAL:
            data = info.data
            if isinstance(data, OptionalTypeData):
                return f"{self.format_type(data.inner)}?"
        elif info.kind == TypeKind.CLOSURE:
            data = info.data
            if isinstance(data, ClosureTypeData):
                params = ", ".join(self.format_type(p) for p in data.params)
                ret = self.format_type(data.return_type)
                captures = ", ".join(self.format_type(c) for c in data.captures)
                async_prefix = "async " if data.is_async else ""
                return f"{async_prefix}closure({params}) -> {ret} [captures: {captures}]"
        elif info.kind == TypeKind.PROTOCOL:
            data = info.data
            if isinstance(data, ProtocolTypeData):
                return self._symbol_name(data.symbol_id, "protocol")
        elif info.kind == TypeKind.EXISTENTIAL:
            data = info.data
            if isinstance(data, ExistentialTypeData):
                proto = self.format_type(data.protocol_id)
                return f"any {proto}"
        elif info.kind == TypeKind.TYPE_VARIABLE:
            data = info.data
            if isinstance(data, TypeVariableData):
                return f"${data.name}"
        elif info.kind == TypeKind.ERROR:
            return "<error>"
        elif info.kind == TypeKind.NEVER:
            return "Never"

        return "<unknown>"
