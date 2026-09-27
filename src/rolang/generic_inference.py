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
    FunctionTypeData,
    OptionalTypeData,
)
from .symbols import Symbol, SymbolId

if TYPE_CHECKING:
    from .checker import TypeChecker


def infer_resolved_type_arguments(table, pattern, concrete, names, inferred):
    """Unify canonical types, so aliases may reorder or wrap parameters."""
    template = table.get_type(pattern)
    actual = table.get_type(concrete)
    if template is None or actual is None or table.is_error(concrete):
        return
    left, right = template.data, actual.data
    if isinstance(left, TypeVariableData):
        if left.name in names:
            inferred.setdefault(left.name, concrete)
        return
    pairs = []
    if isinstance(left, OptionalTypeData):
        pairs = [(left.inner, right.inner if isinstance(right, OptionalTypeData) else concrete)]
    elif isinstance(left, FunctionTypeData) and isinstance(right, FunctionTypeData):
        pairs = list(zip(left.params, right.params)) + [(left.return_type, right.return_type)]
    elif isinstance(left, (StructTypeData, EnumTypeData)) and type(left) is type(right):
        if left.symbol_id != right.symbol_id:
            return
        if isinstance(left, StructTypeData) and left.symbol_id is None:
            pairs = [(a[1], b[1]) for a, b in zip(left.anon_fields or (), right.anon_fields or ())]
        else:
            pairs = list(zip(left.type_args, right.type_args))
    for parameter, argument in pairs:
        infer_resolved_type_arguments(table, parameter, argument, names, inferred)


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
        if not decl.generic_params and not isinstance(call.callee, ast.MemberAccess):
            return {}

        inferred: Dict[str, TypeId] = {}
        generic_names = {param.name for param in decl.generic_params}
        owner = None
        if isinstance(call.callee, ast.MemberAccess):
            receiver_type = self._c.expr_types.get(id(call.callee.object))
            info = self._c.type_table.get_type(receiver_type) if receiver_type is not None else None
            owner = self._c.expr_checker._find_method_owner(decl)
            if owner and info and isinstance(info.data, (StructTypeData, EnumTypeData)):
                inferred.update(zip((p.name for p in owner.generic_params), info.data.type_args))

        # Infer ordinary arguments first, then provide their types to callbacks.
        # This also handles a callback preceding the collection argument.
        if expected_type is not None and decl.return_type is not None:
            self._infer_type_node_generics(decl.return_type, expected_type, generic_names, inferred)
        ordered = sorted(enumerate(call.arguments), key=lambda pair: isinstance(pair[1].value, ast.Lambda))
        for i, arg in ordered:
            if i >= len(decl.params) or arg.value is None:
                continue
            if isinstance(arg.value, ast.Lambda):
                context = self._c.type_resolver.resolve(decl.params[i].type_annotation, inferred)
                arg_type = self._c._infer_with_expected(arg.value, context)
            else:
                arg_type = self._c.expr_types.get(id(arg.value))
                if arg_type is None:
                    arg_type = self._c._infer_with_expected(arg.value, None)
            self._infer_type_node_generics(decl.params[i].type_annotation, arg_type, generic_names, inferred)

        # Nominal method signatures already contain receiver substitutions.
        # Applying them again captures caller variables with the same names.
        if owner is not None and not isinstance(owner, ast.ExtensionDecl):
            return {name: value for name, value in inferred.items() if name in generic_names}
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
        variables = {name: self._c.type_table.make_type_variable(name) for name in generic_names}
        pattern = self._c.type_resolver.resolve(type_node, {**inferred, **variables})
        infer_resolved_type_arguments(self._c.type_table, pattern, concrete_type, generic_names, inferred)

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
