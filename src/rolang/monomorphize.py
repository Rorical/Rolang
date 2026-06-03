"""
Monomorphization Phase - Transforms generic HIR into specialized HIR.

This phase instantiates generic functions, structs, and enums with concrete
types, producing a monomorphized HIR where no generics remain.

Pipeline position:
    HIR (typed, with generics)
            ↓
      Monomorphization  ← THIS PHASE
            ↓
    HIR (monomorphized, no generics)
            ↓
           MIR
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Set, Tuple, Union
from collections import deque

from . import ast
from .symbols import SymbolTable, SymbolId, SymbolKind, Namespace
from .types import (
    TypeId, TypeKind, TypeTable,
    StructTypeData, EnumTypeData,
    FunctionTypeData, OptionalTypeData,
    TypeVariableData,
    ClosureTypeData, ExistentialTypeData, ProtocolTypeData,
)
from .hir import (
    HirProgram, HirItem,
    HirFunction, HirExternFunc, HirParam,
    HirStruct, HirField, HirEnum, HirEnumCase,
    HirProtocol, HirExtension,
    HirStmt, HirBlock, HirVarDecl, HirAssign, HirExprStmt, HirReturn,
    HirBreak, HirContinue, HirIf, HirIfLet, HirGuard,
    HirWhile, HirFor, HirSwitchCase, HirSwitch, HirDefer,
    HirExpr, HirLiteral, HirVar, HirBinaryOp, HirUnaryOp, HirTernary,
    HirCall, HirMethodCall, HirFieldAccess, HirSubscript,
    HirTuple, HirArray, HirDict, HirLambda, HirClone,
    HirStructInit, HirEnumConstruct, HirCast, HirTypeCheck, HirTryExpr,
    HirOptionalSome, HirOptionalNone, HirOptionalMatch,
    HirPattern, HirWildcardPattern, HirBindingPattern, HirLiteralPattern,
    HirTuplePattern, HirEnumCasePattern, HirOrPattern,
)
from .hir_builder import HirBuildResult
from .layout import LayoutService


# ========================= Core Data Structures =========================

@dataclass(frozen=True)
class InstanceKey:
    """
    Unique identifier for a monomorphized instance.
    Combines the original symbol with concrete type arguments.
    """
    symbol_id: SymbolId
    type_args: Tuple[TypeId, ...]

    def __hash__(self) -> int:
        return hash((self.symbol_id, self.type_args))


@dataclass
class TypeSubstitution:
    """
    Maps generic type parameter names to concrete types.
    Used to transform types during specialization.
    """
    mapping: Dict[str, TypeId] = field(default_factory=dict)

    def apply(self, type_id: TypeId, type_table: TypeTable) -> TypeId:
        """Apply the substitution to a type, returning the specialized type."""
        info = type_table.get_type(type_id)
        if info is None:
            return type_id

        kind = info.kind
        data = info.data

        # Type variable - look up in substitution
        if kind == TypeKind.TYPE_VARIABLE:
            if isinstance(data, TypeVariableData):
                if data.name in self.mapping:
                    return self.mapping[data.name]
            return type_id

        # Primitive types - no substitution needed
        if kind == TypeKind.PRIMITIVE:
            return type_id

        # Error/Never types - no substitution
        if kind in (TypeKind.ERROR, TypeKind.NEVER):
            return type_id

        # Struct type - recurse into type args (or anon_fields for tuples)
        if kind == TypeKind.STRUCT:
            if isinstance(data, StructTypeData):
                if data.symbol_id is None:
                    # Anonymous struct (tuple): recurse into element types
                    fields = data.anon_fields or ()
                    new_fields = tuple(
                        (fname, self.apply(t, type_table)) for fname, t in fields
                    )
                    return type_table.make_tuple(new_fields)
                new_args = tuple(self.apply(arg, type_table) for arg in data.type_args)
                return type_table.make_struct(data.symbol_id, new_args)
            return type_id

        # Enum type - recurse into type args
        if kind == TypeKind.ENUM:
            if isinstance(data, EnumTypeData):
                new_args = tuple(self.apply(arg, type_table) for arg in data.type_args)
                return type_table.make_enum(data.symbol_id, new_args)
            return type_id

        # Function type - recurse into params and return type
        if kind == TypeKind.FUNCTION:
            if isinstance(data, FunctionTypeData):
                new_params = tuple(self.apply(p, type_table) for p in data.params)
                new_return = self.apply(data.return_type, type_table)
                return type_table.make_function(new_params, new_return, data.is_async)
            return type_id

        # Optional type - recurse into inner type
        if kind == TypeKind.OPTIONAL:
            if isinstance(data, OptionalTypeData):
                new_inner = self.apply(data.inner, type_table)
                return type_table.make_optional(new_inner)
            return type_id

        return type_id

    def is_empty(self) -> bool:
        """Check if substitution is empty (no mappings)."""
        return len(self.mapping) == 0


@dataclass
class FunctionInstance:
    """A specialized instance of a generic function."""
    key: InstanceKey
    original_func: HirFunction
    specialized_func: HirFunction
    mangled_name: str


@dataclass
class StructInstance:
    """A specialized instance of a generic struct."""
    key: InstanceKey
    original_struct: HirStruct
    specialized_struct: HirStruct
    mangled_name: str


@dataclass
class EnumInstance:
    """A specialized instance of a generic enum."""
    key: InstanceKey
    original_enum: HirEnum
    specialized_enum: HirEnum
    mangled_name: str


@dataclass
class MonomorphizationResult:
    """Result of the monomorphization phase."""
    program: HirProgram
    type_table: TypeTable
    symbol_table: SymbolTable
    function_instances: Dict[InstanceKey, FunctionInstance]
    struct_instances: Dict[InstanceKey, StructInstance]
    enum_instances: Dict[InstanceKey, EnumInstance]
    errors: List[str]

    def has_errors(self) -> bool:
        return len(self.errors) > 0


# ========================= Name Mangling =========================

def mangle_name(base_name: str, type_args: Tuple[TypeId, ...], type_table: TypeTable) -> str:
    """
    Generate a unique mangled name for a monomorphized instance.

    Examples:
        identity<i32>         -> identity_i32
        Box<String>           -> Box_String
        Dict<String, i32>     -> Dict_String_i32
        Option<Point>         -> Option_Point
    """
    if not type_args:
        return base_name

    parts = [base_name]
    for arg in type_args:
        parts.append(_mangle_type(arg, type_table))

    return "_".join(parts)


def _mangle_type(type_id: TypeId, type_table: TypeTable) -> str:
    """Generate a mangled name component for a type."""
    info = type_table.get_type(type_id)
    if info is None:
        return "unknown"

    kind = info.kind
    data = info.data

    if kind == TypeKind.PRIMITIVE:
        return type_table.format_type(type_id).replace(" ", "_")

    if kind == TypeKind.STRUCT:
        if isinstance(data, StructTypeData):
            # Anonymous struct (tuple) — mangle by element types so two
            # equal-shape tuples in different functions get the same name.
            if data.symbol_id is None:
                fields = data.anon_fields or ()
                elems = "_".join(_mangle_type(t, type_table) for _, t in fields)
                return f"Tuple_{elems}" if elems else "Tuple"
            base = f"S{data.symbol_id.id}"
            if data.type_args:
                args = "_".join(_mangle_type(a, type_table) for a in data.type_args)
                return f"{base}_{args}"
            return base
        return "struct"

    if kind == TypeKind.ENUM:
        if isinstance(data, EnumTypeData):
            base = f"E{data.symbol_id.id}"
            if data.type_args:
                args = "_".join(_mangle_type(a, type_table) for a in data.type_args)
                return f"{base}_{args}"
            return base
        return "enum"

    if kind == TypeKind.OPTIONAL:
        if isinstance(data, OptionalTypeData):
            return f"Opt_{_mangle_type(data.inner, type_table)}"
        return "opt"

    if kind == TypeKind.FUNCTION:
        # Mangle by full signature so two distinct function-typed type
        # arguments (e.g. (i32)->Void vs (f64)->Bool) get distinct symbols.
        if isinstance(data, FunctionTypeData):
            params = "_".join(_mangle_type(p, type_table) for p in data.params)
            ret = _mangle_type(data.return_type, type_table)
            prefix = "afn" if data.is_async else "fn"
            return f"{prefix}_{params}_to_{ret}" if params else f"{prefix}__to_{ret}"
        return "fn"

    if kind == TypeKind.CLOSURE:
        if isinstance(data, ClosureTypeData):
            params = "_".join(_mangle_type(p, type_table) for p in data.params)
            ret = _mangle_type(data.return_type, type_table)
            caps = "_".join(_mangle_type(c, type_table) for c in data.captures)
            prefix = "aclo" if data.is_async else "clo"
            return f"{prefix}_{params}_to_{ret}_c{caps}"
        return "closure"

    if kind == TypeKind.EXISTENTIAL:
        # Distinguish `any P` by the protocol it wraps so Box<any A> and
        # Box<any B> don't collide on a shared "unknown" mangling.
        if isinstance(data, ExistentialTypeData):
            return f"any_{_mangle_type(data.protocol_id, type_table)}"
        return "existential"

    if kind == TypeKind.PROTOCOL:
        if isinstance(data, ProtocolTypeData):
            return f"P{data.symbol_id.id}"
        return "protocol"

    if kind == TypeKind.TYPE_VARIABLE:
        if isinstance(data, TypeVariableData):
            return f"T{data.name}"
        return "typevar"

    return "unknown"


# ========================= Monomorphizer =========================

class Monomorphizer:
    """
    Transforms generic HIR into monomorphized HIR using worklist-based instantiation.

    Algorithm:
    1. Seed worklist with entry points (main, non-generic functions)
    2. While worklist not empty:
       a. Pop function/struct/enum instance key
       b. If already processed, skip
       c. Build TypeSubstitution from type_args
       d. Deep-clone HIR node with type substitution
       e. During cloning, discover new instantiation needs
       f. Enqueue newly discovered instances
    3. Build final MonomorphizationResult
    """

    def __init__(
        self,
        hir_result: HirBuildResult,
    ) -> None:
        self.program = hir_result.program
        self.type_table = hir_result.type_table
        self.symbol_table = hir_result.symbol_table
        self.layout = LayoutService(self.type_table, self.symbol_table)
        self.errors: List[str] = []

        # Instance tracking
        self.function_instances: Dict[InstanceKey, FunctionInstance] = {}
        self.struct_instances: Dict[InstanceKey, StructInstance] = {}
        self.enum_instances: Dict[InstanceKey, EnumInstance] = {}

        # Worklists for lazy instantiation
        self._func_worklist: deque[InstanceKey] = deque()
        self._struct_worklist: deque[InstanceKey] = deque()
        self._enum_worklist: deque[InstanceKey] = deque()

        # Track what's been enqueued to avoid duplicates
        self._func_enqueued: Set[InstanceKey] = set()
        self._struct_enqueued: Set[InstanceKey] = set()
        self._enum_enqueued: Set[InstanceKey] = set()

        # Index original HIR items by symbol
        self._func_by_symbol: Dict[SymbolId, HirFunction] = {}
        self._struct_by_symbol: Dict[SymbolId, HirStruct] = {}
        self._enum_by_symbol: Dict[SymbolId, HirEnum] = {}

        # Build indices
        for item in self.program.items:
            if isinstance(item, HirFunction):
                self._func_by_symbol[item.symbol_id] = item
            elif isinstance(item, HirStruct):
                self._struct_by_symbol[item.symbol_id] = item
            elif isinstance(item, HirEnum):
                self._enum_by_symbol[item.symbol_id] = item
            elif isinstance(item, HirExtension):
                # Index extension methods alongside free functions so they
                # participate in monomorphization. Without this an extension
                # method on `String` that uses `Vec<T>` would never see its
                # `Vec<T>` substituted to `Vec_T`, leading to bogus types in
                # codegen — the bug this whole index fixes.
                for method in item.methods:
                    self._func_by_symbol[method.symbol_id] = method

        # Track specialized symbols
        self._specialized_symbols: Dict[Tuple[SymbolId, Tuple[TypeId, ...]], SymbolId] = {}
        self._temp_counter = 0

    def monomorphize(self) -> MonomorphizationResult:
        """Execute monomorphization and return the result."""
        # Seed worklist with entry points
        self._seed_entry_points()

        # Process all worklists until empty
        self._process_worklists()

        # Build final result
        return self._build_result()

    # ----------------------- Entry Point Seeding -----------------------

    def _seed_entry_points(self) -> None:
        """Find and enqueue all entry points (main, non-generic functions)."""
        for item in self.program.items:
            if isinstance(item, HirFunction):
                if not self._is_generic_function(item):
                    # Non-generic function is an entry point
                    key = InstanceKey(item.symbol_id, ())
                    self._enqueue_function(key)
            elif isinstance(item, HirStruct):
                if not self._is_generic_struct(item):
                    key = InstanceKey(item.symbol_id, ())
                    self._enqueue_struct(key)
            elif isinstance(item, HirEnum):
                if not self._is_generic_enum(item):
                    key = InstanceKey(item.symbol_id, ())
                    self._enqueue_enum(key)
            elif isinstance(item, HirExtension):
                # Extension methods are non-generic standalone-style
                # functions. Seed each one so its body and signature
                # get specialized; generic uses inside (e.g. Vec<T>)
                # are then properly substituted to Vec_T.
                for method in item.methods:
                    if not self._is_generic_function(method):
                        key = InstanceKey(method.symbol_id, ())
                        self._enqueue_function(key)

    def _is_generic_function(self, func: HirFunction) -> bool:
        """Check if a function has generic parameters."""
        symbol = self.symbol_table.get_symbol(func.symbol_id)
        if symbol and symbol.decl_node:
            decl = symbol.decl_node
            if isinstance(decl, ast.FuncDecl) and decl.generic_params:
                return True
        return False

    def _is_generic_struct(self, struct: HirStruct) -> bool:
        """Check if a struct has generic parameters."""
        symbol = self.symbol_table.get_symbol(struct.symbol_id)
        if symbol and symbol.decl_node:
            decl = symbol.decl_node
            if isinstance(decl, ast.StructDecl) and decl.generic_params:
                return True
        return False

    def _is_generic_enum(self, enum: HirEnum) -> bool:
        """Check if an enum has generic parameters."""
        symbol = self.symbol_table.get_symbol(enum.symbol_id)
        if symbol and symbol.decl_node:
            decl = symbol.decl_node
            if isinstance(decl, ast.EnumDecl) and decl.generic_params:
                return True
        return False

    def _get_generic_params(self, symbol_id: SymbolId) -> List[str]:
        """Get the names of generic parameters for a symbol."""
        symbol = self.symbol_table.get_symbol(symbol_id)
        if symbol and symbol.decl_node:
            decl = symbol.decl_node
            if hasattr(decl, 'generic_params'):
                return [p.name for p in decl.generic_params]
        return []

    # ----------------------- Worklist Management -----------------------

    def _enqueue_function(self, key: InstanceKey) -> None:
        """Enqueue a function instance for processing."""
        if key not in self._func_enqueued:
            self._func_enqueued.add(key)
            self._func_worklist.append(key)

    def _enqueue_struct(self, key: InstanceKey) -> None:
        """Enqueue a struct instance for processing."""
        if key not in self._struct_enqueued:
            self._struct_enqueued.add(key)
            self._struct_worklist.append(key)

    def _enqueue_enum(self, key: InstanceKey) -> None:
        """Enqueue an enum instance for processing."""
        if key not in self._enum_enqueued:
            self._enum_enqueued.add(key)
            self._enum_worklist.append(key)

    # Upper bound on the number of distinct instantiations. Polymorphic
    # recursion (e.g. `struct W<T> { inner: W<Box<T>>? }`) produces an
    # unbounded stream of ever-larger type arguments; without a cap the
    # worklist never drains and the compiler hangs. This bound is far above
    # any realistic program's instantiation count.
    _MAX_INSTANTIATIONS = 100_000

    def _process_worklists(self) -> None:
        """Process all worklists until empty (or the instantiation cap trips)."""
        count = 0

        def _over_budget() -> bool:
            nonlocal count
            count += 1
            if count > self._MAX_INSTANTIATIONS:
                self.errors.append(
                    "monomorphization exceeded the instantiation limit "
                    f"({self._MAX_INSTANTIATIONS}); this usually indicates "
                    "unbounded polymorphic recursion in a generic type or "
                    "function"
                )
                self._func_worklist.clear()
                self._struct_worklist.clear()
                self._enum_worklist.clear()
                return True
            return False

        # Keep processing until all worklists are empty
        while (
            self._func_worklist or
            self._struct_worklist or
            self._enum_worklist
        ):
            # Process functions
            while self._func_worklist:
                key = self._func_worklist.popleft()
                if key not in self.function_instances:
                    if _over_budget():
                        return
                    self._instantiate_function(key)

            # Process structs
            while self._struct_worklist:
                key = self._struct_worklist.popleft()
                if key not in self.struct_instances:
                    if _over_budget():
                        return
                    self._instantiate_struct(key)

            # Process enums
            while self._enum_worklist:
                key = self._enum_worklist.popleft()
                if key not in self.enum_instances:
                    if _over_budget():
                        return
                    self._instantiate_enum(key)

    # ----------------------- Instantiation -----------------------

    def _build_substitution(self, symbol_id: SymbolId, type_args: Tuple[TypeId, ...]) -> TypeSubstitution:
        """Build a type substitution from generic params and type args."""
        subst = TypeSubstitution()
        params = self._get_generic_params(symbol_id)
        for i, param_name in enumerate(params):
            if i < len(type_args):
                subst.mapping[param_name] = type_args[i]
        return subst

    def _specialize_type(self, type_id: TypeId, subst: TypeSubstitution) -> TypeId:
        """Apply substitution and rewrite generic instances to specialized symbols.

        Walks compound types (Optional/Array/Dict/Tuple/...) so that any
        nested generic struct or enum gets monomorphized too.
        """
        specialized = subst.apply(type_id, self.type_table)
        return self._monomorphize_nested(specialized)

    def _monomorphize_nested(self, type_id: TypeId) -> TypeId:
        """Replace every reachable generic struct/enum instantiation with its
        specialized symbol."""
        info = self.type_table.get_type(type_id)
        if info is None:
            return type_id

        if info.kind == TypeKind.STRUCT and isinstance(info.data, StructTypeData):
            data = info.data
            new_args = tuple(self._monomorphize_nested(a) for a in data.type_args)
            has_vars = any(self._has_type_variables(a) for a in new_args)
            if new_args and data.symbol_id in self._struct_by_symbol and not has_vars:
                original = self._struct_by_symbol[data.symbol_id]
                key = InstanceKey(data.symbol_id, new_args)
                self._enqueue_struct(key)
                mangled = mangle_name(original.name, new_args, self.type_table)
                symbol = self._get_or_create_specialized_symbol(
                    data.symbol_id, new_args, mangled,
                )
                return self.type_table.make_struct(symbol, ())
            if new_args != data.type_args:
                return self.type_table.make_struct(data.symbol_id, new_args)
            return type_id

        if info.kind == TypeKind.ENUM and isinstance(info.data, EnumTypeData):
            data = info.data
            new_args = tuple(self._monomorphize_nested(a) for a in data.type_args)
            has_vars = any(self._has_type_variables(a) for a in new_args)
            if new_args and data.symbol_id in self._enum_by_symbol and not has_vars:
                original = self._enum_by_symbol[data.symbol_id]
                key = InstanceKey(data.symbol_id, new_args)
                self._enqueue_enum(key)
                mangled = mangle_name(original.name, new_args, self.type_table)
                symbol = self._get_or_create_specialized_symbol(
                    data.symbol_id, new_args, mangled,
                )
                return self.type_table.make_enum(symbol, ())
            if new_args != data.type_args:
                return self.type_table.make_enum(data.symbol_id, new_args)
            return type_id

        if info.kind == TypeKind.OPTIONAL and isinstance(info.data, OptionalTypeData):
            new_inner = self._monomorphize_nested(info.data.inner)
            if new_inner != info.data.inner:
                return self.type_table.make_optional(new_inner)
            return type_id

        if (info.kind == TypeKind.STRUCT and isinstance(info.data, StructTypeData)
                and info.data.symbol_id is None):
            fields = info.data.anon_fields or ()
            new_fields = tuple(
                (fname, self._monomorphize_nested(t)) for fname, t in fields
            )
            if new_fields != fields:
                return self.type_table.make_tuple(new_fields)
            return type_id

        if info.kind == TypeKind.FUNCTION and isinstance(info.data, FunctionTypeData):
            new_params = tuple(self._monomorphize_nested(p) for p in info.data.params)
            new_return = self._monomorphize_nested(info.data.return_type)
            if new_params != info.data.params or new_return != info.data.return_type:
                return self.type_table.make_function(new_params, new_return, info.data.is_async)
            return type_id

        return type_id

    def _instantiate_function(self, key: InstanceKey) -> None:
        """Instantiate a function with concrete type arguments."""
        original = self._func_by_symbol.get(key.symbol_id)
        if original is None:
            return

        subst = self._build_substitution(key.symbol_id, key.type_args)
        mangled = mangle_name(original.name, key.type_args, self.type_table)

        # Create or get specialized symbol
        specialized_symbol = self._get_or_create_specialized_symbol(
            key.symbol_id, key.type_args, mangled
        )

        # Specialize the function
        specialized = self._specialize_function(original, subst, specialized_symbol, mangled)

        instance = FunctionInstance(
            key=key,
            original_func=original,
            specialized_func=specialized,
            mangled_name=mangled,
        )
        self.function_instances[key] = instance

    def _instantiate_struct(self, key: InstanceKey) -> None:
        """Instantiate a struct with concrete type arguments."""
        original = self._struct_by_symbol.get(key.symbol_id)
        if original is None:
            return

        subst = self._build_substitution(key.symbol_id, key.type_args)
        mangled = mangle_name(original.name, key.type_args, self.type_table)

        # Create specialized symbol
        specialized_symbol = self._get_or_create_specialized_symbol(
            key.symbol_id, key.type_args, mangled
        )

        # Specialize the struct
        specialized = self._specialize_struct(original, subst, specialized_symbol, mangled)

        instance = StructInstance(
            key=key,
            original_struct=original,
            specialized_struct=specialized,
            mangled_name=mangled,
        )
        self.struct_instances[key] = instance

    def _instantiate_enum(self, key: InstanceKey) -> None:
        """Instantiate an enum with concrete type arguments."""
        original = self._enum_by_symbol.get(key.symbol_id)
        if original is None:
            return

        subst = self._build_substitution(key.symbol_id, key.type_args)
        mangled = mangle_name(original.name, key.type_args, self.type_table)

        # Create specialized symbol
        specialized_symbol = self._get_or_create_specialized_symbol(
            key.symbol_id, key.type_args, mangled
        )

        # Specialize the enum
        specialized = self._specialize_enum(original, subst, specialized_symbol, mangled)

        instance = EnumInstance(
            key=key,
            original_enum=original,
            specialized_enum=specialized,
            mangled_name=mangled,
        )
        self.enum_instances[key] = instance

    def _get_or_create_specialized_symbol(
        self,
        original_id: SymbolId,
        type_args: Tuple[TypeId, ...],
        mangled_name: str,
    ) -> SymbolId:
        """Get or create a symbol for a specialized instance."""
        cache_key = (original_id, type_args)
        if cache_key in self._specialized_symbols:
            return self._specialized_symbols[cache_key]

        # Get original symbol info
        original = self.symbol_table.get_symbol(original_id)
        if original is None:
            return original_id

        # For non-generic instances, reuse original symbol
        if not type_args:
            return original_id

        # Create new symbol for specialized instance
        symbol = self.symbol_table.create_symbol(
            name=mangled_name,
            kind=original.kind,
            namespace=original.namespace,
            span=original.span,
            decl_node=original.decl_node,  # Keep reference to original decl
            is_mutable=original.is_mutable,
        )
        self._specialized_symbols[cache_key] = symbol.id
        # Record so member resolution can recover the original generic context.
        self.symbol_table.record_specialization(symbol.id, original_id, type_args)
        return symbol.id

    # ----------------------- Function Specialization -----------------------

    def _specialize_function(
        self,
        func: HirFunction,
        subst: TypeSubstitution,
        new_symbol: SymbolId,
        mangled_name: str,
    ) -> HirFunction:
        """Create a specialized copy of a function with types substituted."""
        # Specialize parameters
        new_params = [self._specialize_param(p, subst) for p in func.params]

        # Specialize return type
        new_return = self._specialize_type(func.return_type, subst)
        # Discover type instantiation for the return type
        self._discover_type_instantiation(new_return)

        # Specialize body
        new_body: Optional[HirBlock] = None
        if func.body is not None:
            new_body = self._specialize_block(func.body, subst)

        return HirFunction(
            name=mangled_name,
            symbol_id=new_symbol,
            params=new_params,
            return_type=new_return,
            body=new_body,
            is_async=func.is_async,
            is_method=func.is_method,
            is_static=func.is_static,
        )

    def _specialize_param(self, param: HirParam, subst: TypeSubstitution) -> HirParam:
        """Specialize a function parameter."""
        new_type = self._specialize_type(param.type_id, subst)
        # Discover type instantiation for the parameter's type
        self._discover_type_instantiation(new_type)
        return HirParam(
            name=param.name,
            symbol_id=param.symbol_id,
            type_id=new_type,
            external_name=param.external_name,
            has_default=param.has_default,
        )

    # ----------------------- Struct/Enum Specialization -----------------------

    def _specialize_struct(
        self,
        struct: HirStruct,
        subst: TypeSubstitution,
        new_symbol: SymbolId,
        mangled_name: str,
    ) -> HirStruct:
        """Create a specialized copy of a struct."""
        # Specialize fields
        new_fields = [self._specialize_field(f, subst) for f in struct.fields]

        # Specialize methods
        new_methods: List[HirFunction] = []
        for method in struct.methods:
            # Create a specialized symbol for the method
            method_mangled = f"{mangled_name}_{method.name}"
            method_symbol = self.symbol_table.create_symbol(
                name=method_mangled,
                kind=SymbolKind.FUNCTION,
                namespace=Namespace.VALUE,
            )
            specialized_method = self._specialize_function(
                method, subst, method_symbol.id, method_mangled
            )
            new_methods.append(specialized_method)

        return HirStruct(
            name=mangled_name,
            symbol_id=new_symbol,
            fields=new_fields,
            methods=new_methods,
        )

    def _specialize_field(self, field: HirField, subst: TypeSubstitution) -> HirField:
        """Specialize a struct field."""
        new_type = self._specialize_type(field.type_id, subst)
        # Discover type instantiation for the field's type
        self._discover_type_instantiation(new_type)
        new_default = None
        if field.default_value is not None:
            new_default = self._specialize_expr(field.default_value, subst)

        return HirField(
            name=field.name,
            symbol_id=field.symbol_id,
            type_id=new_type,
            is_mutable=field.is_mutable,
            default_value=new_default,
        )

    def _specialize_enum(
        self,
        enum: HirEnum,
        subst: TypeSubstitution,
        new_symbol: SymbolId,
        mangled_name: str,
    ) -> HirEnum:
        """Create a specialized copy of an enum."""
        # Specialize cases
        new_cases = [self._specialize_enum_case(c, subst) for c in enum.cases]

        # Specialize methods
        new_methods: List[HirFunction] = []
        for method in enum.methods:
            method_mangled = f"{mangled_name}_{method.name}"
            method_symbol = self.symbol_table.create_symbol(
                name=method_mangled,
                kind=SymbolKind.FUNCTION,
                namespace=Namespace.VALUE,
            )
            specialized_method = self._specialize_function(
                method, subst, method_symbol.id, method_mangled
            )
            new_methods.append(specialized_method)

        return HirEnum(
            name=mangled_name,
            symbol_id=new_symbol,
            cases=new_cases,
            methods=new_methods,
        )

    def _specialize_enum_case(
        self, case: HirEnumCase, subst: TypeSubstitution
    ) -> HirEnumCase:
        """Specialize an enum case."""
        new_payload: List[Tuple[Optional[str], TypeId]] = []
        for label, type_id in case.payload:
            new_type = self._specialize_type(type_id, subst)
            # Discover type instantiation for the payload type
            self._discover_type_instantiation(new_type)
            new_payload.append((label, new_type))

        return HirEnumCase(
            name=case.name,
            symbol_id=case.symbol_id,
            payload=new_payload,
        )

    # ----------------------- Block/Statement Specialization -----------------------

    def _specialize_block(self, block: HirBlock, subst: TypeSubstitution) -> HirBlock:
        """Specialize a block of statements."""
        new_stmts = [self._specialize_stmt(s, subst) for s in block.statements]
        return HirBlock(statements=new_stmts)

    def _specialize_stmt(self, stmt: HirStmt, subst: TypeSubstitution) -> HirStmt:
        """Specialize a statement."""
        if isinstance(stmt, HirBlock):
            return self._specialize_block(stmt, subst)

        if isinstance(stmt, HirVarDecl):
            new_type = self._specialize_type(stmt.type_id, subst)
            # Discover type instantiation for the variable's type
            self._discover_type_instantiation(new_type)
            new_init = None
            if stmt.initializer is not None:
                new_init = self._specialize_expr(stmt.initializer, subst)
            return HirVarDecl(
                name=stmt.name,
                symbol_id=stmt.symbol_id,
                type_id=new_type,
                initializer=new_init,
                is_mutable=stmt.is_mutable,
            )

        if isinstance(stmt, HirAssign):
            return HirAssign(
                target=self._specialize_expr(stmt.target, subst),
                value=self._specialize_expr(stmt.value, subst),
                compound_op=stmt.compound_op,
            )

        if isinstance(stmt, HirExprStmt):
            return HirExprStmt(expr=self._specialize_expr(stmt.expr, subst))

        if isinstance(stmt, HirReturn):
            new_value = None
            if stmt.value is not None:
                new_value = self._specialize_expr(stmt.value, subst)
            return HirReturn(value=new_value)

        if isinstance(stmt, HirBreak):
            return HirBreak()

        if isinstance(stmt, HirContinue):
            return HirContinue()

        if isinstance(stmt, HirIf):
            new_cond = self._specialize_expr(stmt.condition, subst)
            new_then = self._specialize_block(stmt.then_block, subst)
            new_else: Optional[Union[HirBlock, HirIf]] = None
            if stmt.else_block is not None:
                if isinstance(stmt.else_block, HirBlock):
                    new_else = self._specialize_block(stmt.else_block, subst)
                else:
                    new_else = self._specialize_stmt(stmt.else_block, subst)
                    if not isinstance(new_else, HirIf):
                        # Wrap in block if needed
                        new_else = None
            return HirIf(
                condition=new_cond,
                then_block=new_then,
                else_block=new_else,
            )

        if isinstance(stmt, HirIfLet):
            new_pattern = self._specialize_pattern(stmt.pattern, subst)
            new_scrutinee = self._specialize_expr(stmt.scrutinee, subst)
            new_then = self._specialize_block(stmt.then_block, subst)
            new_else: Optional[Union[HirBlock, HirIf]] = None
            if stmt.else_block is not None:
                if isinstance(stmt.else_block, HirBlock):
                    new_else = self._specialize_block(stmt.else_block, subst)
                else:
                    specialized_else = self._specialize_stmt(stmt.else_block, subst)
                    if isinstance(specialized_else, HirIf):
                        new_else = specialized_else
            return HirIfLet(
                pattern=new_pattern,
                scrutinee=new_scrutinee,
                then_block=new_then,
                else_block=new_else,
            )

        if isinstance(stmt, HirGuard):
            return HirGuard(
                condition=self._specialize_expr(stmt.condition, subst),
                else_block=self._specialize_block(stmt.else_block, subst),
            )

        if isinstance(stmt, HirWhile):
            return HirWhile(
                condition=self._specialize_expr(stmt.condition, subst),
                body=self._specialize_block(stmt.body, subst),
            )

        if isinstance(stmt, HirFor):
            return HirFor(
                pattern=self._specialize_pattern(stmt.pattern, subst),
                iterable=self._specialize_expr(stmt.iterable, subst),
                body=self._specialize_block(stmt.body, subst),
            )

        if isinstance(stmt, HirSwitch):
            new_scrutinee = self._specialize_expr(stmt.scrutinee, subst)
            new_scrutinee_type = self._specialize_type(stmt.scrutinee_type, subst)
            new_cases = [self._specialize_switch_case(c, subst) for c in stmt.cases]
            return HirSwitch(
                scrutinee=new_scrutinee,
                scrutinee_type=new_scrutinee_type,
                cases=new_cases,
            )

        if isinstance(stmt, HirDefer):
            return HirDefer(body=self._specialize_block(stmt.body, subst))

        return stmt

    def _specialize_switch_case(
        self, case: HirSwitchCase, subst: TypeSubstitution
    ) -> HirSwitchCase:
        """Specialize a switch case."""
        new_patterns: List[Tuple[HirPattern, Optional[HirExpr]]] = []
        for pattern, guard in case.patterns:
            new_pattern = self._specialize_pattern(pattern, subst)
            new_guard = None
            if guard is not None:
                new_guard = self._specialize_expr(guard, subst)
            new_patterns.append((new_pattern, new_guard))

        return HirSwitchCase(
            patterns=new_patterns,
            body=self._specialize_block(case.body, subst),
            is_default=case.is_default,
        )

    # ----------------------- Expression Specialization -----------------------

    def _specialize_expr(self, expr: HirExpr, subst: TypeSubstitution) -> HirExpr:
        """Specialize an expression, discovering new instantiation needs."""
        new_type = self._specialize_type(expr.type_id, subst)

        if isinstance(expr, HirLiteral):
            if expr.kind == "size_of" and isinstance(expr.value, TypeId):
                target_type = self._specialize_type(expr.value, subst)
                return HirLiteral(
                    type_id=new_type,
                    value=self.layout.size_of(target_type),
                    kind="int",
                )
            if expr.kind == "type_id" and isinstance(expr.value, TypeId):
                target_type = self._specialize_type(expr.value, subst)
                return HirLiteral(
                    type_id=new_type,
                    value=self.type_table.runtime_type_id(target_type),
                    kind="int",
                )
            if expr.kind == "align_of" and isinstance(expr.value, TypeId):
                target_type = self._specialize_type(expr.value, subst)
                return HirLiteral(
                    type_id=new_type,
                    value=self.layout.align_of(target_type),
                    kind="int",
                )
            return HirLiteral(type_id=new_type, value=expr.value, kind=expr.kind)

        if isinstance(expr, HirVar):
            return HirVar(
                type_id=new_type,
                name=expr.name,
                symbol_id=expr.symbol_id,
            )

        if isinstance(expr, HirBinaryOp):
            return HirBinaryOp(
                type_id=new_type,
                left=self._specialize_expr(expr.left, subst),
                op=expr.op,
                right=self._specialize_expr(expr.right, subst),
            )

        if isinstance(expr, HirUnaryOp):
            return HirUnaryOp(
                type_id=new_type,
                op=expr.op,
                operand=self._specialize_expr(expr.operand, subst),
            )

        if isinstance(expr, HirTernary):
            return HirTernary(
                type_id=new_type,
                condition=self._specialize_expr(expr.condition, subst),
                then_expr=self._specialize_expr(expr.then_expr, subst),
                else_expr=self._specialize_expr(expr.else_expr, subst),
            )

        if isinstance(expr, HirCall):
            return self._specialize_call(expr, subst, new_type)

        if isinstance(expr, HirMethodCall):
            return self._specialize_method_call(expr, subst, new_type)

        if isinstance(expr, HirFieldAccess):
            return HirFieldAccess(
                type_id=new_type,
                object=self._specialize_expr(expr.object, subst),
                field_name=expr.field_name,
                field_symbol=expr.field_symbol,
            )

        if isinstance(expr, HirSubscript):
            new_indices = [self._specialize_expr(i, subst) for i in expr.indices]
            return HirSubscript(
                type_id=new_type,
                object=self._specialize_expr(expr.object, subst),
                indices=new_indices,
            )

        if isinstance(expr, HirTuple):
            new_elements = [
                (label, self._specialize_expr(e, subst))
                for label, e in expr.elements
            ]
            return HirTuple(type_id=new_type, elements=new_elements)

        if isinstance(expr, HirArray):
            new_elem_type = self._specialize_type(expr.element_type, subst)
            new_elements = [self._specialize_expr(e, subst) for e in expr.elements]
            # `[1, 2, 3]` desugars to a `Vec<T>` struct; enqueue the
            # concrete `Vec<T>` so its `with_capacity` / `push` methods
            # exist by the time the MIR lowerer needs them.
            self._discover_type_instantiation(new_type)
            return HirArray(
                type_id=new_type,
                elements=new_elements,
                element_type=new_elem_type,
            )

        if isinstance(expr, HirDict):
            new_key_type = self._specialize_type(expr.key_type, subst)
            new_value_type = self._specialize_type(expr.value_type, subst)
            new_entries = [
                (self._specialize_expr(k, subst), self._specialize_expr(v, subst))
                for k, v in expr.entries
            ]
            # `["a": 1]` desugars to `Dict<K, V>`; enqueue the concrete
            # `Dict<K, V>` so its `with_capacity` / `set` methods exist.
            self._discover_type_instantiation(new_type)
            return HirDict(
                type_id=new_type,
                entries=new_entries,
                key_type=new_key_type,
                value_type=new_value_type,
            )

        if isinstance(expr, HirLambda):
            new_params = [self._specialize_param(p, subst) for p in expr.params]
            new_body = self._specialize_block(expr.body, subst)
            return HirLambda(
                type_id=new_type,
                params=new_params,
                body=new_body,
                captures=list(expr.captures),
            )

        if isinstance(expr, HirStructInit):
            return self._specialize_struct_init(expr, subst, new_type)

        if isinstance(expr, HirEnumConstruct):
            return self._specialize_enum_construct(expr, subst, new_type)

        if isinstance(expr, HirCast):
            new_target = self._specialize_type(expr.target_type, subst)
            return HirCast(
                type_id=new_type,
                expr=self._specialize_expr(expr.expr, subst),
                target_type=new_target,
                kind=expr.kind,
            )

        if isinstance(expr, HirTypeCheck):
            new_checked = self._specialize_type(expr.checked_type, subst)
            return HirTypeCheck(
                type_id=new_type,
                expr=self._specialize_expr(expr.expr, subst),
                checked_type=new_checked,
            )

        if isinstance(expr, HirTryExpr):
            return HirTryExpr(
                type_id=new_type,
                expr=self._specialize_expr(expr.expr, subst),
                result_type=new_type,
            )

        if isinstance(expr, HirOptionalSome):
            new_inner = self._specialize_type(expr.inner_type, subst)
            return HirOptionalSome(
                type_id=new_type,
                value=self._specialize_expr(expr.value, subst),
                inner_type=new_inner,
            )

        if isinstance(expr, HirOptionalNone):
            new_inner = self._specialize_type(expr.inner_type, subst)
            return HirOptionalNone(type_id=new_type, inner_type=new_inner)

        if isinstance(expr, HirOptionalMatch):
            new_inner = self._specialize_type(expr.inner_type, subst)
            return HirOptionalMatch(
                type_id=new_type,
                scrutinee=self._specialize_expr(expr.scrutinee, subst),
                inner_type=new_inner,
                some_binding=expr.some_binding,
                some_expr=self._specialize_expr(expr.some_expr, subst),
                none_expr=self._specialize_expr(expr.none_expr, subst),
            )

        if isinstance(expr, HirClone):
            return HirClone(
                type_id=new_type,
                value=self._specialize_expr(expr.value, subst),
            )

        return expr

    def _specialize_call(
        self,
        call: HirCall,
        subst: TypeSubstitution,
        new_type: TypeId,
    ) -> HirCall:
        """Specialize a function call, potentially discovering new instantiations."""
        new_callee = self._specialize_expr(call.callee, subst)
        new_args = [
            (label, self._specialize_expr(arg, subst))
            for label, arg in call.arguments
        ]

        # Check if callee is a generic function that needs instantiation
        callee_symbol = call.callee_symbol
        specialized_callee = new_callee
        if callee_symbol is not None:
            # Infer type arguments using ``subst.apply`` on the original
            # argument types — *not* the fully-monomorphized rewrites in
            # ``new_args``. ``_monomorphize_nested`` replaces e.g.
            # ``Result<i32, String>`` with a freshly created
            # ``Result_i32_String`` whose own ``type_args`` are empty,
            # which then makes unification against ``Result<T, E>``
            # impossible. Using ``subst.apply(call.arguments[i].type_id)``
            # keeps the parametric structure intact.
            args_for_inference: List[Tuple[Optional[str], HirExpr]] = []
            for (label, orig_arg), (_, new_arg) in zip(call.arguments, new_args):
                # Always carry the substituted-but-not-yet-monomorphized
                # type so generic structure (e.g. ``Result<i32, String>``)
                # is preserved for unification. ``_monomorphize_nested``
                # would otherwise rewrite this to a specialized symbol
                # with empty type_args.
                inferred_type = subst.apply(orig_arg.type_id, self.type_table)
                args_for_inference.append((
                    label,
                    replace(new_arg, type_id=inferred_type),
                ))

            substituted_type = subst.apply(call.type_id, self.type_table)
            type_args = self._infer_call_type_args(
                callee_symbol, args_for_inference, substituted_type
            )
            # Only instantiate when *every* type parameter was resolved. A
            # leftover error_type means inference failed for some parameter;
            # specializing with it would clone the body with error types
            # throughout (a guaranteed downstream miscompile / bogus
            # `..._error` symbol). Report it cleanly instead.
            if type_args and any(self.type_table.is_error(a) for a in type_args):
                callee = self._func_by_symbol.get(callee_symbol)
                name = callee.name if callee is not None else "<generic call>"
                self.errors.append(
                    f"could not infer all type arguments for generic call to "
                    f"'{name}'"
                )
                type_args = ()
            if type_args:
                # Enqueue the specialized function
                key = InstanceKey(callee_symbol, type_args)
                self._enqueue_function(key)

                original_func = self._func_by_symbol.get(callee_symbol)
                if original_func is None:
                    return HirCall(
                        type_id=new_type,
                        callee=specialized_callee,
                        arguments=new_args,
                        callee_symbol=callee_symbol,
                    )

                mangled_name = mangle_name(
                    original_func.name,
                    type_args,
                    self.type_table,
                )
                callee_symbol = self._get_or_create_specialized_symbol(
                    callee_symbol,
                    type_args,
                    mangled_name,
                )
                if isinstance(specialized_callee, HirVar):
                    specialized_callee = HirVar(
                        type_id=specialized_callee.type_id,
                        name=mangled_name,
                        symbol_id=callee_symbol,
                    )

        return HirCall(
            type_id=new_type,
            callee=specialized_callee,
            arguments=new_args,
            callee_symbol=callee_symbol,
        )

    def _specialize_method_call(
        self,
        call: HirMethodCall,
        subst: TypeSubstitution,
        new_type: TypeId,
    ) -> HirMethodCall:
        """Specialize a method call."""
        new_receiver = self._specialize_expr(call.receiver, subst)
        new_args = [
            (label, self._specialize_expr(arg, subst))
            for label, arg in call.arguments
        ]

        # Check receiver type for generic instantiation
        receiver_type = new_receiver.type_id
        self._discover_type_instantiation(receiver_type)

        # Extension methods live in _func_by_symbol and need explicit
        # enqueuing (struct methods are handled by struct specialization).
        method_symbol = call.method_symbol
        if method_symbol is not None:
            original_func = self._func_by_symbol.get(method_symbol)
            if original_func is not None:
                # Extract concrete type args from the receiver's struct type.
                receiver_info = self.type_table.get_type(receiver_type)
                if receiver_info and receiver_info.kind == TypeKind.STRUCT \
                        and isinstance(receiver_info.data, StructTypeData):
                    type_args = receiver_info.data.type_args
                    if type_args and not any(
                        self._has_type_variables(t) for t in type_args
                    ):
                        key = InstanceKey(method_symbol, type_args)
                        self._enqueue_function(key)
                        mangled_name = mangle_name(
                            original_func.name, type_args, self.type_table
                        )
                        method_symbol = self._get_or_create_specialized_symbol(
                            method_symbol, type_args, mangled_name
                        )

        return HirMethodCall(
            type_id=new_type,
            receiver=new_receiver,
            method_name=call.method_name,
            arguments=new_args,
            method_symbol=method_symbol,
            is_static=call.is_static,
        )

    def _specialize_struct_init(
        self,
        init: HirStructInit,
        subst: TypeSubstitution,
        new_type: TypeId,
    ) -> HirStructInit:
        """Specialize a struct initialization."""
        new_struct_type = self._specialize_type(init.struct_type, subst)
        new_args = [
            (label, self._specialize_expr(arg, subst))
            for label, arg in init.arguments
        ]

        # Discover struct instantiation
        self._discover_type_instantiation(new_struct_type)

        return HirStructInit(
            type_id=new_type,
            struct_type=new_struct_type,
            struct_symbol=init.struct_symbol,
            arguments=new_args,
        )

    def _specialize_enum_construct(
        self,
        construct: HirEnumConstruct,
        subst: TypeSubstitution,
        new_type: TypeId,
    ) -> HirEnumConstruct:
        """Specialize an enum construction."""
        new_enum_type = self._specialize_type(construct.enum_type, subst)
        new_payload = [
            (label, self._specialize_expr(e, subst))
            for label, e in construct.payload
        ]

        # Discover enum instantiation
        self._discover_type_instantiation(new_enum_type)

        return HirEnumConstruct(
            type_id=new_type,
            enum_type=new_enum_type,
            case_name=construct.case_name,
            case_symbol=construct.case_symbol,
            payload=new_payload,
        )

    # ----------------------- Pattern Specialization -----------------------

    def _specialize_pattern(
        self, pattern: HirPattern, subst: TypeSubstitution
    ) -> HirPattern:
        """Specialize a pattern."""
        if isinstance(pattern, HirWildcardPattern):
            return HirWildcardPattern()

        if isinstance(pattern, HirBindingPattern):
            new_type = self._specialize_type(pattern.type_id, subst)
            return HirBindingPattern(
                name=pattern.name,
                symbol_id=pattern.symbol_id,
                type_id=new_type,
                is_mutable=pattern.is_mutable,
            )

        if isinstance(pattern, HirLiteralPattern):
            new_type = self._specialize_type(pattern.type_id, subst)
            return HirLiteralPattern(
                value=pattern.value,
                type_id=new_type,
            )

        if isinstance(pattern, HirTuplePattern):
            new_type = self._specialize_type(pattern.type_id, subst)
            new_elements = [
                (label, self._specialize_pattern(p, subst))
                for label, p in pattern.elements
            ]
            return HirTuplePattern(
                elements=new_elements,
                type_id=new_type,
            )

        if isinstance(pattern, HirEnumCasePattern):
            new_enum_type = self._specialize_type(pattern.enum_type, subst)
            new_payload = [self._specialize_pattern(p, subst) for p in pattern.payload]

            # Discover enum instantiation
            self._discover_type_instantiation(new_enum_type)

            return HirEnumCasePattern(
                case_name=pattern.case_name,
                case_symbol=pattern.case_symbol,
                payload=new_payload,
                enum_type=new_enum_type,
            )

        if isinstance(pattern, HirOrPattern):
            new_type = self._specialize_type(pattern.type_id, subst)
            new_patterns = [self._specialize_pattern(p, subst) for p in pattern.patterns]
            return HirOrPattern(
                patterns=new_patterns,
                type_id=new_type,
            )

        return pattern

    # ----------------------- Type Argument Inference -----------------------

    def _infer_call_type_args(
        self,
        callee_symbol: SymbolId,
        arguments: List[Tuple[Optional[str], HirExpr]],
        return_type: Optional[TypeId] = None,
    ) -> Tuple[TypeId, ...]:
        """
        Infer type arguments for a generic function call.

        Example:
            def identity<T>(x: T) -> T
            identity(42)  // Infer T = i32
        """
        params = self._get_generic_params(callee_symbol)
        if not params:
            return ()

        # Get the function's parameter types from its declaration
        symbol = self.symbol_table.get_symbol(callee_symbol)
        if symbol is None or symbol.decl_node is None:
            return ()

        decl = symbol.decl_node
        if not isinstance(decl, ast.FuncDecl):
            return ()

        # Build mapping from type parameter names to concrete types. The set
        # of names that count as type parameters during unification must be
        # restricted to the callee's own parameters — otherwise other
        # generic functions' parameter names pollute the search and we end
        # up mapping concrete types onto unrelated identifiers.
        param_set: Set[str] = set(params)
        inferred: Dict[str, TypeId] = {}

        # Infer from arguments
        for i, (_, arg_expr) in enumerate(arguments):
            if i >= len(decl.params):
                break

            param = decl.params[i]
            arg_type = arg_expr.type_id

            # Try to unify the parameter type with the argument type
            self._unify_for_inference(
                param.type_annotation, arg_type, inferred, param_set
            )

        # Infer from return type if still missing params
        missing_params = {p for p in params if p not in inferred}
        if missing_params and return_type is not None and decl.return_type is not None:
            self._unify_for_inference(
                decl.return_type, return_type, inferred, param_set
            )

        # Build type_args tuple in parameter order
        result: List[TypeId] = []
        for param_name in params:
            if param_name in inferred:
                result.append(inferred[param_name])
            else:
                # Couldn't infer - use error type
                result.append(self.type_table.error_type)

        return tuple(result)

    def _unify_for_inference(
        self,
        type_node: Optional[ast.Type],
        concrete_type: TypeId,
        inferred: Dict[str, TypeId],
        params: Optional[Set[str]] = None,
    ) -> None:
        """
        Unify a type annotation with a concrete type to infer type parameters.

        ``params`` is the set of names that should be treated as the callee's
        generic parameters. If omitted, we fall back to looking at every
        generic parameter across the whole program.
        """
        if type_node is None:
            return

        if params is None:
            params = self._get_generic_params_from_context()

        if isinstance(type_node, ast.NamedType):
            # Check if this is a type parameter reference
            if not type_node.generic_args:
                if type_node.name in params:
                    # Found a type parameter - record the inference
                    if type_node.name not in inferred:
                        inferred[type_node.name] = concrete_type
                    return

            # Otherwise, recurse into generic args
            info = self.type_table.get_type(concrete_type)
            if info is None:
                return

            if info.kind == TypeKind.STRUCT:
                data = info.data
                if isinstance(data, StructTypeData):
                    for i, arg in enumerate(type_node.generic_args):
                        if i < len(data.type_args):
                            self._unify_for_inference(
                                arg, data.type_args[i], inferred, params
                            )

            elif info.kind == TypeKind.ENUM:
                data = info.data
                if isinstance(data, EnumTypeData):
                    for i, arg in enumerate(type_node.generic_args):
                        if i < len(data.type_args):
                            self._unify_for_inference(
                                arg, data.type_args[i], inferred, params
                            )

        elif isinstance(type_node, ast.ArrayType):
            # `[T]` annotations are sugar for `Vec<T>` STRUCT.
            info = self.type_table.get_type(concrete_type)
            if info and info.kind == TypeKind.STRUCT:
                data = info.data
                if isinstance(data, StructTypeData) and data.type_args:
                    self._unify_for_inference(
                        type_node.element, data.type_args[0], inferred, params
                    )

        elif isinstance(type_node, ast.DictType):
            # `[K: V]` annotations are sugar for `Dict<K, V>` STRUCT.
            info = self.type_table.get_type(concrete_type)
            if info and info.kind == TypeKind.STRUCT:
                data = info.data
                if isinstance(data, StructTypeData) and len(data.type_args) >= 2:
                    self._unify_for_inference(
                        type_node.key, data.type_args[0], inferred, params
                    )
                    self._unify_for_inference(
                        type_node.value, data.type_args[1], inferred, params
                    )

        elif isinstance(type_node, ast.OptionalType):
            info = self.type_table.get_type(concrete_type)
            if info and info.kind == TypeKind.OPTIONAL:
                data = info.data
                if isinstance(data, OptionalTypeData):
                    self._unify_for_inference(
                        type_node.inner, data.inner, inferred, params
                    )

        elif isinstance(type_node, ast.TupleType):
            info = self.type_table.get_type(concrete_type)
            if (info and info.kind == TypeKind.STRUCT
                    and isinstance(info.data, StructTypeData) and info.data.symbol_id is None):
                fields = info.data.anon_fields or ()
                for i, (_, elem_type) in enumerate(type_node.elements):
                    if i < len(fields):
                        self._unify_for_inference(
                            elem_type, fields[i][1], inferred, params
                        )

    def _get_generic_params_from_context(self) -> Set[str]:
        """Get generic parameter names from the current context (all functions)."""
        params: Set[str] = set()
        for func in self._func_by_symbol.values():
            symbol = self.symbol_table.get_symbol(func.symbol_id)
            if symbol and symbol.decl_node:
                decl = symbol.decl_node
                if hasattr(decl, 'generic_params'):
                    for p in decl.generic_params:
                        params.add(p.name)
        return params

    def _has_type_variables(self, type_id: TypeId) -> bool:
        """Check if a type contains any unresolved type variables."""
        info = self.type_table.get_type(type_id)
        if info is None:
            return False
        if info.kind == TypeKind.TYPE_VARIABLE:
            return True
        if info.kind == TypeKind.STRUCT and isinstance(info.data, StructTypeData):
            if any(self._has_type_variables(a) for a in info.data.type_args):
                return True
            if info.data.symbol_id is None:
                return any(self._has_type_variables(t) for _, t in (info.data.anon_fields or ()))
            return False
        if info.kind == TypeKind.ENUM and isinstance(info.data, EnumTypeData):
            return any(self._has_type_variables(a) for a in info.data.type_args)
        if info.kind == TypeKind.OPTIONAL and isinstance(info.data, OptionalTypeData):
            return self._has_type_variables(info.data.inner)
        if info.kind == TypeKind.FUNCTION and isinstance(info.data, FunctionTypeData):
            return any(self._has_type_variables(p) for p in info.data.params) or self._has_type_variables(info.data.return_type)
        return False

    # ----------------------- Type Instantiation Discovery -----------------------

    def _discover_type_instantiation(self, type_id: TypeId) -> None:
        """Discover and enqueue type instantiations needed for a type."""
        info = self.type_table.get_type(type_id)
        if info is None:
            return

        if info.kind == TypeKind.STRUCT:
            data = info.data
            if isinstance(data, StructTypeData):
                if data.symbol_id is None:
                    # Anonymous struct (tuple): recurse into element types
                    for _, elem_type in (data.anon_fields or ()):
                        self._discover_type_instantiation(elem_type)
                    return
                if data.symbol_id in self._struct_by_symbol:
                    struct = self._struct_by_symbol[data.symbol_id]
                    is_generic = self._is_generic_struct(struct)
                    # Only enqueue if non-generic, or if generic with concrete type args
                    if not is_generic or (data.type_args and not any(self._has_type_variables(a) for a in data.type_args)):
                        key = InstanceKey(data.symbol_id, data.type_args)
                        self._enqueue_struct(key)

                # Recurse into type args
                for arg in data.type_args:
                    self._discover_type_instantiation(arg)

        elif info.kind == TypeKind.ENUM:
            data = info.data
            if isinstance(data, EnumTypeData):
                if data.symbol_id in self._enum_by_symbol:
                    enum = self._enum_by_symbol[data.symbol_id]
                    is_generic = self._is_generic_enum(enum)
                    # Only enqueue if non-generic, or if generic with concrete type args
                    if not is_generic or (data.type_args and not any(self._has_type_variables(a) for a in data.type_args)):
                        key = InstanceKey(data.symbol_id, data.type_args)
                        self._enqueue_enum(key)

                # Recurse into type args
                for arg in data.type_args:
                    self._discover_type_instantiation(arg)

        elif info.kind == TypeKind.OPTIONAL:
            data = info.data
            if isinstance(data, OptionalTypeData):
                self._discover_type_instantiation(data.inner)



    # ----------------------- Result Building -----------------------

    def _build_result(self) -> MonomorphizationResult:
        """Build the final monomorphization result."""
        # Collect all specialized items
        items: List[HirItem] = []

        # Methods that came from extensions: their specialized versions
        # live in self.function_instances; we don't also want them as
        # free top-level functions in the output, since MIR builder
        # generates the MIR code from the extension wrapper. Track the
        # original symbol ids so we can skip them below.
        extension_method_symbols: Set[SymbolId] = set()
        for item in self.program.items:
            if isinstance(item, HirExtension):
                for method in item.methods:
                    extension_method_symbols.add(method.symbol_id)

        # Add non-generic items that weren't processed (extern funcs, etc.)
        for item in self.program.items:
            if isinstance(item, HirExternFunc):
                items.append(item)
            elif isinstance(item, HirProtocol):
                items.append(item)
            elif isinstance(item, HirExtension):
                # Replace each method with its specialized counterpart
                # so the body has fully monomorphized types. If a method
                # has no specialized instance (e.g. it was generic and
                # never called), we keep the original — it won't compile
                # downstream but at least we don't lose the declaration.
                specialized_methods: List[HirFunction] = []
                for method in item.methods:
                    key = InstanceKey(method.symbol_id, ())
                    inst = self.function_instances.get(key)
                    if inst is not None:
                        specialized_methods.append(inst.specialized_func)
                    else:
                        specialized_methods.append(method)
                items.append(
                    HirExtension(
                        extended_type=self._monomorphize_nested(item.extended_type),
                        methods=specialized_methods,
                    )
                )

        # Add specialized functions, but skip the ones that originally
        # came from extensions — they're already attached to the
        # HirExtension item above and MIR builder will pick them up
        # from there.
        for instance in self.function_instances.values():
            if instance.original_func.symbol_id in extension_method_symbols:
                continue
            items.append(instance.specialized_func)

        # Add specialized structs
        for instance in self.struct_instances.values():
            items.append(instance.specialized_struct)

        # Add specialized enums
        for instance in self.enum_instances.values():
            items.append(instance.specialized_enum)

        program = HirProgram(items=items)

        return MonomorphizationResult(
            program=program,
            type_table=self.type_table,
            symbol_table=self.symbol_table,
            function_instances=self.function_instances,
            struct_instances=self.struct_instances,
            enum_instances=self.enum_instances,
            errors=self.errors,
        )


# ========================= Public API =========================

def monomorphize(hir_result: HirBuildResult) -> MonomorphizationResult:
    """
    Transform generic HIR into monomorphized HIR.

    Args:
        hir_result: The result from the HIR building phase

    Returns:
        MonomorphizationResult with specialized program
    """
    monomorphizer = Monomorphizer(hir_result)
    return monomorphizer.monomorphize()
