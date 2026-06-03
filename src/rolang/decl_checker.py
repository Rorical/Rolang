"""Declaration type checking for Rolang."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

from . import ast
from .types import (
    TypeId, TypeKind,
    StructTypeData, EnumTypeData, FunctionTypeData,

    OptionalTypeData,
    FuncRequirement, PropRequirement,
    ExistentialTypeData,
)
from .symbols import SymbolKind
from .members import MethodInfo, FieldInfo, TypeMembers
from .checker_core import TypeErrorKind

if TYPE_CHECKING:
    from .checker import TypeChecker


class DeclChecker:
    """Check type declarations: functions, structs, enums, extensions."""

    def __init__(self, checker: TypeChecker) -> None:
        self._c = checker

    def _check_signature_visibility(
        self,
        func: ast.FuncDecl,
        param_types: List[TypeId],
        return_type: TypeId,
    ) -> None:
        """Reject `pub` functions whose signatures expose non-`pub` types.

        A non-`pub` type referenced through a public surface is a privacy
        leak: callers can hold values of a type they cannot name, depend
        on its layout, and pin it as their public ABI. We require every
        type mentioned in a `pub` function's parameter list, return
        type, or generic constraints to be `pub` itself (builtins and
        generic parameters are exempt).
        """
        seen: set = set()
        for ty in param_types:
            self._inspect_type_for_privacy_leak(func, ty, "parameter", seen)
        self._inspect_type_for_privacy_leak(func, return_type, "return type", seen)

    def _inspect_type_for_privacy_leak(
        self,
        func: ast.FuncDecl,
        type_id: TypeId,
        role: str,
        seen: set,
    ) -> None:
        """Walk one type expression and report any non-`pub` user symbol."""
        from .types import (
            StructTypeData, EnumTypeData, ProtocolTypeData,
            ExistentialTypeData, FunctionTypeData, ClosureTypeData,
            OptionalTypeData,
        )

        if type_id in seen:
            return
        seen.add(type_id)

        info = self._c.type_table.get_type(type_id)
        if info is None:
            return

        # If the type is anchored on a user symbol, check the symbol's visibility.
        symbol_id = None
        if isinstance(info.data, StructTypeData):
            symbol_id = info.data.symbol_id
        elif isinstance(info.data, EnumTypeData):
            symbol_id = info.data.symbol_id
        elif isinstance(info.data, ProtocolTypeData):
            symbol_id = info.data.symbol_id
        if symbol_id is not None:
            sym = self._c.symbol_table.get_symbol(symbol_id)
            if sym is not None:
                # Generic params and builtin types are always visible.
                if sym.kind not in (SymbolKind.GENERIC_PARAM, SymbolKind.BUILTIN_TYPE):
                    if getattr(sym, 'visibility', 'internal') != 'pub':
                        self._c._error(
                            TypeErrorKind.INVALID_OPERATION,
                            (
                                f"public function '{func.name}' exposes "
                                f"non-public type '{sym.name}' in its {role}"
                            ),
                            node=func,
                        )

        # Recurse into composite type arguments.
        if isinstance(info.data, (StructTypeData, EnumTypeData)):
            for ta in info.data.type_args:
                self._inspect_type_for_privacy_leak(func, ta, role, seen)
            if isinstance(info.data, StructTypeData) and info.data.symbol_id is None:
                for _, et in (info.data.anon_fields or ()):
                    self._inspect_type_for_privacy_leak(func, et, role, seen)
        elif isinstance(info.data, ExistentialTypeData):
            self._inspect_type_for_privacy_leak(func, info.data.protocol_id, role, seen)
        elif isinstance(info.data, FunctionTypeData):
            for pt in info.data.params:
                self._inspect_type_for_privacy_leak(func, pt, role, seen)
            self._inspect_type_for_privacy_leak(func, info.data.return_type, role, seen)
        elif isinstance(info.data, ClosureTypeData):
            for pt in info.data.params:
                self._inspect_type_for_privacy_leak(func, pt, role, seen)
            self._inspect_type_for_privacy_leak(func, info.data.return_type, role, seen)
        elif isinstance(info.data, OptionalTypeData):
            self._inspect_type_for_privacy_leak(func, info.data.inner, role, seen)

    def _report_extension_method_conflicts(
        self,
        type_name: str,
        conflicts: list,
        node: Optional[ast.Node] = None,
    ) -> None:
        """Convert register_extension() conflicts to type-checker errors.

        Each conflict is two methods that share a name on the same type
        and are visible from at least one common module. Surfacing them
        as a clear diagnostic also pre-empts the downstream codegen
        crash that would otherwise hit a `DuplicatedNameError` from
        llvmlite.
        """
        seen: set = set()
        for prev, new in conflicts:
            key = (prev.symbol_id, new.symbol_id)
            if key in seen:
                continue
            seen.add(key)
            self._c._error(
                TypeErrorKind.DUPLICATE_MEMBER,
                f"Extension method '{new.name}' is already defined on type "
                f"'{type_name}' (conflicting declarations are visible to the "
                f"same module)",
                node=node,
            )

    def run(self, program) -> None:
        """Run all declaration checking passes."""
        for item in program.items:
            self._collect_type_decl(item)
        self._register_imported_types(program)
        for item in program.items:
            self._check_item(item)


    def _collect_type_decl(self, item: ast.TopLevelItem) -> None:
        """Collect type declaration for later reference."""
        if isinstance(item, ast.StructDecl):
            symbol_id = self._c.node_symbols.get(id(item))
            if symbol_id:
                # Register struct type
                self._c.type_table.make_struct(symbol_id)

        elif isinstance(item, ast.EnumDecl):
            symbol_id = self._c.node_symbols.get(id(item))
            if symbol_id:
                # Register enum type
                self._c.type_table.make_enum(symbol_id)
        elif isinstance(item, ast.ProtocolDecl):
            symbol_id = self._c.node_symbols.get(id(item))
            if symbol_id:
                func_requirements: List[FuncRequirement] = []
                prop_requirements: List[PropRequirement] = []

                for member in item.members:
                    if isinstance(member, ast.ProtocolFuncReq):
                        params = tuple(
                            self._c._resolve_type(param.type_annotation)
                            for param in member.params
                        )
                        return_type = (
                            self._c._resolve_type(member.return_type)
                            if member.return_type
                            else self._c.type_table.void_type
                        )
                        func_requirements.append(FuncRequirement(
                            name=member.name,
                            params=params,
                            return_type=return_type,
                            is_async=member.is_async,
                        ))
                    elif isinstance(member, ast.ProtocolPropReq):
                        prop_type = (
                            self._c._resolve_type(member.type_annotation)
                            if member.type_annotation
                            else self._c.type_table.error_type
                        )
                        prop_requirements.append(PropRequirement(
                            name=member.name,
                            type_id=prop_type,
                            has_getter=member.has_getter,
                            has_setter=member.has_setter,
                        ))

                self._c.type_table.make_protocol(
                    symbol_id,
                    tuple(func_requirements),
                    tuple(prop_requirements),
                )

    # ========================= Item Checking =========================

    def _register_imported_types(self, program: ast.Program) -> None:
        """Register struct/enum/protocol types from imported modules in TypeTable."""
        for name, symbol_id in self._c.imported_symbols.items():
            if "." in name:
                continue
            sym = self._c.symbol_table.get_symbol(symbol_id)
            if sym is None or sym.decl_node is None:
                continue
            if sym.kind == SymbolKind.STRUCT:
                self._c.type_table.make_struct(symbol_id, ())
            elif sym.kind == SymbolKind.ENUM:
                self._c.type_table.make_enum(symbol_id, ())
            elif sym.kind == SymbolKind.PROTOCOL:
                self._c.type_table.get_protocol_type(symbol_id)

        # Register imported extension methods in the local MemberResolver
        for type_name, methods in self._c.imported_extension_methods.items():
            # Find the symbol for the extended type
            type_sym_id = None
            for sid, s in self._c.symbol_table.symbols.items():
                if s.name == type_name and s.kind in (SymbolKind.STRUCT, SymbolKind.ENUM, SymbolKind.BUILTIN_TYPE):
                    type_sym_id = sid
                    break
            if type_sym_id is None:
                continue

            method_infos: list = []
            for method_name, method_sym_id in methods:
                method_sym = self._c.symbol_table.get_symbol(method_sym_id)
                if method_sym is None or method_sym.decl_node is None:
                    continue
                from . import ast as ast_module
                if not isinstance(method_sym.decl_node, ast_module.FuncDecl):
                    continue
                func = method_sym.decl_node
                # Resolve the method signature
                params = []
                for param in func.params:
                    param_type = self._c._resolve_type(param.type_annotation)
                    params.append(param_type)
                ret_type = self._c._resolve_type(func.return_type) if func.return_type else self._c.type_table.void_type
                signature = self._c.type_table.make_function(params=tuple(params), return_type=ret_type, is_async=func.is_async)
                method_infos.append(MethodInfo(
                    name=method_name,
                    symbol_id=method_sym_id,
                    signature=signature,
                    is_static=func.is_static,
                ))

            if method_infos:
                conflicts = self._c.member_resolver.register_extension(type_sym_id, method_infos)
                self._report_extension_method_conflicts(type_name, conflicts)

    def _check_item(self, item: ast.TopLevelItem) -> None:
        """Type check a top-level item."""
        # Tell the member resolver which source module this item came
        # from so non-`pub` extension methods are scoped to their
        # declaring module. The merge step (driver._compile_unified)
        # tags every kept top-level item with `_source_module`.
        prev_source_module = self._c.member_resolver._current_source_module
        self._c.member_resolver.set_current_source_module(
            getattr(item, '_source_module', None)
        )
        try:
            if isinstance(item, ast.FuncDecl):
                if item.is_static:
                    self._c._error(
                        TypeErrorKind.INVALID_OPERATION,
                        "'static' is only valid on methods inside a type or extension",
                        node=item,
                    )
                self._c._check_func_decl(item)
                # Privacy: a top-level `pub` function's signature must
                # not mention non-`pub` user types. Otherwise importers
                # can hold values of a private type they cannot name.
                # We only enforce this for top-level functions —
                # methods inside structs/extensions are gated by their
                # receiver type's visibility.
                if getattr(item, 'visibility', 'internal') == 'pub':
                    self._check_top_level_func_visibility(item)
            elif isinstance(item, ast.ExternFuncDecl):
                self._check_extern_func(item)
            elif isinstance(item, ast.StructDecl):
                self._c._check_struct_decl(item)
            elif isinstance(item, ast.EnumDecl):
                self._c._check_enum_decl(item)
            elif isinstance(item, ast.ExtensionDecl):
                self._check_extension(item)
        finally:
            self._c.member_resolver.set_current_source_module(prev_source_module)

    def _check_top_level_func_visibility(self, func: ast.FuncDecl) -> None:
        """Run the signature-visibility leak check for a top-level pub function."""
        param_types: List[TypeId] = []
        for param in func.params:
            param_types.append(self._c._resolve_type(param.type_annotation))
        return_type = (
            self._c._resolve_type(func.return_type)
            if func.return_type
            else self._c.type_table.void_type
        )
        self._check_signature_visibility(func, param_types, return_type)

    def _check_func_decl(self, func: ast.FuncDecl) -> None:
        """Type check a function declaration."""
        # If we're in a type context (struct/enum), bind 'self' to that type
        # The resolver created a self symbol and stored it in self_symbols[func_symbol]
        if self._c._current_self_type is not None and not func.is_static:
            func_symbol_id = self._c.node_symbols.get(id(func))
            if func_symbol_id:
                self_symbol_id = self._c.self_symbols.get(func_symbol_id)
                if self_symbol_id:
                    self._c._type_env[self_symbol_id] = self._c._current_self_type

        # Resolve parameter types and bind them
        param_types: List[TypeId] = []
        for param in func.params:
            param_type = self._c._resolve_type(param.type_annotation)
            param_types.append(param_type)
            # Bind parameter symbol to its type
            param_symbol_id = self._c.node_symbols.get(id(param))
            if param_symbol_id:
                self._c._type_env[param_symbol_id] = param_type
            if param.default_value is not None:
                default_type = self._c._infer_with_expected(param.default_value, param_type)
                self._c._check_assignable(
                    default_type,
                    param_type,
                    f"default value for parameter '{param.internal_name}'",
                    node=param,
                )

        # Resolve return type
        return_type = self._c._resolve_type(func.return_type) if func.return_type else self._c.type_table.void_type

        # `throws`: enforce that the function returns a Result-shaped type
        # (an enum). `try` / `?` already require a Result-shaped return type,
        # so this brings the function declaration into alignment.
        if func.throws:
            ret_info = self._c.type_table.get_type(return_type)
            if ret_info is None or ret_info.kind != TypeKind.ENUM:
                self._c._error(
                    TypeErrorKind.INVALID_OPERATION,
                    f"function '{func.name}' is declared 'throws' but does not "
                    "return a Result-shaped type; declare a Result<T, E> return "
                    "type or remove 'throws'"
                )

        # Store function's return type for return statement checking
        old_return_type = self._c._current_function_return
        self._c._current_function_return = return_type

        # Track async function context
        old_in_async = self._c._in_async_function
        self._c._in_async_function = func.is_async

        # Check body
        if func.body:
            self._c.stmt_checker._check_block(func.body)
            if return_type != self._c.type_table.void_type and not self._c.stmt_checker._definitely_returns_block(func.body):
                self._c._error(
                    TypeErrorKind.TYPE_MISMATCH,
                    f"function '{func.name}' must return a value of type "
                    f"{self._c.type_table.format_type(return_type)} on all paths",
                    node=func,
                )

        self._c._in_async_function = old_in_async
        self._c._current_function_return = old_return_type

    def _check_extern_func(self, func: ast.ExternFuncDecl) -> None:
        """Type check an extern function declaration."""
        # Just resolve types, no body to check
        for param in func.params:
            self._c._resolve_type(param.type_annotation)
        if func.return_type:
            self._c._resolve_type(func.return_type)

    def _check_struct_decl(self, struct: ast.StructDecl) -> None:
        """Type check a struct declaration."""
        # Get struct type for self binding. For a generic struct we instantiate
        # `self` with type variables that carry the declared bounds, so member
        # method bodies can resolve protocol-bound calls like `self.item.show()`.
        struct_symbol_id = self._c.node_symbols.get(id(struct))
        struct_type = None
        if struct_symbol_id:
            type_args = self._c._make_generic_param_type_args(struct.generic_params)
            struct_type = self._c.type_table.make_struct(struct_symbol_id, type_args)

        old_self_type = self._c._current_self_type
        self._c._current_self_type = struct_type

        for member in struct.members:
            if isinstance(member, ast.PropertyDecl):
                # Resolve property type
                if member.type_annotation:
                    prop_type = self._c._resolve_type(member.type_annotation)
                    # Bind property to its type
                    prop_symbol_id = self._c.node_symbols.get(id(member))
                    if prop_symbol_id:
                        self._c._type_env[prop_symbol_id] = prop_type

                # Check initializer if present
                if member.initializer:
                    init_type = self._c._infer_expr(member.initializer)
                    if member.type_annotation:
                        prop_type = self._c._resolve_type(member.type_annotation)
                        self._c._check_assignable(init_type, prop_type, "property initializer")

            elif isinstance(member, ast.FuncDecl):
                self._c._check_func_decl(member)

        # Populate pre-resolved member cache for MemberResolver
        if struct_symbol_id is not None:
            fields: Dict[str, FieldInfo] = {}
            methods: Dict[str, MethodInfo] = {}
            decl_module = getattr(struct, "_source_module", None)
            for i, member in enumerate(struct.members):
                if isinstance(member, ast.PropertyDecl):
                    field_type = self._c._resolve_type(member.type_annotation) if member.type_annotation else self._c.type_table.error_type
                    fields[member.name] = FieldInfo(
                        name=member.name, type_id=field_type,
                        is_mutable=member.is_mutable, index=i,
                        visibility=member.visibility,
                        source_module=decl_module,
                    )
                elif isinstance(member, ast.FuncDecl):
                    method_sym_id = self._c.node_symbols.get(id(member))
                    if method_sym_id:
                        func_type = self._c.type_table.make_function(
                            tuple(self._c._resolve_type(p.type_annotation) for p in member.params),
                            self._c._resolve_type(member.return_type) if member.return_type else self._c.type_table.void_type,
                            member.is_async,
                        )
                        methods[member.name] = MethodInfo(
                            name=member.name, symbol_id=method_sym_id,
                            signature=func_type,
                            is_static=member.is_static,
                        )
            gen_names = tuple(p.name for p in struct.generic_params)
            self._c.type_table.set_type_members(struct_symbol_id, TypeMembers(
                fields=fields, methods=methods, generic_param_names=gen_names,
            ))

        self._c._current_self_type = old_self_type

    def _check_enum_decl(self, enum: ast.EnumDecl) -> None:
        """Type check an enum declaration."""
        enum_symbol_id = self._c.node_symbols.get(id(enum))
        enum_type = None
        if enum_symbol_id:
            type_args = self._c._make_generic_param_type_args(enum.generic_params)
            enum_type = self._c.type_table.make_enum(enum_symbol_id, type_args)

        old_self_type = self._c._current_self_type
        self._c._current_self_type = enum_type

        for member in enum.members:
            if isinstance(member, ast.EnumCaseDecl):
                for case in member.cases:
                    # Resolve payload types
                    for _, payload_type in case.payload:
                        self._c._resolve_type(payload_type)

            elif isinstance(member, ast.FuncDecl):
                self._c._check_func_decl(member)

        # Populate pre-resolved member cache for MemberResolver
        if enum_symbol_id is not None:
            methods: Dict[str, MethodInfo] = {}
            for member in enum.members:
                if isinstance(member, ast.FuncDecl):
                    method_sym_id = self._c.node_symbols.get(id(member))
                    if method_sym_id:
                        func_type = self._c.type_table.make_function(
                            tuple(self._c._resolve_type(p.type_annotation) for p in member.params),
                            self._c._resolve_type(member.return_type) if member.return_type else self._c.type_table.void_type,
                            member.is_async,
                        )
                        methods[member.name] = MethodInfo(
                            name=member.name, symbol_id=method_sym_id,
                            signature=func_type,
                            is_static=member.is_static,
                        )
            gen_names = tuple(p.name for p in enum.generic_params)
            self._c.type_table.set_type_members(enum_symbol_id, TypeMembers(
                fields={}, methods=methods, generic_param_names=gen_names,
            ))

        self._c._current_self_type = old_self_type

    def _make_generic_param_type_args(
        self, generic_params: List[ast.GenericParam]
    ) -> Tuple[TypeId, ...]:
        """Create type variables for each generic param, carrying their bounds."""
        return self._c.generic_inference.make_generic_param_type_args(generic_params)

    def _check_extension(self, ext: ast.ExtensionDecl) -> None:
        """Type check an extension declaration."""
        # Get extended type and its symbol for self binding and registration
        extended_type = None
        extended_type_symbol = None
        if ext.extended_type:
            extended_type = self._c._resolve_named_type(ext.extended_type)
            # Get the symbol from the named type
            extended_type_symbol = self._c.node_symbols.get(id(ext.extended_type))
            # For builtin types (String, etc.), look up the builtin symbol
            if extended_type_symbol is None:
                info = self._c.type_table.get_type(extended_type)
                if info and info.kind == TypeKind.PRIMITIVE:
                    if isinstance(info.data, PrimitiveTypeData):
                        extended_type_symbol = self._c.symbol_table.get_builtin(ext.extended_type.name)

        # Set self type for methods
        old_self_type = self._c._current_self_type
        self._c._current_self_type = extended_type

        # Check members and collect extension methods
        extension_methods: List[MethodInfo] = []
        ext_visibility = getattr(ext, 'visibility', 'internal')
        ext_source_module = getattr(ext, '_source_module', None)
        for member in ext.members:
            if isinstance(member, ast.FuncDecl):
                self._c._check_func_decl(member)
                # Create MethodInfo for this extension method
                method_symbol = self._c.node_symbols.get(id(member))
                if method_symbol:
                    # Build the method signature
                    param_types = tuple(
                        self._c._resolve_type(p.type_annotation)
                        for p in member.params
                    )
                    return_type = (
                        self._c._resolve_type(member.return_type)
                        if member.return_type
                        else self._c.type_table.void_type
                    )
                    method_signature = self._c.type_table.make_function(
                        param_types, return_type, member.is_async
                    )
                    extension_methods.append(MethodInfo(
                        name=member.name,
                        symbol_id=method_symbol,
                        signature=method_signature,
                        is_static=member.is_static,
                        visibility=ext_visibility,
                        source_module=ext_source_module,
                    ))
            elif isinstance(member, ast.PropertyDecl):
                if member.type_annotation:
                    self._c._resolve_type(member.type_annotation)

        self._c._current_self_type = old_self_type

        # Register extension methods with member resolver
        if extended_type_symbol is not None and extension_methods:
            conflicts = self._c.member_resolver.register_extension(
                extended_type_symbol, extension_methods
            )
            type_name = ext.extended_type.name if isinstance(ext.extended_type, ast.NamedType) else "?"
            self._report_extension_method_conflicts(type_name, conflicts, node=ext)

        # Validate any declared protocol conformances and register them.
        if ext.conformances and extended_type is not None:
            ext_symbol = self._c.node_symbols.get(id(ext))
            for conformance_node in ext.conformances:
                protocol_type = self._c._resolve_named_type(conformance_node)
                if self._c.type_table.is_error(protocol_type):
                    continue
                if not self._c.type_table.is_protocol(protocol_type):
                    self._c._error(
                        TypeErrorKind.NOT_A_PROTOCOL,
                        f"'{conformance_node.name}' is not a protocol; "
                        f"only protocols can appear in an extension's conformance clause",
                    )
                    continue

                # Register before checking so the conformance checker can find
                # methods defined in this extension.
                if ext_symbol is not None:
                    self._c.conformance_checker.register_extension(
                        extended_type, protocol_type, ext_symbol,
                    )

                result = self._c.conformance_checker.check_conformance(
                    extended_type, protocol_type
                )
                if not result.conforms:
                    parts: List[str] = []
                    if result.missing_requirements:
                        parts.append(
                            "missing requirements: "
                            + ", ".join(result.missing_requirements)
                        )
                    if result.errors:
                        parts.append(result.errors[0])
                    detail = "; " + "; ".join(parts) if parts else ""
                    self._c._error(
                        TypeErrorKind.PROTOCOL_NOT_SATISFIED,
                        f"Type {self._c.type_table.format_type(extended_type)} "
                        f"does not conform to {conformance_node.name}{detail}",
                    )
