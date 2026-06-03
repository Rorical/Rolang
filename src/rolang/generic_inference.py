"""Generic type inference and substitution for the type checker."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

from . import ast
from .types import (
    TypeId,
    TypeKind,
    TypeVariableData,
    StructTypeData,
    EnumTypeData,
    StructTypeData,
    FunctionTypeData,
    OptionalTypeData,
)
from .symbols import Symbol, SymbolId

if TYPE_CHECKING:
    from .checker import TypeChecker


class GenericInference:
    """Generic type inference and substitution for the type checker."""

    def __init__(self, checker: TypeChecker) -> None:
        self._c = checker

    def make_generic_param_type_args(
        self, generic_params: List[ast.GenericParam]
    ) -> Tuple[TypeId, ...]:
        """Create type variables for each generic param, carrying their bounds."""
        args: List[TypeId] = []
        for param in generic_params:
            bounds: List[TypeId] = []
            for bound in (param.bounds or ()):
                bound_type = self._c._resolve_type(bound)
                if not self._c.type_table.is_error(bound_type):
                    bounds.append(bound_type)
            args.append(
                self._c.type_table.make_type_variable(param.name, tuple(bounds))
            )
        return tuple(args)

    def infer_generic_call_args(
        self,
        callee_symbol: SymbolId,
        call: ast.Call,
        expected_type: Optional[TypeId] = None,
    ) -> Dict[str, TypeId]:
        """Infer generic function type parameters from concrete call arguments."""
        symbol = self._c.symbol_table.get_symbol(callee_symbol)
        if symbol is None or not isinstance(symbol.decl_node, ast.FuncDecl):
            return {}

        decl = symbol.decl_node
        if not decl.generic_params:
            return {}

        inferred: Dict[str, TypeId] = {}
        generic_names = {param.name for param in decl.generic_params}

        for i, arg in enumerate(call.arguments):
            if i >= len(decl.params) or arg.value is None:
                break
            arg_type = self._c.expr_types.get(id(arg.value))
            if arg_type is None:
                arg_type = self._c._infer_expr(arg.value)
            self._infer_type_node_generics(
                decl.params[i].type_annotation,
                arg_type,
                generic_names,
                inferred,
            )

        if expected_type is not None and decl.return_type is not None:
            self._infer_type_node_generics(
                decl.return_type,
                expected_type,
                generic_names,
                inferred,
            )

        return inferred

    def _infer_type_node_generics(
        self,
        type_node: Optional[ast.Type],
        concrete_type: TypeId,
        generic_names: set[str],
        inferred: Dict[str, TypeId],
    ) -> None:
        """Unify an annotation against a concrete type for generic inference."""
        if type_node is None:
            return

        if isinstance(type_node, ast.NamedType):
            if not type_node.generic_args and type_node.name in generic_names:
                inferred.setdefault(type_node.name, concrete_type)
                return
            info = self._c.type_table.get_type(concrete_type)
            if info and info.kind == TypeKind.STRUCT and isinstance(info.data, StructTypeData):
                for node_arg, concrete_arg in zip(type_node.generic_args, info.data.type_args):
                    self._infer_type_node_generics(node_arg, concrete_arg, generic_names, inferred)
            elif info and info.kind == TypeKind.ENUM and isinstance(info.data, EnumTypeData):
                for node_arg, concrete_arg in zip(type_node.generic_args, info.data.type_args):
                    self._infer_type_node_generics(node_arg, concrete_arg, generic_names, inferred)
            return

        if isinstance(type_node, ast.ArrayType):
            # `[T]` is sugar for `Vec<T>` STRUCT now — recurse into the
            # concrete struct's first type arg.
            info = self._c.type_table.get_type(concrete_type)
            if info and info.kind == TypeKind.STRUCT and isinstance(info.data, StructTypeData):
                if info.data.type_args:
                    self._infer_type_node_generics(
                        type_node.element, info.data.type_args[0], generic_names, inferred
                    )
            return

        if isinstance(type_node, ast.DictType):
            # `[K: V]` is sugar for `Dict<K, V>` STRUCT now.
            info = self._c.type_table.get_type(concrete_type)
            if info and info.kind == TypeKind.STRUCT and isinstance(info.data, StructTypeData):
                if len(info.data.type_args) >= 2:
                    self._infer_type_node_generics(
                        type_node.key, info.data.type_args[0], generic_names, inferred
                    )
                    self._infer_type_node_generics(
                        type_node.value, info.data.type_args[1], generic_names, inferred
                    )
            return

        if isinstance(type_node, ast.OptionalType):
            info = self._c.type_table.get_type(concrete_type)
            if info and info.kind == TypeKind.OPTIONAL and isinstance(info.data, OptionalTypeData):
                self._infer_type_node_generics(type_node.inner, info.data.inner, generic_names, inferred)
            else:
                self._infer_type_node_generics(type_node.inner, concrete_type, generic_names, inferred)
            return

        if isinstance(type_node, ast.TupleType):
            info = self._c.type_table.get_type(concrete_type)
            if (info and info.kind == TypeKind.STRUCT
                    and isinstance(info.data, StructTypeData) and info.data.symbol_id is None):
                fields = info.data.anon_fields or ()
                for (_, node_elem), (_, concrete_elem) in zip(type_node.elements, fields):
                    self._infer_type_node_generics(node_elem, concrete_elem, generic_names, inferred)

    def substitute_type(self, type_id: TypeId, mapping: Dict[str, TypeId]) -> TypeId:
        """Apply a generic type substitution to a TypeId."""
        if not mapping:
            return type_id

        info = self._c.type_table.get_type(type_id)
        if info is None:
            return type_id

        if info.kind == TypeKind.TYPE_VARIABLE and isinstance(info.data, TypeVariableData):
            return mapping.get(info.data.name, type_id)

        if info.kind == TypeKind.STRUCT and isinstance(info.data, StructTypeData):
            if info.data.symbol_id is None:
                fields = info.data.anon_fields or ()
                new_fields = tuple(
                    (fname, self.substitute_type(t, mapping)) for fname, t in fields
                )
                return self._c.type_table.make_tuple(new_fields)
            args = tuple(self.substitute_type(arg, mapping) for arg in info.data.type_args)
            return self._c.type_table.make_struct(info.data.symbol_id, args)

        if info.kind == TypeKind.ENUM and isinstance(info.data, EnumTypeData):
            args = tuple(self.substitute_type(arg, mapping) for arg in info.data.type_args)
            return self._c.type_table.make_enum(info.data.symbol_id, args)

        if info.kind == TypeKind.FUNCTION and isinstance(info.data, FunctionTypeData):
            params = tuple(self.substitute_type(param, mapping) for param in info.data.params)
            ret = self.substitute_type(info.data.return_type, mapping)
            return self._c.type_table.make_function(params, ret, info.data.is_async)

        if info.kind == TypeKind.OPTIONAL and isinstance(info.data, OptionalTypeData):
            return self._c.type_table.make_optional(self.substitute_type(info.data.inner, mapping))

        return type_id

    def get_function_type(self, symbol: Symbol) -> TypeId:
        """Get the function type for a function symbol."""
        if symbol.decl_node is None:
            return self._c.type_table.error_type

        if isinstance(symbol.decl_node, ast.FuncDecl):
            func = symbol.decl_node
            params = tuple(self._c._resolve_type(p.type_annotation) for p in func.params)
            ret = self._c._resolve_type(func.return_type) if func.return_type else self._c.type_table.void_type
            return self._c.type_table.make_function(params, ret, func.is_async)

        if isinstance(symbol.decl_node, ast.ExternFuncDecl):
            func = symbol.decl_node
            params = tuple(self._c._resolve_type(p.type_annotation) for p in func.params)
            ret = self._c._resolve_type(func.return_type) if func.return_type else self._c.type_table.void_type
            return self._c.type_table.make_function(params, ret, func.is_async)

        return self._c.type_table.error_type

    def check_generic_constraints(
        self,
        inferred: Dict[str, TypeId],
        generic_params: List[ast.GenericParam],
    ) -> None:
        """Check that inferred type arguments satisfy generic parameter bounds."""
        from .conformance import ConformanceChecker
        from .checker_core import TypeErrorKind

        conformance = ConformanceChecker(self._c.type_table, self._c.symbol_table)

        for param in generic_params:
            if param.name not in inferred:
                continue
            concrete_type = inferred[param.name]
            for bound in (param.bounds or []):
                bound_type = self._c._resolve_type(bound)
                if self._c.type_table.is_error(bound_type):
                    continue
                if not self._c.type_table.is_protocol(bound_type):
                    continue
                result = conformance.check_conformance(concrete_type, bound_type)
                if not result.conforms:
                    bound_name = getattr(bound, 'name', str(bound))
                    self._c._error(
                        TypeErrorKind.TYPE_MISMATCH,
                        f"Type '{self._c.type_table.format_type(concrete_type)}' "
                        f"does not conform to protocol "
                        f"'{self._c.type_table.format_type(bound_type)}' "
                        f"(required by '{param.name}: {bound_name}')"
                    )
