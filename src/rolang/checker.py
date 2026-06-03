"""Type checker for RoLang - assigns types to all expressions."""

from __future__ import annotations
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional, Dict, List, Tuple

from . import ast
from .types import (
    TypeId, TypeKind, TypeTable, TypeInfo,
    StructTypeData, EnumTypeData, FunctionTypeData,
    ClosureTypeData,
    OptionalTypeData, TypeVariableData,
    PrimitiveTypeData, PrimitiveType,
    FuncRequirement, PropRequirement,
    ExistentialTypeData,
)
from .symbols import (
    Symbol, SymbolId, SymbolKind,
    ResolutionResult,
)
from .members import MemberResolver, MethodInfo
from .type_resolver import TypeResolver
from .layout import LayoutService
from .exhaustiveness import ExhaustivenessChecker
from .expr_checker import ExprChecker
from .generic_inference import GenericInference
from .decl_checker import DeclChecker
from .stmt_checker import StmtChecker
from .checker_core import (
    CalleeKind,
    CalleeId,
    TypeErrorKind,
    TypeError,
    TypeCheckResult,
)


# ========================= Type Checker =========================

class TypeChecker:
    """Type checker using bidirectional type checking."""

    def __init__(self, resolution: ResolutionResult) -> None:
        from .conformance import ConformanceChecker
        self.symbol_table = resolution.symbol_table
        self.node_symbols = resolution.node_symbols
        self.imported_symbols = getattr(resolution, 'imported_symbols', {})
        self.imported_extension_methods = getattr(resolution, 'imported_extension_methods', {})
        # id(MemberAccess) -> SymbolId of the resolved method. Populated
        # by ExprChecker._infer_member_access whenever a member resolves
        # to a method (not a field). Used by the HIR builder to attach
        # the resolved method's SymbolId to the resulting HirMethodCall,
        # which lets MIR / codegen disambiguate same-named methods across
        # modules.
        self.member_method_symbols: Dict[int, SymbolId] = {}
        self.self_symbols = resolution.self_symbols  # func_symbol -> self_symbol
        self.type_table = TypeTable()
        # Attach the symbol table so format_type renders user-facing names like
        # `List<i32>` instead of opaque `enum#28<i32>` placeholders.
        self.type_table.attach_symbol_table(self.symbol_table)
        self.type_resolver = TypeResolver(
            self.type_table,
            self.symbol_table,
            self.node_symbols,
            self.imported_symbols,
            self._report_type_resolver_error,
        )
        self.layout = LayoutService(
            self.type_table, self.symbol_table, self.type_resolver
        )
        self.member_resolver = MemberResolver(self.type_table, self.symbol_table)
        self.conformance_checker = ConformanceChecker(
            self.type_table, self.symbol_table
        )

        self.expr_checker = ExprChecker(self)
        self.generic_inference = GenericInference(self)
        self.decl_checker = DeclChecker(self)
        self.stmt_checker = StmtChecker(self)

        # Results
        self.expr_types: Dict[int, TypeId] = {}
        self.call_targets: Dict[int, CalleeId] = {}
        self.operator_targets: Dict[int, CalleeId] = {}
        self.errors: List[TypeError] = []

        # State
        self._current_function_return: Optional[TypeId] = None
        self._type_env: Dict[SymbolId, TypeId] = {}  # Variable types
        self._in_async_function: bool = False  # Track if in async context
        # True when inside a type body (struct/enum/extension) so we know
        # `self` is a meaningful identifier in this scope.
        self._current_self_type: Optional[TypeId] = None  # Type for 'self' in methods
        # Expected type for the expression currently being inferred. Set by
        # contexts that know it (var decls with annotations, returns, ...) and
        # consulted by constructor inference so e.g. a generic enum's unbound
        # type parameters can be filled from the surrounding context.
        self._expected_type: Optional[TypeId] = None
        self._in_unsafe: bool = False

    def check_program(self, program: ast.Program) -> TypeCheckResult:
        """Type check the entire program."""
        self.decl_checker.run(program)

        return TypeCheckResult(
            type_table=self.type_table,
            expr_types=self.expr_types,
            call_targets=self.call_targets,
            operator_targets=self.operator_targets,
            errors=self.errors,
            member_method_symbols=self.member_method_symbols,
        )

    # ========================= Error Handling =========================

    def _error(
        self,
        kind: TypeErrorKind,
        message: str,
        span: Optional[ast.Span] = None,
        node: Optional[ast.Node] = None,
    ) -> None:
        """Record a type error."""
        if span is None and node is not None:
            span = node.span
        self.errors.append(TypeError(kind=kind, message=message, span=span))

    def _report_type_resolver_error(
        self,
        kind: str,
        message: str,
        node: Optional[ast.Node] = None,
    ) -> None:
        error_kind = (
            TypeErrorKind.GENERIC_ARG_COUNT
            if kind == "GENERIC_ARG_COUNT"
            else TypeErrorKind.NOT_A_TYPE
        )
        self._error(error_kind, message, node=node)

    def _is_raw_ptr_type(self, type_id: TypeId) -> bool:
        info = self.type_table.get_type(type_id)
        if info is None or info.kind != TypeKind.PRIMITIVE:
            return False
        data = info.data
        return isinstance(data, PrimitiveTypeData) and data.primitive == PrimitiveType.RAW_PTR

    def _require_unsafe(self, operation: str, node: Optional[ast.Node] = None) -> None:
        if self._in_unsafe:
            return
        self._error(
            TypeErrorKind.INVALID_OPERATION,
            f"{operation} is unsafe and must be used inside an unsafe block",
            node=node,
        )

    # ========================= Type Declaration Collection =========================

    def _collect_type_decl(self, item: ast.TopLevelItem) -> None:
        """Delegate to self.decl_checker."""
        return self.decl_checker._collect_type_decl(item)
    # ========================= Item Checking =========================

    def _register_imported_types(self, program: ast.Program) -> None:
        """Delegate to self.decl_checker."""
        return self.decl_checker._register_imported_types(program)
    def _check_item(self, item: ast.TopLevelItem) -> None:
        """Delegate to self.decl_checker."""
        return self.decl_checker._check_item(item)
    def _check_func_decl(self, func: ast.FuncDecl) -> None:
        """Delegate to self.decl_checker."""
        return self.decl_checker._check_func_decl(func)
    def _check_extern_func(self, func: ast.ExternFuncDecl) -> None:
        """Delegate to self.decl_checker."""
        return self.decl_checker._check_extern_func(func)
    def _check_struct_decl(self, struct: ast.StructDecl) -> None:
        """Delegate to self.decl_checker."""
        return self.decl_checker._check_struct_decl(struct)
    def _check_enum_decl(self, enum: ast.EnumDecl) -> None:
        """Delegate to self.decl_checker."""
        return self.decl_checker._check_enum_decl(enum)
    def _make_generic_param_type_args(
        self, generic_params: List[ast.GenericParam]
    ) -> Tuple[TypeId, ...]:
        """Delegate to self.decl_checker."""
        return self.decl_checker._make_generic_param_type_args(generic_params)
    def _check_extension(self, ext: ast.ExtensionDecl) -> None:
        """Delegate to self.decl_checker."""
        return self.decl_checker._check_extension(ext)
    # ========================= Statement Checking =========================

    def _check_block(self, block: ast.Block) -> None:
        """Delegate to self.stmt_checker."""
        return self.stmt_checker._check_block(block)
    def _check_stmt(self, stmt: ast.Stmt) -> None:
        """Delegate to self.stmt_checker."""
        return self.stmt_checker._check_stmt(stmt)
    def _check_var_decl(self, var_decl: ast.VarDecl) -> None:
        """Delegate to self.stmt_checker."""
        return self.stmt_checker._check_var_decl(var_decl)
    def _check_assignment(self, assign: ast.Assignment) -> None:
        """Delegate to self.stmt_checker."""
        return self.stmt_checker._check_assignment(assign)
    def _check_return(self, ret: ast.ReturnStmt) -> None:
        """Delegate to self.stmt_checker."""
        return self.stmt_checker._check_return(ret)
    def _check_if_stmt(self, if_stmt: ast.IfStmt) -> None:
        """Delegate to self.stmt_checker."""
        return self.stmt_checker._check_if_stmt(if_stmt)
    def _check_while_stmt(self, while_stmt: ast.WhileStmt) -> None:
        """Delegate to self.stmt_checker."""
        return self.stmt_checker._check_while_stmt(while_stmt)
    def _check_for_stmt(self, for_stmt: ast.ForStmt) -> None:
        """Delegate to self.stmt_checker."""
        return self.stmt_checker._check_for_stmt(for_stmt)
    def _check_switch_stmt(self, switch_stmt: ast.SwitchStmt) -> None:
        """Delegate to self.stmt_checker."""
        return self.stmt_checker._check_switch_stmt(switch_stmt)
    def _check_switch_case(self, case: ast.SwitchCase, value_type: TypeId) -> None:
        """Delegate to self.stmt_checker."""
        return self.stmt_checker._check_switch_case(case, value_type)
    def _check_guard_stmt(self, guard: ast.GuardStmt) -> None:
        """Delegate to self.stmt_checker."""
        return self.stmt_checker._check_guard_stmt(guard)
    # ========================= Expression Type Inference =========================

    def _infer_with_expected(self, expr: ast.Expr, expected_type: Optional[TypeId]) -> TypeId:
        return self.expr_checker._infer_with_expected(expr, expected_type)

    def _infer_expr(self, expr: ast.Expr) -> TypeId:
        return self.expr_checker._infer_expr(expr)

    def _check_binary_op_types_raw(self, left_type: TypeId, op: str, right_type: TypeId, emit_error: bool = True) -> TypeId:
        return self.expr_checker._check_binary_op_types_raw(left_type, op, right_type, emit_error)

    # ========================= Type Resolution =========================

    def _resolve_type(self, type_node: Optional[ast.Type]) -> TypeId:
        """Resolve an AST type node to a TypeId."""
        return self.type_resolver.resolve(type_node)

    def _resolve_named_type(self, named: ast.NamedType) -> TypeId:
        """Resolve a named type reference."""
        return self.type_resolver.resolve_named(named)

    # ========================= Helper Methods =========================

    def _get_function_type(self, symbol: Symbol) -> TypeId:
        """Get the function type for a function symbol."""
        return self.generic_inference.get_function_type(symbol)

    def _infer_generic_call_args(
        self,
        callee_symbol: SymbolId,
        call: ast.Call,
        expected_type: Optional[TypeId] = None,
    ) -> Dict[str, TypeId]:
        """Infer generic function type parameters from concrete call arguments."""
        return self.generic_inference.infer_generic_call_args(callee_symbol, call, expected_type)

    def _infer_type_node_generics(
        self,
        type_node: Optional[ast.Type],
        concrete_type: TypeId,
        generic_names: set[str],
        inferred: Dict[str, TypeId],
    ) -> None:
        """Unify an annotation against a concrete type for generic inference."""
        self.generic_inference._infer_type_node_generics(type_node, concrete_type, generic_names, inferred)

    def _substitute_type(self, type_id: TypeId, mapping: Dict[str, TypeId]) -> TypeId:
        """Apply a generic type substitution to a TypeId."""
        return self.generic_inference.substitute_type(type_id, mapping)

    def _get_enum_case_type(self, symbol: Symbol) -> TypeId:
        """Get the type for an enum case symbol."""
        # Find the parent enum
        for sym_id, sym in self.symbol_table.symbols.items():
            if sym.kind == SymbolKind.ENUM and sym.decl_node:
                enum_decl = sym.decl_node
                if isinstance(enum_decl, ast.EnumDecl):
                    for member in enum_decl.members:
                        if isinstance(member, ast.EnumCaseDecl):
                            for case in member.cases:
                                if case.name == symbol.name:
                                    return self.type_table.make_enum(sym_id)
        return self.type_table.error_type

    def _lookup_enum_case(
        self,
        enum_type: TypeId,
        case_name: str,
    ) -> Optional[ast.EnumCaseDef]:
        """Find the EnumCaseDef for a case on the given enum type, if any."""
        info = self.type_table.get_type(enum_type)
        if info is None or info.kind != TypeKind.ENUM:
            return None
        data = info.data
        if not isinstance(data, EnumTypeData):
            return None
        symbol = self.symbol_table.get_symbol(data.symbol_id)
        if symbol is None or not isinstance(symbol.decl_node, ast.EnumDecl):
            return None
        for member in symbol.decl_node.members:
            if isinstance(member, ast.EnumCaseDecl):
                for case in member.cases:
                    if case.name == case_name:
                        return case
        return None

    def _bind_pattern_type(self, pattern: ast.Pattern, type_id: TypeId) -> None:
        """Bind variables in a pattern to their types."""
        if isinstance(pattern, ast.IdentifierPattern):
            symbol_id = self.node_symbols.get(id(pattern))
            if symbol_id:
                self._type_env[symbol_id] = type_id

        elif isinstance(pattern, ast.TuplePattern):
            info = self.type_table.get_type(type_id)
            if info and info.kind == TypeKind.STRUCT and isinstance(info.data, StructTypeData):
                fields = info.data.anon_fields or ()
                for i, (_, elem_pattern) in enumerate(pattern.elements):
                    if i < len(fields):
                        self._bind_pattern_type(elem_pattern, fields[i][1])

        elif isinstance(pattern, ast.EnumCasePattern):
            # Get payload types from enum case, substituting any generic params
            # from the scrutinee's instantiated type args.
            info = self.type_table.get_type(type_id)
            if info and info.kind == TypeKind.ENUM:
                data = info.data
                if isinstance(data, EnumTypeData):
                    symbol = self.symbol_table.get_symbol(data.symbol_id)
                    if symbol and symbol.decl_node and isinstance(symbol.decl_node, ast.EnumDecl):
                        enum_decl = symbol.decl_node
                        subst: Dict[str, TypeId] = {}
                        if len(data.type_args) == len(enum_decl.generic_params):
                            for param, arg in zip(enum_decl.generic_params, data.type_args):
                                subst[param.name] = arg
                        for member in enum_decl.members:
                            if isinstance(member, ast.EnumCaseDecl):
                                for case in member.cases:
                                    if case.name == pattern.case_name:
                                        for i, payload_pattern in enumerate(pattern.payload):
                                            if i < len(case.payload):
                                                _, payload_type = case.payload[i]
                                                resolved = self._substitute_type(
                                                    self._resolve_type(payload_type), subst
                                                )
                                                self._bind_pattern_type(payload_pattern, resolved)
            elif info and info.kind == TypeKind.OPTIONAL:
                # `case .Some(let v):` against an Optional<T> binds v to T.
                if pattern.case_name == "Some" and isinstance(info.data, OptionalTypeData):
                    inner = info.data.inner
                    for payload_pattern in pattern.payload:
                        self._bind_pattern_type(payload_pattern, inner)

        elif isinstance(pattern, ast.TypedPattern):
            if pattern.pattern:
                if pattern.type_annotation:
                    ann_type = self._resolve_type(pattern.type_annotation)
                    self._bind_pattern_type(pattern.pattern, ann_type)
                else:
                    self._bind_pattern_type(pattern.pattern, type_id)

        elif isinstance(pattern, ast.OrPattern):
            # All branches bind the same names with the same types
            for p in pattern.patterns:
                self._bind_pattern_type(p, type_id)

    def _auto_deref(self, type_id: TypeId) -> TypeId:
        """Auto-dereference types (no-op since Ref<T> is removed in v2)."""
        return type_id

    def _common_numeric_type(self, t1: TypeId, t2: TypeId) -> TypeId:
        """Find the common numeric type for two operands of a binop.

        * Float beats integer; f64 beats f32.
        * Both signed and same width => widest wins.
        * Both unsigned and same width => widest wins.
        * Mixed signedness: pick the type whose range covers both. A signed
          type wider than the unsigned operand wins (it can represent every
          unsigned value); otherwise we conservatively widen to ``i64``
          and let the user disambiguate with an explicit cast.

        The previous implementation returned ``t1`` unconditionally for
        integer pairs, which silently leaked the LHS's signedness and
        width (``u8 + i64`` typed as ``u8``).
        """
        tt = self.type_table
        # Floats win over integers
        if tt.is_float(t1) or tt.is_float(t2):
            f64 = tt.get_builtin("f64")
            if f64 and (t1 == f64 or t2 == f64):
                return f64
            return t1 if tt.is_float(t1) else t2

        if not (tt.is_integer(t1) and tt.is_integer(t2)):
            return t1

        info1 = tt.get_type(t1)
        info2 = tt.get_type(t2)
        if info1 is None or info2 is None:
            return t1
        sizes = {
            PrimitiveType.I8: 8, PrimitiveType.U8: 8,
            PrimitiveType.I16: 16, PrimitiveType.U16: 16,
            PrimitiveType.I32: 32, PrimitiveType.U32: 32,
            PrimitiveType.I64: 64, PrimitiveType.U64: 64,
        }
        if not (isinstance(info1.data, PrimitiveTypeData)
                and isinstance(info2.data, PrimitiveTypeData)):
            return t1
        prim1 = info1.data.primitive
        prim2 = info2.data.primitive
        bits1 = sizes.get(prim1)
        bits2 = sizes.get(prim2)
        if bits1 is None or bits2 is None:
            return t1
        signed1 = tt.is_signed_integer(t1)
        signed2 = tt.is_signed_integer(t2)

        if signed1 == signed2:
            return t1 if bits1 >= bits2 else t2

        # Mixed signedness — prefer a signed type that strictly covers the
        # unsigned operand's range. Otherwise widen to i64.
        if signed1 and bits1 > bits2:
            return t1
        if signed2 and bits2 > bits1:
            return t2
        return tt.get_builtin("i64") or t1

    def _check_boolean(self, type_id: TypeId, context: str) -> None:
        """Check that a type is boolean."""
        if not self.type_table.is_bool(type_id) and not self.type_table.is_error(type_id):
            self._error(
                TypeErrorKind.TYPE_MISMATCH,
                f"Expected Bool for {context}, got {self.type_table.format_type(type_id)}"
            )

    def _types_equal(self, t1: TypeId, t2: TypeId) -> bool:
        """Structural equality for types, treating same-named type variables as equal."""
        if t1 == t2:
            return True
        info1 = self.type_table.get_type(t1)
        info2 = self.type_table.get_type(t2)
        if info1 is None or info2 is None:
            return False
        if info1.kind != info2.kind:
            return False
        if info1.kind == TypeKind.TYPE_VARIABLE:
            if isinstance(info1.data, TypeVariableData) and isinstance(info2.data, TypeVariableData):
                return info1.data.name == info2.data.name
            return False
        if info1.kind == TypeKind.STRUCT:
            if isinstance(info1.data, StructTypeData) and isinstance(info2.data, StructTypeData):
                if info1.data.symbol_id is None and info2.data.symbol_id is None:
                    f1 = info1.data.anon_fields or ()
                    f2 = info2.data.anon_fields or ()
                    if len(f1) != len(f2):
                        return False
                    return all(self._types_equal(a, b) for (_, a), (_, b) in zip(f1, f2))
                if info1.data.symbol_id is None or info2.data.symbol_id is None:
                    return False
                if info1.data.symbol_id != info2.data.symbol_id:
                    return False
                if len(info1.data.type_args) != len(info2.data.type_args):
                    return False
                return all(self._types_equal(a, b) for a, b in zip(info1.data.type_args, info2.data.type_args))
            return False
        if info1.kind == TypeKind.ENUM:
            if isinstance(info1.data, EnumTypeData) and isinstance(info2.data, EnumTypeData):
                if info1.data.symbol_id != info2.data.symbol_id:
                    return False
                if len(info1.data.type_args) != len(info2.data.type_args):
                    return False
                return all(self._types_equal(a, b) for a, b in zip(info1.data.type_args, info2.data.type_args))
            return False
        if info1.kind == TypeKind.OPTIONAL:
            if isinstance(info1.data, OptionalTypeData) and isinstance(info2.data, OptionalTypeData):
                return self._types_equal(info1.data.inner, info2.data.inner)
            return False
        if info1.kind == TypeKind.FUNCTION:
            if isinstance(info1.data, FunctionTypeData) and isinstance(info2.data, FunctionTypeData):
                if len(info1.data.params) != len(info2.data.params):
                    return False
                return (all(self._types_equal(a, b) for a, b in zip(info1.data.params, info2.data.params)) and
                        self._types_equal(info1.data.return_type, info2.data.return_type) and
                        info1.data.is_async == info2.data.is_async)
            return False
        if info1.kind == TypeKind.CLOSURE:
            if isinstance(info1.data, ClosureTypeData) and isinstance(info2.data, ClosureTypeData):
                if len(info1.data.params) != len(info2.data.params):
                    return False
                if len(info1.data.captures) != len(info2.data.captures):
                    return False
                return (all(self._types_equal(a, b) for a, b in zip(info1.data.params, info2.data.params)) and
                        self._types_equal(info1.data.return_type, info2.data.return_type) and
                        all(self._types_equal(a, b) for a, b in zip(info1.data.captures, info2.data.captures)) and
                        info1.data.is_async == info2.data.is_async)
            return False
        return False

    def _check_assignable(self, source: TypeId, target: TypeId, context: str, node: Optional[ast.Node] = None) -> None:
        """Check if source type can be assigned to target type, emitting error if not."""
        if source == self.type_table.error_type or target == self.type_table.error_type:
            return  # Don't report cascading errors

        if source == target:
            return

        if self._types_equal(source, target):
            return

        # Generic type parameters are checked after monomorphization.
        target_info = self.type_table.get_type(target)
        if target_info and target_info.kind == TypeKind.TYPE_VARIABLE:
            return

        # Handle optional assignment (T assignable to T?, including int widening).
        inner = self.type_table.get_optional_inner(target)
        if inner is not None:
            if source == inner or self._types_equal(source, inner):
                return
            if self.type_table.can_widen_int(source, inner):
                return

        # Assigning a concrete value to any P requires structural conformance.
        if (
            target_info
            and target_info.kind == TypeKind.EXISTENTIAL
            and isinstance(target_info.data, ExistentialTypeData)
        ):
            conformance = self.conformance_checker.check_conformance(
                source,
                target_info.data.protocol_id,
            )
            if conformance.conforms:
                return

            protocol_name = self.type_table.format_type(target_info.data.protocol_id)
            details = ""
            if conformance.missing_requirements:
                missing = ", ".join(conformance.missing_requirements)
                details = f"; missing requirements: {missing}"
            elif conformance.errors:
                details = f"; {conformance.errors[0]}"

            self._error(
                TypeErrorKind.TYPE_MISMATCH,
                f"Type {self.type_table.format_type(source)} does not conform to {protocol_name}{details} in {context}",
                node=node,
            )
            return

        # Handle nil assignment to optional or RawPtr
        if source == self.type_table.nil_type:
            if self.type_table.get_optional_inner(target) is not None:
                return
            raw_ptr = self.type_table.get_builtin("RawPtr")
            if raw_ptr is not None and target == raw_ptr:
                return
            self._error(
                TypeErrorKind.TYPE_MISMATCH,
                f"Cannot assign nil to non-optional type {self.type_table.format_type(target)} in {context}",
                node=node,
            )
            return

        source_info = self.type_table.get_type(source)
        if source_info and source_info.kind == TypeKind.TYPE_VARIABLE:
            return

        # Handle never type (assignable to anything)
        if self.type_table.is_never(source):
            return

        # Handle integer widening (e.g., i32 to i64)
        if self.type_table.can_widen_int(source, target):
            return

        self._error(
            TypeErrorKind.TYPE_MISMATCH,
            f"Cannot assign {self.type_table.format_type(source)} to {self.type_table.format_type(target)} in {context}",
            node=node,
        )

    def _get_iterable_element(self, type_id: TypeId) -> TypeId:
        """Get the element type bound by a `for` loop pattern.

        After the legacy `[T]` / `[K: V]` removal, iterables are always
        STRUCT types — either `Vec<T>` / `Dict<K, V>` (recognized by
        their generic args) or any user struct that provides
        `len() -> i32` plus `get(i32) -> T`.
        """
        info = self.type_table.get_type(type_id)
        if info is None:
            return self.type_table.error_type

        # Handle indexable types: structs with len() -> i32 and get(index: i32) -> T
        if info.kind == TypeKind.STRUCT:
            len_method = self.member_resolver.get_method(type_id, "len")
            get_method = self.member_resolver.get_method(type_id, "get")
            if len_method is not None and get_method is not None:
                len_data = self.type_table.get_function_data(len_method.signature)
                if len_data and not len_data.params and len_data.return_type:
                    from .types import PrimitiveTypeData, PrimitiveType
                    len_ret = self.type_table.get_type(len_data.return_type)
                    if len_ret and len_ret.kind == TypeKind.PRIMITIVE:
                        if isinstance(len_ret.data, PrimitiveTypeData) and len_ret.data.primitive == PrimitiveType.I32:
                            get_data = self.type_table.get_function_data(get_method.signature)
                            if get_data and len(get_data.params) >= 1 and get_data.return_type:
                                return get_data.return_type

        # Check for protocol-based iteration
        iterable_symbol = self.symbol_table.get_builtin("Iterable")
        if iterable_symbol:
            iterable_type = self.type_table.get_protocol_type(iterable_symbol)
            if iterable_type:
                result = self.conformance_checker.check_conformance(type_id, iterable_type)
                if result.conforms:
                    return self._get_iterator_element_type(type_id)

        return self.type_table.error_type

    def _get_iterator_element_type(self, iterable_type: TypeId) -> TypeId:
        """Get the Element type from an Iterable's Iterator."""
        # For now, look for a makeIterator method and get its return type's Element
        # Full implementation would resolve associated types properly
        method = self.member_resolver.get_method(iterable_type, "__iter__")
        if method:
            # Get the return type (IteratorType)
            func_data = self.type_table.get_function_data(method.signature)
            if func_data:
                iterator_type = func_data.return_type
                # Get __next__() method from iterator
                next_method = self.member_resolver.get_method(iterator_type, "__next__")
                if next_method:
                    next_data = self.type_table.get_function_data(next_method.signature)
                    if next_data:
                        # Return type is Element? - unwrap optional
                        from .types import OptionalTypeData
                        ret_info = self.type_table.get_type(next_data.return_type)
                        if ret_info and ret_info.kind == TypeKind.OPTIONAL:
                            data = ret_info.data
                            if isinstance(data, OptionalTypeData):
                                return data.inner
        return self.type_table.error_type

    def _check_generic_constraints(
        self,
        inferred: Dict[str, TypeId],
        generic_params: List[ast.GenericParam],
    ) -> None:
        """Check that inferred type arguments satisfy generic parameter bounds."""
        self.generic_inference.check_generic_constraints(inferred, generic_params)

    def _check_struct_literal_fields(
        self,
        type_info: TypeInfo,
        args: List[ast.Argument],
        expected_type: Optional[TypeId] = None,
    ) -> TypeId:
        """Check struct literal fields against struct declarations.

        Returns the struct type, which may be a fresh instantiation if the
        struct is generic and type params can be inferred from the fields
        or from an expected-type hint flowing in from context.
        """
        if type_info.kind != TypeKind.STRUCT:
            for arg in args:
                if arg.value:
                    self._infer_expr(arg.value)
            return type_info.id

        data = type_info.data
        if not isinstance(data, StructTypeData):
            return type_info.id

        struct_symbol_id = data.symbol_id
        struct_decl: Optional[ast.StructDecl] = None
        symbol = self.symbol_table.get_symbol(struct_symbol_id)
        if symbol and isinstance(symbol.decl_node, ast.StructDecl):
            struct_decl = symbol.decl_node

        generic_names: set[str] = set()
        inferred: Dict[str, TypeId] = {}
        if struct_decl and struct_decl.generic_params:
            generic_names = {p.name for p in struct_decl.generic_params}
            # Seed from any explicit type args (e.g. `Box<i32>(v: ...)`).
            if len(data.type_args) == len(struct_decl.generic_params):
                for param, arg in zip(struct_decl.generic_params, data.type_args):
                    if not self.type_table.is_error(arg):
                        info = self.type_table.get_type(arg)
                        # Skip placeholder type variables (un-instantiated).
                        if not (info and info.kind == TypeKind.TYPE_VARIABLE):
                            inferred[param.name] = arg
            # Seed from the expected type if it's the same struct with concrete args.
            if expected_type is not None:
                exp_info = self.type_table.get_type(expected_type)
                if (exp_info and exp_info.kind == TypeKind.STRUCT
                        and isinstance(exp_info.data, StructTypeData)
                        and exp_info.data.symbol_id == struct_symbol_id
                        and len(exp_info.data.type_args) == len(struct_decl.generic_params)):
                    for param, arg in zip(struct_decl.generic_params, exp_info.data.type_args):
                        inferred.setdefault(param.name, arg)

        # Build label -> field-annotation map by walking the struct AST.
        field_annotations: Dict[str, ast.Type] = {}
        positional_fields: List[Tuple[str, ast.Type]] = []
        if struct_decl:
            for member in struct_decl.members:
                if isinstance(member, ast.PropertyDecl) and member.type_annotation:
                    field_annotations[member.name] = member.type_annotation
                    positional_fields.append((member.name, member.type_annotation))

        seen_labels: set[str] = set()
        if struct_decl:
            for arg in args:
                label = arg.label
                if label is None:
                    continue
                if label in seen_labels:
                    self._error(
                        TypeErrorKind.DUPLICATE_MEMBER,
                        f"duplicate field '{label}' in struct literal",
                        node=arg,
                    )
                    continue
                seen_labels.add(label)
                if label not in field_annotations:
                    self._error(
                        TypeErrorKind.UNDEFINED_MEMBER,
                        f"struct '{struct_decl.name}' has no field '{label}'",
                        node=arg,
                    )

            missing = [name for name, _ in positional_fields if name not in seen_labels]
            for name in missing:
                self._error(
                    TypeErrorKind.TYPE_MISMATCH,
                    f"missing field '{name}' in struct literal for '{struct_decl.name}'",
                )

        # First pass: infer generics from arg types against field annotations.
        for i, arg in enumerate(args):
            if arg.value is None:
                continue
            if arg.label is None:
                self._error(
                    TypeErrorKind.TYPE_MISMATCH,
                    "Struct literal fields must be labeled",
                    node=arg,
                )
                continue
            arg_type = self._infer_expr(arg.value)
            ann: Optional[ast.Type] = None
            if arg.label and arg.label in field_annotations:
                ann = field_annotations[arg.label]
            if ann is not None and generic_names:
                self._infer_type_node_generics(ann, arg_type, generic_names, inferred)

        # Build instantiated type if generic.
        result_type = type_info.id
        if struct_decl and struct_decl.generic_params:
            unbound = [p.name for p in struct_decl.generic_params if p.name not in inferred]
            if unbound:
                self._error(
                    TypeErrorKind.CANNOT_INFER,
                    f"Cannot infer type parameter(s) {', '.join(unbound)} of "
                    f"struct '{struct_decl.name}' from arguments; "
                    f"add explicit type arguments or a type annotation"
                )
            else:
                self._check_generic_constraints(inferred, struct_decl.generic_params)
                type_args = tuple(
                    inferred[p.name] for p in struct_decl.generic_params
                )
                result_type = self.type_table.make_struct(struct_symbol_id, type_args)

        # Second pass: type-check each arg against the (substituted) field type.
        for i, arg in enumerate(args):
            if arg.value is None:
                continue
            arg_type = self.expr_types.get(id(arg.value))
            if arg_type is None:
                arg_type = self._infer_expr(arg.value)
            ann: Optional[ast.Type] = None
            label = arg.label
            if label and label in field_annotations:
                ann = field_annotations[label]
            if ann is None:
                continue
            expected = self._substitute_type(self._resolve_type(ann), inferred)
            self._check_assignable(
                arg_type, expected,
                f"field '{label}'" if label else f"field {i + 1}"
            )

        return result_type

    def _record_call_target(
        self,
        call: ast.Call,
        kind: CalleeKind,
        symbol_id: Optional[SymbolId] = None,
    ) -> None:
        """Record a resolved call target."""
        self.call_targets[id(call)] = CalleeId(kind=kind, symbol_id=symbol_id)


# ========================= Public API =========================

def typecheck(program: ast.Program, resolution: ResolutionResult) -> TypeCheckResult:
    """Type check a RoLang program.

    Args:
        program: The parsed AST.
        resolution: Result from name resolution.

    Returns:
        TypeCheckResult containing type table, expression types, and errors.
    """
    checker = TypeChecker(resolution)
    return checker.check_program(program)
