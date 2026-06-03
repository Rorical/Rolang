"""
Protocol Conformance Checking.

Verifies that concrete types implement all requirements of a protocol.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Set

from . import ast
from .types import (
    TypeId, TypeTable, TypeKind,
    ProtocolTypeData, FuncRequirement, PropRequirement,
    StructTypeData, EnumTypeData,
)
from .symbols import SymbolId, SymbolTable, SymbolKind, Namespace


@dataclass
class WitnessEntry:
    """A single entry in a witness table."""
    requirement_name: str
    implementation_symbol: Optional[SymbolId]
    implementation_name: str
    is_method: bool = True


@dataclass
class ConformanceResult:
    """Result of checking protocol conformance."""
    conforms: bool
    witnesses: List[WitnessEntry] = field(default_factory=list)
    missing_requirements: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


class ConformanceChecker:
    """
    Checks whether a concrete type conforms to a protocol.

    Conformance requires:
    1. All required methods are implemented with matching signatures
    2. All required properties are implemented with matching types
    """

    def __init__(
        self,
        type_table: TypeTable,
        symbol_table: SymbolTable,
    ) -> None:
        self.type_table = type_table
        self.symbol_table = symbol_table

        # Cache for computed conformances
        self._conformance_cache: Dict[Tuple[TypeId, TypeId], ConformanceResult] = {}

        # Map from (concrete_type, protocol) -> list of extension symbols
        self._extensions: Dict[Tuple[TypeId, TypeId], List[SymbolId]] = {}

    def register_extension(
        self,
        concrete_type: TypeId,
        protocol_type: TypeId,
        extension_symbol: SymbolId,
    ) -> None:
        """Register an extension that provides protocol conformance."""
        key = (concrete_type, protocol_type)
        if key not in self._extensions:
            self._extensions[key] = []
        self._extensions[key].append(extension_symbol)
        # Invalidate every cached conformance for this concrete type, not just
        # the (concrete, protocol) pair. `_find_func_witness` searches *all*
        # extensions registered for the concrete type regardless of the
        # protocol they were registered under, so a new extension can change
        # the conformance result for a *different* protocol that was already
        # cached as non-conforming.
        stale = [k for k in self._conformance_cache if k[0] == concrete_type]
        for k in stale:
            del self._conformance_cache[k]

    def check_conformance(
        self,
        concrete_type: TypeId,
        protocol_type: TypeId,
    ) -> ConformanceResult:
        """
        Check if a concrete type conforms to a protocol.

        Returns a ConformanceResult with:
        - conforms: True if all requirements are satisfied
        - witnesses: List of implementations for each requirement
        - missing_requirements: Names of unimplemented requirements
        - errors: Any error messages
        """
        # Check cache
        cache_key = (concrete_type, protocol_type)
        if cache_key in self._conformance_cache:
            return self._conformance_cache[cache_key]

        result = self._check_conformance_impl(concrete_type, protocol_type)
        self._conformance_cache[cache_key] = result
        return result

    def _check_conformance_impl(
        self,
        concrete_type: TypeId,
        protocol_type: TypeId,
    ) -> ConformanceResult:
        """Implementation of conformance checking."""
        # Get protocol info
        protocol_info = self.type_table.get_type(protocol_type)
        if protocol_info is None or protocol_info.kind != TypeKind.PROTOCOL:
            return ConformanceResult(
                conforms=False,
                errors=[f"Not a protocol type: {protocol_type}"]
            )

        protocol_data = protocol_info.data
        if not isinstance(protocol_data, ProtocolTypeData):
            return ConformanceResult(
                conforms=False,
                errors=["Invalid protocol type data"]
            )

        # Get concrete type info
        concrete_info = self.type_table.get_type(concrete_type)
        if concrete_info is None:
            return ConformanceResult(
                conforms=False,
                errors=[f"Unknown type: {concrete_type}"]
            )

        witnesses: List[WitnessEntry] = []
        missing: List[str] = []
        errors: List[str] = []

        # Check function requirements
        for func_req in protocol_data.func_requirements:
            witness = self._find_func_witness(concrete_type, func_req, errors)
            if witness is not None:
                witnesses.append(witness)
            elif not any(err.startswith(f"Method '{func_req.name}' ") for err in errors):
                missing.append(func_req.name)

        # Check property requirements
        for prop_req in protocol_data.prop_requirements:
            witness = self._find_prop_witness(concrete_type, prop_req, errors)
            if witness is not None:
                witnesses.append(witness)
            elif not any(err.startswith(f"Property '{prop_req.name}' ") for err in errors):
                missing.append(prop_req.name)

        conforms = len(missing) == 0 and len(errors) == 0

        return ConformanceResult(
            conforms=conforms,
            witnesses=witnesses,
            missing_requirements=missing,
            errors=errors,
        )

    def _find_func_witness(
        self,
        concrete_type: TypeId,
        requirement: FuncRequirement,
        errors: List[str],
    ) -> Optional[WitnessEntry]:
        """Find a method implementation for a function requirement."""
        decl = self._get_type_decl(concrete_type)
        if decl is None:
            return None

        for member in self._iter_type_methods(decl):
            if member.name != requirement.name:
                continue

            mismatch = self._func_mismatch(member, requirement)
            if mismatch:
                errors.append(f"Method '{requirement.name}' {mismatch}")
                return None

            return WitnessEntry(
                requirement_name=requirement.name,
                implementation_symbol=self._symbol_for_node(member),
                implementation_name=member.name,
                is_method=True,
            )

        # Check extensions
        for key, extension_symbols in self._extensions.items():
            ext_concrete, _ = key
            if ext_concrete == concrete_type:
                for ext_symbol_id in extension_symbols:
                    ext_symbol = self.symbol_table.get_symbol(ext_symbol_id)
                    if ext_symbol is None:
                        continue
                    ext_decl = ext_symbol.decl_node
                    if not isinstance(ext_decl, ast.ExtensionDecl):
                        continue
                    for member in ext_decl.members:
                        if isinstance(member, ast.FuncDecl) and member.name == requirement.name:
                            mismatch = self._func_mismatch(member, requirement)
                            if mismatch:
                                errors.append(f"Method '{requirement.name}' {mismatch}")
                                return None
                            return WitnessEntry(
                                requirement_name=requirement.name,
                                implementation_symbol=self._symbol_for_node(member),
                                implementation_name=member.name,
                                is_method=True,
                            )

        return None

    def _find_prop_witness(
        self,
        concrete_type: TypeId,
        requirement: PropRequirement,
        errors: List[str],
    ) -> Optional[WitnessEntry]:
        """Find a property implementation for a property requirement."""
        decl = self._get_type_decl(concrete_type)
        if decl is None:
            return None

        for member in self._iter_type_properties(decl):
            if member.name != requirement.name:
                continue

            if requirement.has_setter and not member.is_mutable:
                errors.append(
                    f"Property '{requirement.name}' must be mutable to satisfy set requirement"
                )
                return None

            prop_type = (
                self._resolve_ast_type(member.type_annotation)
                if member.type_annotation
                else None
            )
            if prop_type != requirement.type_id:
                got = self.type_table.format_type(prop_type) if prop_type else "unknown"
                expected = self.type_table.format_type(requirement.type_id)
                errors.append(
                    f"Property '{requirement.name}' has type {got}, expected {expected}"
                )
                return None

            return WitnessEntry(
                requirement_name=requirement.name,
                implementation_symbol=self._symbol_for_node(member),
                implementation_name=member.name,
                is_method=False,
            )

        return None

    def _get_type_decl(
        self,
        concrete_type: TypeId,
    ) -> Optional[ast.StructDecl | ast.EnumDecl]:
        """Return the AST declaration for a concrete struct or enum type."""
        info = self.type_table.get_type(concrete_type)
        if info is None:
            return None

        symbol_id: Optional[SymbolId] = None
        if info.kind == TypeKind.STRUCT and isinstance(info.data, StructTypeData):
            symbol_id = info.data.symbol_id
        elif info.kind == TypeKind.ENUM and isinstance(info.data, EnumTypeData):
            symbol_id = info.data.symbol_id

        if symbol_id is None:
            return None

        symbol = self.symbol_table.get_symbol(symbol_id)
        if symbol is None:
            return None

        decl = symbol.decl_node
        if isinstance(decl, (ast.StructDecl, ast.EnumDecl)):
            return decl
        return None

    def _iter_type_methods(
        self,
        decl: ast.StructDecl | ast.EnumDecl,
    ) -> List[ast.FuncDecl]:
        """Return function members declared directly on a struct or enum."""
        members = decl.members if hasattr(decl, "members") else []
        return [member for member in members if isinstance(member, ast.FuncDecl)]

    def _iter_type_properties(
        self,
        decl: ast.StructDecl | ast.EnumDecl,
    ) -> List[ast.PropertyDecl]:
        """Return stored properties declared directly on a struct."""
        if not isinstance(decl, ast.StructDecl):
            return []
        return [
            member for member in decl.members
            if isinstance(member, ast.PropertyDecl)
        ]

    def _func_mismatch(
        self,
        implementation: ast.FuncDecl,
        requirement: FuncRequirement,
    ) -> Optional[str]:
        """Return a mismatch explanation, or None when signatures match."""
        if implementation.is_async != requirement.is_async:
            expected = "async " if requirement.is_async else ""
            got = "async " if implementation.is_async else ""
            return f"asyncness mismatch: expected {expected}function, got {got}function"

        if len(implementation.params) != len(requirement.params):
            return (
                f"parameter count mismatch: expected {len(requirement.params)}, "
                f"got {len(implementation.params)}"
            )

        for index, (param, expected_type) in enumerate(
            zip(implementation.params, requirement.params),
            start=1,
        ):
            actual_type = self._resolve_ast_type(param.type_annotation)
            if actual_type != expected_type:
                return (
                    f"parameter {index} type mismatch: expected "
                    f"{self.type_table.format_type(expected_type)}, got "
                    f"{self.type_table.format_type(actual_type)}"
                )

        actual_return = (
            self._resolve_ast_type(implementation.return_type)
            if implementation.return_type
            else self.type_table.void_type
        )
        if actual_return != requirement.return_type:
            return (
                f"return type mismatch: expected "
                f"{self.type_table.format_type(requirement.return_type)}, got "
                f"{self.type_table.format_type(actual_return)}"
            )

        return None

    def _resolve_ast_type(self, type_node: Optional[ast.Type]) -> TypeId:
        """Resolve the subset of AST type nodes needed for conformance checks."""
        if type_node is None:
            return self.type_table.error_type

        if isinstance(type_node, ast.BuiltinType):
            return self.type_table.get_builtin(type_node.name) or self.type_table.error_type

        if isinstance(type_node, ast.NamedType):
            symbol = self._lookup_type_symbol(type_node.name)
            type_args = tuple(
                self._resolve_ast_type(arg)
                for arg in type_node.generic_args
            )
            if symbol is None:
                return self.type_table.error_type
            if symbol.kind == SymbolKind.STRUCT:
                return self.type_table.make_struct(symbol.id, type_args)
            if symbol.kind == SymbolKind.ENUM:
                return self.type_table.make_enum(symbol.id, type_args)
            if symbol.kind == SymbolKind.PROTOCOL:
                protocol_type = self.type_table.get_protocol_type(symbol.id)
                return protocol_type if protocol_type else self.type_table.error_type
            if symbol.kind == SymbolKind.BUILTIN_TYPE:
                builtin = self.type_table.get_builtin(symbol.name)
                if builtin:
                    return builtin

        if isinstance(type_node, ast.OptionalType) and type_node.inner:
            return self.type_table.make_optional(self._resolve_ast_type(type_node.inner))

        # `[T]` and `[K: V]` are now sugar for `Vec<T>` and `Dict<K, V>` —
        # look up the std struct symbols and intern as STRUCT types.
        if isinstance(type_node, ast.ArrayType) and type_node.element:
            vec_sym = self.symbol_table.get_type_symbol("Vec")
            if vec_sym is not None:
                return self.type_table.make_struct(
                    vec_sym, (self._resolve_ast_type(type_node.element),)
                )
            return self.type_table.error_type

        if isinstance(type_node, ast.DictType) and type_node.key and type_node.value:
            dict_sym = self.symbol_table.get_type_symbol("Dict")
            if dict_sym is not None:
                return self.type_table.make_struct(
                    dict_sym,
                    (
                        self._resolve_ast_type(type_node.key),
                        self._resolve_ast_type(type_node.value),
                    ),
                )
            return self.type_table.error_type

        if isinstance(type_node, ast.TupleType):
            return self.type_table.make_tuple(tuple(
                (label, self._resolve_ast_type(elem_type))
                for label, elem_type in type_node.elements
            ))

        if isinstance(type_node, ast.FunctionType):
            params = tuple(self._resolve_ast_type(param) for param in type_node.params)
            ret = (
                self._resolve_ast_type(type_node.return_type)
                if type_node.return_type
                else self.type_table.void_type
            )
            return self.type_table.make_function(params, ret, type_node.is_async)

        if isinstance(type_node, ast.AnyType) and type_node.protocol:
            protocol_type = self._resolve_ast_type(type_node.protocol)
            if self.type_table.is_protocol(protocol_type):
                return self.type_table.make_existential(protocol_type)

        if isinstance(type_node, ast.PointerType):
            return self.type_table.get_builtin("RawPtr") or self.type_table.error_type

        return self.type_table.error_type

    def _lookup_type_symbol(self, name: str):
        """Find a type symbol by name in O(1)."""
        builtin_id = self.symbol_table.get_builtin(name)
        if builtin_id is not None:
            return self.symbol_table.get_symbol(builtin_id)

        sid = self.symbol_table.get_type_symbol(name)
        if sid is not None:
            return self.symbol_table.get_symbol(sid)
        return None

    def _symbol_for_node(self, node: ast.Node) -> Optional[SymbolId]:
        """Find the symbol created for an AST declaration node in O(1)."""
        return self.symbol_table.get_symbol_by_node(node)

    def get_all_conformances(self, concrete_type: TypeId) -> List[TypeId]:
        """Get all protocols that a type conforms to."""
        conforming: List[TypeId] = []

        # Check all registered extensions
        for (ext_concrete, protocol), _ in self._extensions.items():
            if ext_concrete == concrete_type:
                result = self.check_conformance(concrete_type, protocol)
                if result.conforms:
                    conforming.append(protocol)

        return conforming


def check_conformance(
    concrete_type: TypeId,
    protocol_type: TypeId,
    type_table: TypeTable,
    symbol_table: SymbolTable,
) -> ConformanceResult:
    """
    Check if a concrete type conforms to a protocol.

    Convenience function that creates a ConformanceChecker.
    """
    checker = ConformanceChecker(type_table, symbol_table)
    return checker.check_conformance(concrete_type, protocol_type)
