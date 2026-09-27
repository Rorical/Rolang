"""Shared AST type-node to TypeId resolution."""

from __future__ import annotations

from collections.abc import Callable
from typing import Optional

from . import ast
from .symbols import SymbolId, SymbolKind, SymbolTable
from .types import TypeId, TypeTable


TypeResolverError = Callable[[str, str, Optional[ast.Node]], None]


class TypeResolver:
    """Resolve AST type annotations into canonical TypeIds."""

    def __init__(
        self,
        type_table: TypeTable,
        symbol_table: SymbolTable,
        node_symbols: Optional[dict[int, SymbolId]] = None,
        imported_symbols: Optional[dict[str, SymbolId]] = None,
        error_reporter: Optional[TypeResolverError] = None,
        allow_symbol_table_lookup: bool = False,
    ) -> None:
        self.type_table = type_table
        self.symbol_table = symbol_table
        self.node_symbols = node_symbols if node_symbols is not None else {}
        self.imported_symbols = imported_symbols if imported_symbols is not None else {}
        self.error_reporter = error_reporter
        self.allow_symbol_table_lookup = allow_symbol_table_lookup
        self._resolving_aliases: set[SymbolId] = set()

    # ------------------------------------------------------------------
    # Std collection helpers
    #
    # `Vec<T>` and `Dict<K, V>` are now the canonical compiler-blessed
    # collection types. The old `[T]` / `[K: V]` literal and annotation
    # syntax is sugar for these structs, so the resolver needs a fast
    # way to find them. Both come in via the implicit `vec.rl`/`dict.rl`
    # imports that the driver injects into every non-stdlib source.
    # ------------------------------------------------------------------

    def lookup_named_struct(self, name: str) -> Optional[SymbolId]:
        """Find a struct symbol by source-level name.

        Checks the importer's flat `imported_symbols` first (where
        `import "vec.rl"` lands `Vec` -> SymbolId), then falls back to
        the global type index (for stdlib code that declares the struct
        in its own module).
        """
        sid = self.imported_symbols.get(name)
        if sid is not None:
            return sid
        return self.symbol_table.get_type_symbol(name)

    def make_vec_type(self, element: TypeId) -> TypeId:
        """Return the `Vec<element>` struct type, or the error type."""
        sid = self.lookup_named_struct("Vec")
        if sid is None:
            self._error(
                "NOT_A_TYPE",
                "Vec<T> is not in scope; ensure 'vec.rl' is imported.",
            )
            return self.type_table.error_type
        return self.type_table.make_struct(sid, (element,))

    def make_dict_type(self, key: TypeId, value: TypeId) -> TypeId:
        """Return the `Dict<key, value>` struct type, or the error type."""
        sid = self.lookup_named_struct("Dict")
        if sid is None:
            self._error(
                "NOT_A_TYPE",
                "Dict<K, V> is not in scope; ensure 'dict.rl' is imported.",
            )
            return self.type_table.error_type
        return self.type_table.make_struct(sid, (key, value))

    def resolve_type_node(
        self,
        type_node: Optional[ast.Type],
        subst: Optional[dict[str, TypeId]] = None,
    ) -> TypeId:
        """Compatibility wrapper for callers that name the AST form explicitly."""
        return self.resolve(type_node, subst)

    def resolve(
        self,
        type_node: Optional[ast.Type],
        subst: Optional[dict[str, TypeId]] = None,
    ) -> TypeId:
        """Resolve an AST type node to a TypeId."""
        if type_node is None:
            return self.type_table.error_type

        if isinstance(type_node, ast.BuiltinType):
            type_id = self.type_table.get_builtin(type_node.name)
            if type_id:
                return type_id
            self._error("NOT_A_TYPE", f"Unknown type '{type_node.name}'")
            return self.type_table.error_type

        if isinstance(type_node, ast.NamedType):
            return self.resolve_named(type_node, subst)

        if isinstance(type_node, ast.OptionalType):
            if type_node.inner:
                return self.type_table.make_optional(self.resolve(type_node.inner, subst))
            return self.type_table.error_type

        if isinstance(type_node, ast.ArrayType):
            # `[T]` is sugar for `Vec<T>`; the legacy builtin array type
            # has been removed.
            if type_node.element:
                return self.make_vec_type(self.resolve(type_node.element, subst))
            return self.type_table.error_type

        if isinstance(type_node, ast.DictType):
            # `[K: V]` is sugar for `Dict<K, V>`.
            if type_node.key and type_node.value:
                return self.make_dict_type(
                    self.resolve(type_node.key, subst),
                    self.resolve(type_node.value, subst),
                )
            return self.type_table.error_type

        if isinstance(type_node, ast.TupleType):
            elements = tuple(
                (label, self.resolve(elem_type, subst))
                for label, elem_type in type_node.elements
            )
            return self.type_table.make_tuple(elements)

        if isinstance(type_node, ast.FunctionType):
            params = tuple(self.resolve(param, subst) for param in type_node.params)
            ret = (
                self.resolve(type_node.return_type, subst)
                if type_node.return_type
                else self.type_table.void_type
            )
            return self.type_table.make_function(params, ret, type_node.is_async)

        if isinstance(type_node, ast.AnyType):
            if type_node.protocol:
                protocol_type = self.resolve_named(type_node.protocol, subst)
                if self.type_table.is_protocol(protocol_type):
                    return self.type_table.make_existential(protocol_type)
                self._error(
                    "NOT_A_TYPE",
                    f"'{type_node.protocol.name}' is not a protocol",
                    type_node.protocol,
                )
            return self.type_table.error_type

        if isinstance(type_node, ast.PointerType):
            return self.type_table.get_builtin("RawPtr") or self.type_table.error_type

        return self.type_table.error_type

    def resolve_named(
        self,
        named: ast.NamedType,
        subst: Optional[dict[str, TypeId]] = None,
    ) -> TypeId:
        """Resolve a named type reference."""
        if (
            subst
            and not named.module_path
            and not named.generic_args
            and named.name in subst
        ):
            return subst[named.name]

        symbol_id = self._lookup_named_symbol(named)
        if symbol_id is None:
            builtin = self.type_table.get_builtin(named.name)
            if builtin:
                return builtin
            display_name = (
                ".".join(named.module_path) + "." + named.name
                if named.module_path
                else named.name
            )
            self._error("NOT_A_TYPE", f"Unknown type '{display_name}'", named)
            return self.type_table.error_type

        symbol = self.symbol_table.get_symbol(symbol_id)
        if symbol is None:
            return self.type_table.error_type

        type_args = tuple(self.resolve(arg, subst) for arg in named.generic_args)

        if symbol.kind == SymbolKind.TYPE_ALIAS:
            params = symbol.decl_node.generic_params
            if len(type_args) != len(params):
                message = (f"Type alias '{symbol.name}' expects {len(params)} generic argument(s), got {len(type_args)}"
                           if params else f"Type alias '{symbol.name}' does not accept generic arguments")
                self._error("GENERIC_ARG_COUNT", message, named)
                return self.type_table.error_type
            if symbol_id in self._resolving_aliases:
                self._error("NOT_A_TYPE", f"Cyclic type alias '{symbol.name}'", named)
                return self.type_table.error_type
            self._resolving_aliases.add(symbol_id)
            try:
                # Resolve in the declaration's scope using its bound AST nodes;
                # call-site generic substitutions must not capture alias names.
                return self.resolve(symbol.decl_node.aliased_type, dict(zip((p.name for p in params), type_args)))
            finally:
                self._resolving_aliases.remove(symbol_id)

        if symbol.kind == SymbolKind.STRUCT:
            expected = self._generic_arity(symbol)
            if named.generic_args and expected is not None and len(type_args) != expected:
                self._error(
                    "GENERIC_ARG_COUNT",
                    f"Struct '{symbol.name}' expects {expected} generic argument(s), got {len(type_args)}",
                    named,
                )
                return self.type_table.error_type
            return self.type_table.make_struct(symbol_id, type_args)
        if symbol.kind == SymbolKind.ENUM:
            expected = self._generic_arity(symbol)
            if named.generic_args and expected is not None and len(type_args) != expected:
                self._error(
                    "GENERIC_ARG_COUNT",
                    f"Enum '{symbol.name}' expects {expected} generic argument(s), got {len(type_args)}",
                    named,
                )
                return self.type_table.error_type
            return self.type_table.make_enum(symbol_id, type_args)
        if symbol.kind == SymbolKind.PROTOCOL:
            if type_args:
                self._error(
                    "GENERIC_ARG_COUNT",
                    f"Protocol '{symbol.name}' does not accept generic arguments",
                    named,
                )
                return self.type_table.error_type
            protocol_type = self.type_table.get_protocol_type(symbol_id)
            return protocol_type if protocol_type else self.type_table.error_type
        if symbol.kind == SymbolKind.GENERIC_PARAM:
            bounds: list[TypeId] = []
            if isinstance(symbol.decl_node, ast.GenericParam):
                for bound in (symbol.decl_node.bounds or ()):
                    bound_type = self.resolve(bound, subst)
                    if not self.type_table.is_error(bound_type):
                        bounds.append(bound_type)
            return self.type_table.make_type_variable(symbol.name, tuple(bounds))
        if symbol.kind == SymbolKind.BUILTIN_TYPE:
            builtin = self.type_table.get_builtin(symbol.name)
            if builtin:
                return builtin

        self._error("NOT_A_TYPE", f"'{named.name}' is not a type", named)
        return self.type_table.error_type

    def _generic_arity(self, symbol) -> Optional[int]:
        decl = getattr(symbol, "decl_node", None)
        params = getattr(decl, "generic_params", None)
        if params is None:
            return None
        return len(params)

    def _lookup_named_symbol(self, named: ast.NamedType) -> Optional[SymbolId]:
        symbol_id = self.node_symbols.get(id(named))
        if symbol_id is not None:
            return symbol_id

        if named.module_path:
            qualified = ".".join(named.module_path) + "." + named.name
            symbol_id = self.imported_symbols.get(qualified)
            if symbol_id is not None:
                return symbol_id

        # Look up a flat import binding. We deliberately do NOT fall back
        # to a fuzzy `endswith("." + name)` walk across imported_symbols
        # here: that produces non-deterministic matches when the same bare
        # type name exists under multiple aliased imports, and it makes
        # `import "lib.rl" as L` effectively no-op for types because
        # `L.Foo` registered as the only binding would still resolve
        # `Foo` directly.
        symbol_id = self.imported_symbols.get(named.name)
        if symbol_id is not None:
            return symbol_id

        if self.allow_symbol_table_lookup and not named.module_path:
            sid = self.symbol_table.get_type_symbol(named.name)
            if sid is not None:
                return sid
            for symbol in self.symbol_table.symbols.values():
                if symbol.name == named.name and symbol.kind == SymbolKind.GENERIC_PARAM:
                    return symbol.id

        return None

    def _error(
        self,
        kind: str,
        message: str,
        node: Optional[ast.Node] = None,
    ) -> None:
        if self.error_reporter is not None:
            self.error_reporter(kind, message, node)


def result_payloads(type_id, type_table, symbol_table, resolver):
    """Resolve a two-case Result's payloads, independent of case/type parameter order."""
    from .types import EnumTypeData
    info = type_table.get_type(type_id)
    if info is None or not isinstance(info.data, EnumTypeData):
        return None
    data = info.data
    symbol = symbol_table.get_symbol(data.symbol_id)
    if symbol is None or not isinstance(symbol.decl_node, ast.EnumDecl):
        return None
    decl = symbol.decl_node
    cases = [case for member in decl.members if isinstance(member, ast.EnumCaseDecl)
             for case in member.cases]
    if len(cases) != 2 or {case.name for case in cases} != {"ok", "err"}:
        return None
    if any(len(case.payload) != 1 for case in cases):
        return None
    subst = {param.name: arg for param, arg in zip(decl.generic_params, data.type_args)}
    return {case.name: resolver.resolve(case.payload[0][1], subst) for case in cases}
