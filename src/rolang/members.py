"""Member resolution for RoLang type checking."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple, TYPE_CHECKING

from .symbols import SymbolKind, SymbolId
from .type_resolver import TypeResolver

if TYPE_CHECKING:
    from .types import TypeId, TypeTable
    from .symbols import SymbolTable


@dataclass
class FieldInfo:
    """Information about a struct/enum field.

    `visibility` and `source_module` enable cross-module access control —
    non-`pub` fields are reachable only from inside the declaring module
    (the same rule extension methods follow). This is how the stdlib keeps
    its internal `RawPtr` handles out of safe user code: an unexported
    field on a public struct cannot be read or written from anywhere
    except the stdlib module that declared it.
    """
    name: str
    type_id: TypeId
    is_mutable: bool
    index: int  # Position in struct layout
    visibility: str = "internal"
    source_module: Optional[str] = None


@dataclass
class MethodInfo:
    """Information about a method.

    Methods can freely update their receiver's heap state.

    `visibility` and `source_module` are populated only for *extension*
    methods so the member resolver can enforce cross-module visibility
    (a non-`pub` extension is only callable from inside its declaring
    module). Regular struct/enum/protocol methods leave these at their
    defaults and are not filtered.
    """
    name: str
    symbol_id: SymbolId
    signature: TypeId  # FunctionType TypeId
    is_static: bool = False
    visibility: str = "pub"  # default = always-visible (struct/enum methods)
    source_module: Optional[str] = None


@dataclass
class TypeMembers:
    """All members accessible on a type."""
    fields: Dict[str, FieldInfo] = field(default_factory=dict)
    methods: Dict[str, MethodInfo] = field(default_factory=dict)
    generic_param_names: Tuple[str, ...] = ()  # For generic substitution


def _extension_methods_clash(a: "MethodInfo", b: "MethodInfo") -> bool:
    """Decide whether two same-named extension methods on the same type
    are mutually visible (and therefore ambiguous).

    Visibility rule recap:
      * `pub`     → visible to every importer of the declaring module.
      * `internal`/`private` → visible only inside the declaring module.

    Two methods clash if there exists some calling module that can see
    both. The cases break down as follows (commutative):
      pub  + pub               → clash (any importer sees both)
      pub  + non-pub(M)        → clash (M sees both)
      non-pub(M) + non-pub(M)  → clash (M sees both — shadow within module)
      non-pub(M) + non-pub(N)  → no clash if M != N
    """
    a_pub = a.visibility == "pub"
    b_pub = b.visibility == "pub"
    if a_pub and b_pub:
        return True
    if a_pub or b_pub:
        return True
    # Both non-pub: clash only if they share the same source module
    # (and both are tagged with one).
    if a.source_module is None or b.source_module is None:
        return True  # Can't tell; be safe.
    return a.source_module == b.source_module


class MemberResolver:
    """Resolves members (fields and methods) for types."""

    def __init__(self, type_table: TypeTable, symbol_table: SymbolTable) -> None:
        self.type_table = type_table
        self.symbol_table = symbol_table
        self.type_resolver = TypeResolver(
            type_table,
            symbol_table,
            allow_symbol_table_lookup=True,
        )
        # Cache for resolved members
        self._cache: Dict[TypeId, TypeMembers] = {}
        # Extension methods registry: type symbol -> list of methods
        self._extension_methods: Dict[SymbolId, List[MethodInfo]] = {}
        # Source module of the call site currently being checked. When set,
        # `get_method` hides non-`pub` extension methods authored in any
        # other module. The checker pushes/pops this as it descends into
        # FuncDecls during unified multi-module compilation.
        self._current_source_module: Optional[str] = None

    def set_current_source_module(self, module: Optional[str]) -> None:
        """Set which source module the current callsite belongs to.

        Affects visibility filtering of extension methods in `get_method`.
        Pass `None` to disable filtering (default).
        """
        self._current_source_module = module

    def get_members(self, type_id: TypeId) -> TypeMembers:
        """Get all members for a type."""
        if type_id in self._cache:
            return self._cache[type_id]

        members = self._resolve_members(type_id)
        self._cache[type_id] = members
        return members

    def get_field(self, type_id: TypeId, name: str) -> Optional[FieldInfo]:
        """Get a specific field by name."""
        members = self.get_members(type_id)
        return members.fields.get(name)

    def get_method(
        self,
        type_id: TypeId,
        name: str,
        *,
        static: Optional[bool] = False,
    ) -> Optional[MethodInfo]:
        """Get a specific method by name."""
        members = self.get_members(type_id)
        if name in members.methods:
            method = members.methods[name]
            if static is None or method.is_static == static:
                return method

        # Check extension methods
        from .types import TypeKind, StructTypeData, EnumTypeData, PrimitiveTypeData
        info = self.type_table.get_type(type_id)
        if info:
            symbol_id = None
            if info.kind == TypeKind.STRUCT:
                data = info.data
                if isinstance(data, StructTypeData):
                    symbol_id = data.symbol_id
            elif info.kind == TypeKind.ENUM:
                data = info.data
                if isinstance(data, EnumTypeData):
                    symbol_id = data.symbol_id
            elif info.kind == TypeKind.PRIMITIVE:
                data = info.data
                if isinstance(data, PrimitiveTypeData):
                    builtin_id = self.symbol_table.get_builtin(data.primitive.value)
                    if builtin_id is not None:
                        symbol_id = builtin_id

            if symbol_id is not None:
                ext_methods = self._extension_methods.get(symbol_id, [])
                for method in ext_methods:
                    if method.name != name:
                        continue
                    if static is not None and method.is_static != static:
                        continue
                    # Cross-module visibility filter for extension methods:
                    # a non-`pub` extension can only be called from the
                    # module that declared it.
                    if (
                        method.source_module is not None
                        and method.visibility != "pub"
                        and self._current_source_module is not None
                        and method.source_module != self._current_source_module
                    ):
                        continue
                    return method

        # Synthesized .clone() — available on every heap type; lowers to
        # rt_obj_clone via the Clone MIR op.  No user-visible symbol: the
        # hir_builder intercepts this method call and emits HirClone before
        # a HirMethodCall is ever constructed, so SymbolId(-1) never reaches
        # the MIR/codegen layers.
        from .symbols import SymbolId
        from .types import TypeKind
        if (name == "clone"
                and static is not True
                and self.type_table.is_heap_type(type_id)):
            clone_sig = self.type_table.make_function(
                params=(),
                return_type=type_id,
            )
            return MethodInfo(
                name="clone",
                symbol_id=SymbolId(-1),
                signature=clone_sig,
                is_static=False,
                visibility="pub",
            )

        return None

    def register_extension(
        self,
        type_symbol_id: SymbolId,
        methods: List[MethodInfo],
    ) -> List[Tuple[MethodInfo, MethodInfo]]:
        """Register extension methods for a type.

        Returns a list of `(existing, new)` MethodInfo pairs for any
        method-name collisions where the two methods are *both* visible
        to at least one common module. Same-symbol re-registrations are
        treated as no-ops. Caller decides whether to surface conflicts
        as diagnostics.
        """
        bucket = self._extension_methods.setdefault(type_symbol_id, [])
        existing_by_id = {m.symbol_id: m for m in bucket}
        existing_by_name: Dict[str, List[MethodInfo]] = {}
        for m in bucket:
            existing_by_name.setdefault(m.name, []).append(m)

        conflicts: List[Tuple[MethodInfo, MethodInfo]] = []
        for new in methods:
            # Same-symbol re-registration (e.g. the AST walk plus the
            # imported-extension-methods path both registering one method
            # under unified compile): skip silently.
            if new.symbol_id in existing_by_id:
                continue
            # Conflict detection: another method with the same name
            # *and* a callsite that can see both copies.
            for prev in existing_by_name.get(new.name, []):
                if _extension_methods_clash(prev, new):
                    conflicts.append((prev, new))
            bucket.append(new)
            existing_by_id[new.symbol_id] = new
            existing_by_name.setdefault(new.name, []).append(new)
        return conflicts

    def _resolve_members(self, type_id: TypeId) -> TypeMembers:
        """Resolve members for a type."""
        from .types import (
            TypeKind, StructTypeData, EnumTypeData,
            OptionalTypeData
        )
        from .symbols import SymbolKind
        from . import ast

        info = self.type_table.get_type(type_id)
        if info is None:
            return TypeMembers()

        members = TypeMembers()

        if info.kind == TypeKind.STRUCT:
            data = info.data
            if isinstance(data, StructTypeData):
                members = self._resolve_struct_members(data)

        elif info.kind == TypeKind.ENUM:
            data = info.data
            if isinstance(data, EnumTypeData):
                members = self._resolve_enum_members(data)

        elif info.kind == TypeKind.OPTIONAL:
            # Built-in methods on T?. These method calls are intercepted in
            # ``hir_builder._build_call`` and desugared into ``HirOptionalMatch``,
            # so the SymbolId here is a placeholder; we only need a
            # plausible signature for type checking.
            from .types import OptionalTypeData
            data = info.data
            if isinstance(data, OptionalTypeData):
                inner = data.inner
                bool_type = self.type_table.get_builtin("Bool")
                if bool_type is not None:
                    is_some_sig = self.type_table.make_function(
                        params=(), return_type=bool_type, is_async=False,
                    )
                    members.methods["is_some"] = MethodInfo(
                        name="is_some",
                        symbol_id=SymbolId(-1),
                        signature=is_some_sig,
                    )
                    members.methods["is_none"] = MethodInfo(
                        name="is_none",
                        symbol_id=SymbolId(-1),
                        signature=is_some_sig,
                    )
                unwrap_or_sig = self.type_table.make_function(
                    params=(inner,), return_type=inner, is_async=False,
                )
                members.methods["unwrap_or"] = MethodInfo(
                    name="unwrap_or",
                    symbol_id=SymbolId(-1),
                    signature=unwrap_or_sig,
                )

        return members

    def _resolve_struct_members(self, data: StructTypeData) -> TypeMembers:
        """Resolve members for a struct type using pre-resolved cache."""
        from .symbols import SymbolKind

        # Anonymous struct (tuple): build members directly from anon_fields.
        if data.symbol_id is None:
            members = TypeMembers()
            fields = data.anon_fields or ()
            for i, (fname, elem_type) in enumerate(fields):
                elem_type = self._monomorphize_nested(elem_type)
                members.fields[fname] = FieldInfo(
                    name=fname, type_id=elem_type, is_mutable=True, index=i,
                )
                if not fname.isdigit():
                    members.fields[str(i)] = FieldInfo(
                        name=str(i), type_id=elem_type, is_mutable=True, index=i,
                    )
            return members

        # Try the pre-resolved member cache first
        cached = self.type_table.get_type_members(data.symbol_id)
        if cached is None:
            return self._resolve_struct_members_from_ast(data)

        # Determine effective type args for generic substitution
        effective_args: Tuple[TypeId, ...] = data.type_args
        if not effective_args:
            origin = self.symbol_table.specialization_origin.get(data.symbol_id)
            if origin is not None:
                effective_args = origin[1]

        # Build type parameter substitution map from cached generic param names
        type_subst: Dict[str, TypeId] = {}
        for i, name in enumerate(cached.generic_param_names):
            if i < len(effective_args):
                type_subst[name] = effective_args[i]

        members = TypeMembers()
        for name, field in cached.fields.items():
            field_type = self._substitute_member_type(field.type_id, type_subst)
            field_type = self._monomorphize_nested(field_type)
            members.fields[name] = FieldInfo(
                name=field.name, type_id=field_type,
                is_mutable=field.is_mutable, index=field.index,
                visibility=field.visibility,
                source_module=field.source_module,
            )
        for name, method in cached.methods.items():
            sig = self._substitute_member_type(method.signature, type_subst)
            members.methods[name] = MethodInfo(
                name=method.name, symbol_id=method.symbol_id,
                signature=sig, is_static=method.is_static,
            )
        return members

    def _resolve_enum_members(self, data: EnumTypeData) -> TypeMembers:
        """Resolve members for an enum type using pre-resolved cache."""
        cached = self.type_table.get_type_members(data.symbol_id)
        if cached is None:
            return self._resolve_enum_members_from_ast(data)

        effective_args: Tuple[TypeId, ...] = data.type_args
        if not effective_args:
            origin = self.symbol_table.specialization_origin.get(data.symbol_id)
            if origin is not None:
                effective_args = origin[1]

        type_subst: Dict[str, TypeId] = {}
        for i, name in enumerate(cached.generic_param_names):
            if i < len(effective_args):
                type_subst[name] = effective_args[i]

        members = TypeMembers()
        for name, method in cached.methods.items():
            sig = self._substitute_member_type(method.signature, type_subst)
            members.methods[name] = MethodInfo(
                name=method.name, symbol_id=method.symbol_id,
                signature=sig,
            )
        return members

    # --- Private helpers ---

    def _substitute_member_type(self, type_id: TypeId, subst: Dict[str, TypeId]) -> TypeId:
        """Apply generic substitution to a pre-resolved member type."""
        from .types import (
            TypeKind, TypeVariableData, StructTypeData, EnumTypeData,
            FunctionTypeData,
            OptionalTypeData,
        )
        if not subst:
            return type_id
        info = self.type_table.get_type(type_id)
        if info is None:
            return type_id
        if info.kind == TypeKind.TYPE_VARIABLE and isinstance(info.data, TypeVariableData):
            return subst.get(info.data.name, type_id)
        if info.kind == TypeKind.STRUCT and isinstance(info.data, StructTypeData):
            args = tuple(self._substitute_member_type(a, subst) for a in info.data.type_args)
            return self.type_table.make_struct(info.data.symbol_id, args)
        if info.kind == TypeKind.ENUM and isinstance(info.data, EnumTypeData):
            args = tuple(self._substitute_member_type(a, subst) for a in info.data.type_args)
            return self.type_table.make_enum(info.data.symbol_id, args)
        if (info.kind == TypeKind.STRUCT and isinstance(info.data, StructTypeData)
                and info.data.symbol_id is None):
            # Anonymous struct (tuple): substitute element types
            fields = info.data.anon_fields or ()
            new_fields = tuple(
                (fname, self._substitute_member_type(t, subst)) for fname, t in fields
            )
            return self.type_table.make_tuple(
                tuple((fname, t) for fname, t in new_fields)
            )
        if info.kind == TypeKind.FUNCTION and isinstance(info.data, FunctionTypeData):
            params = tuple(self._substitute_member_type(p, subst) for p in info.data.params)
            ret = self._substitute_member_type(info.data.return_type, subst)
            return self.type_table.make_function(params, ret, info.data.is_async)
        if info.kind == TypeKind.OPTIONAL and isinstance(info.data, OptionalTypeData):
            return self.type_table.make_optional(self._substitute_member_type(info.data.inner, subst))
        return type_id

    def _resolve_struct_members_from_ast(self, data: StructTypeData) -> TypeMembers:
        """Fallback: resolve members from AST when cache is unavailable."""
        from . import ast
        from .symbols import SymbolKind

        members = TypeMembers()
        symbol = self.symbol_table.get_symbol(data.symbol_id)
        if symbol is None or symbol.decl_node is None:
            return members
        decl = symbol.decl_node
        if not isinstance(decl, ast.StructDecl):
            return members

        effective_args: Tuple[TypeId, ...] = data.type_args
        if not effective_args:
            origin = self.symbol_table.specialization_origin.get(data.symbol_id)
            if origin is not None:
                effective_args = origin[1]

        type_subst: Dict[str, TypeId] = {}
        for i, param in enumerate(decl.generic_params):
            if i < len(effective_args):
                type_subst[param.name] = effective_args[i]

        field_index = 0
        decl_module = self._source_module_for_symbol(data.symbol_id)
        for member in decl.members:
            if isinstance(member, ast.PropertyDecl):
                field_type = self._resolve_member_type(member.type_annotation, type_subst)
                field_type = self._monomorphize_nested(field_type)
                members.fields[member.name] = FieldInfo(
                    name=member.name, type_id=field_type,
                    is_mutable=member.is_mutable, index=field_index,
                    visibility=member.visibility,
                    source_module=decl_module,
                )
                field_index += 1
            elif isinstance(member, ast.FuncDecl):
                method_type = self._resolve_method_type(member, type_subst)
                method_symbol_id = self._find_method_symbol(decl, member.name)
                if method_symbol_id:
                    members.methods[member.name] = MethodInfo(
                        name=member.name, symbol_id=method_symbol_id,
                        signature=method_type,
                        is_static=member.is_static,
                    )
        return members

    def _resolve_enum_members_from_ast(self, data: EnumTypeData) -> TypeMembers:
        """Fallback: resolve enum members from AST when cache is unavailable."""
        from . import ast
        from .symbols import SymbolKind

        members = TypeMembers()
        symbol = self.symbol_table.get_symbol(data.symbol_id)
        if symbol is None or symbol.decl_node is None:
            return members
        decl = symbol.decl_node
        if not isinstance(decl, ast.EnumDecl):
            return members

        effective_args: Tuple[TypeId, ...] = data.type_args
        if not effective_args:
            origin = self.symbol_table.specialization_origin.get(data.symbol_id)
            if origin is not None:
                effective_args = origin[1]

        type_subst: Dict[str, TypeId] = {}
        for i, param in enumerate(decl.generic_params):
            if i < len(effective_args):
                type_subst[param.name] = effective_args[i]

        for member in decl.members:
            if isinstance(member, ast.FuncDecl):
                method_type = self._resolve_method_type(member, type_subst)
                method_symbol_id = self._find_method_symbol(decl, member.name)
                if method_symbol_id:
                    members.methods[member.name] = MethodInfo(
                        name=member.name, symbol_id=method_symbol_id,
                        signature=method_type,
                        is_static=member.is_static,
                    )
        return members

    def _monomorphize_nested(self, type_id: TypeId) -> TypeId:
        """Rewrite any generic struct/enum instantiations in ``type_id`` to
        the corresponding monomorphized symbol. Walks compound types
        recursively. Mirrors the monomorphizer's nested rewriter so member
        resolution returns the same TypeIds codegen actually used."""
        from .types import (
            TypeKind, StructTypeData, EnumTypeData,
            OptionalTypeData,
            FunctionTypeData,
        )
        info = self.type_table.get_type(type_id)
        if info is None:
            return type_id

        if info.kind == TypeKind.STRUCT and isinstance(info.data, StructTypeData):
            data = info.data
            new_args = tuple(self._monomorphize_nested(a) for a in data.type_args)
            if new_args:
                spec_id = self.symbol_table.find_specialization(data.symbol_id, new_args)
                if spec_id is not None:
                    return self.type_table.make_struct(spec_id, ())
            if new_args != data.type_args:
                return self.type_table.make_struct(data.symbol_id, new_args)
            return type_id

        if info.kind == TypeKind.ENUM and isinstance(info.data, EnumTypeData):
            data = info.data
            new_args = tuple(self._monomorphize_nested(a) for a in data.type_args)
            if new_args:
                spec_id = self.symbol_table.find_specialization(data.symbol_id, new_args)
                if spec_id is not None:
                    return self.type_table.make_enum(spec_id, ())
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
            # Anonymous struct (tuple): monomorphize element types
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
                return self.type_table.make_function(
                    new_params, new_return, info.data.is_async
                )
            return type_id

        return type_id

    def _resolve_member_type(
        self,
        type_ann: Optional[ast.Type],
        subst: Dict[str, TypeId]
    ) -> TypeId:
        """Resolve a type annotation, substituting type parameters."""
        if type_ann is None:
            return self.type_table.error_type

        return self._resolve_type_node(type_ann, subst)

    def _resolve_type_node(
        self,
        type_node: ast.Type,
        subst: Dict[str, TypeId]
    ) -> TypeId:
        """Resolve a type AST node to a TypeId."""
        return self.type_resolver.resolve_type_node(type_node, subst)

    def _resolve_method_type(
        self,
        func: ast.FuncDecl,
        subst: Dict[str, TypeId]
    ) -> TypeId:
        """Resolve the type of a method declaration."""
        from . import ast

        # Resolve parameter types
        param_types: List[TypeId] = []
        for param in func.params:
            if param.type_annotation:
                param_type = self._resolve_type_node(param.type_annotation, subst)
                param_types.append(param_type)
            else:
                param_types.append(self.type_table.error_type)

        # Resolve return type
        if func.return_type:
            return_type = self._resolve_type_node(func.return_type, subst)
        else:
            return_type = self.type_table.void_type

        return self.type_table.make_function(
            params=tuple(param_types),
            return_type=return_type,
            is_async=func.is_async
        )

    def _source_module_for_symbol(self, symbol_id: SymbolId) -> Optional[str]:
        """Return the source module of a symbol, if known.

        Source-module bookkeeping currently lives on the AST decl_node
        (`_source_module`), set by the driver as it merges modules. The
        attribute may be absent for synthesized or one-off declarations.
        """
        sym = self.symbol_table.get_symbol(symbol_id)
        if sym is None or sym.decl_node is None:
            return None
        return getattr(sym.decl_node, "_source_module", None)

    def _find_method_symbol(
        self,
        type_decl: ast.StructDecl | ast.EnumDecl,
        method_name: str
    ) -> Optional[SymbolId]:
        """Find the symbol ID for a method in a type declaration."""
        from . import ast

        for member in type_decl.members:
            if isinstance(member, ast.FuncDecl) and member.name == method_name:
                return self.symbol_table.get_symbol_by_node(member)
        return None


def get_type_members(
    type_id: TypeId,
    type_table: TypeTable,
    symbol_table: SymbolTable
) -> TypeMembers:
    """Convenience function to get members for a type."""
    resolver = MemberResolver(type_table, symbol_table)
    return resolver.get_members(type_id)
