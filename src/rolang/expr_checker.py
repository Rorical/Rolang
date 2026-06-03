"""Expression type inference extracted from TypeChecker.

This module contains all expression-level type inference logic, keeping
TypeChecker focused on declarations and statements.
"""

from __future__ import annotations

from typing import Optional, Dict, List, Tuple

from . import ast
from .types import (
    TypeId, TypeKind, TypeTable, TypeInfo,
    StructTypeData, EnumTypeData, FunctionTypeData,
    OptionalTypeData, TypeVariableData,
    PrimitiveTypeData, PrimitiveType,
    FuncRequirement, PropRequirement,
    ExistentialTypeData,
)
from .symbols import (
    Symbol, SymbolId, SymbolKind,
)
from .checker_core import TypeErrorKind, CalleeKind, CalleeId
from .members import MemberResolver, MethodInfo
from .operators import (
    is_arithmetic_op,
    is_bitwise_op,
    is_equality_op,
    is_logical_op,
    is_nil_coalescing_op,
    is_order_comparison_op,
    to_method_name,
)


class ExprChecker:
    """Infer types for expressions."""

    def __init__(self, checker: "TypeChecker") -> None:
        self._c = checker

    def _infer_with_expected(
        self,
        expr: ast.Expr,
        expected_type: Optional[TypeId],
    ) -> TypeId:
        """Infer an expression with an explicit expected type in scope."""
        old = self._c._expected_type
        self._c._expected_type = expected_type
        try:
            return self._infer_expr(expr)
        finally:
            self._c._expected_type = old

    def _infer_expr(self, expr: ast.Expr) -> TypeId:
        """Infer the type of an expression (bottom-up)."""
        result = self._do_infer_expr(expr)
        self._c.expr_types[id(expr)] = result
        return result

    def _do_infer_expr(self, expr: ast.Expr) -> TypeId:
        """Implementation of expression type inference."""
        if isinstance(expr, ast.Literal):
            return self._infer_literal(expr)
        elif isinstance(expr, ast.Identifier):
            return self._infer_identifier(expr)
        elif isinstance(expr, ast.TypeReference):
            return self._infer_type_reference(expr)
        elif isinstance(expr, ast.BinaryOp):
            return self._infer_binary_op(expr)
        elif isinstance(expr, ast.UnaryOp):
            return self._infer_unary_op(expr)
        elif isinstance(expr, ast.TernaryOp):
            return self._infer_ternary_op(expr)
        elif isinstance(expr, ast.Call):
            return self._infer_call(expr)
        elif isinstance(expr, ast.MemberAccess):
            return self._infer_member_access(expr)
        elif isinstance(expr, ast.Subscript):
            return self._infer_subscript(expr)
        elif isinstance(expr, ast.TupleExpr):
            return self._infer_tuple(expr)
        elif isinstance(expr, ast.ArrayLiteral):
            return self._infer_array_literal(expr)
        elif isinstance(expr, ast.DictLiteral):
            return self._infer_dict_literal(expr)
        elif isinstance(expr, ast.Lambda):
            return self._infer_lambda(expr)
        elif isinstance(expr, ast.StructLiteral):
            return self._infer_struct_literal(expr)
        elif isinstance(expr, ast.Cast):
            return self._infer_cast(expr)
        elif isinstance(expr, ast.TypeCheck):
            return self._infer_type_check(expr)
        elif isinstance(expr, ast.OptionalChain):
            return self._infer_optional_chain(expr)
        elif isinstance(expr, ast.TryExpr):
            return self._infer_try_expr(expr)
        elif isinstance(expr, ast.SizeOfExpr):
            return self._infer_size_of_expr(expr)
        elif isinstance(expr, ast.TypeIdExpr):
            return self._infer_type_id_expr(expr)
        elif isinstance(expr, ast.AlignOfExpr):
            return self._infer_align_of_expr(expr)
        elif isinstance(expr, ast.DropOfExpr):
            return self._infer_drop_of_expr(expr)
        elif isinstance(expr, ast.CloneOfExpr):
            return self._infer_clone_of_expr(expr)
        else:
            return self._c.type_table.error_type

    def _infer_literal(self, lit: ast.Literal) -> TypeId:
        """Infer type of a literal.

        Integer and float literals consult ``_expected_type`` so that
        ``let x: i64 = 0x123456789ABCDEF0;`` keeps the literal at i64 rather
        than silently truncating to the historical i32 default. Range checks
        emit a diagnostic when the literal does not fit.
        """
        if lit.kind == "int":
            return self._infer_int_literal(lit)
        elif lit.kind == "float":
            return self._infer_float_literal(lit)
        elif lit.kind == "bool":
            return self._c.type_table.get_builtin("Bool") or self._c.type_table.error_type
        elif lit.kind == "string":
            string_symbol = self._c.symbol_table.get_type_symbol("String")
            if string_symbol is not None:
                return self._c.type_table.make_struct(string_symbol)
            return self._c.type_table.error_type
        elif lit.kind == "char":
            return self._c.type_table.get_builtin("i32") or self._c.type_table.error_type
        elif lit.kind == "nil":
            return self._c.type_table.nil_type
        else:
            return self._c.type_table.error_type

    # ----- Integer literal helpers -----

    _INT_BIT_WIDTHS = {
        PrimitiveType.I8: 8, PrimitiveType.I16: 16,
        PrimitiveType.I32: 32, PrimitiveType.I64: 64,
        PrimitiveType.U8: 8, PrimitiveType.U16: 16,
        PrimitiveType.U32: 32, PrimitiveType.U64: 64,
    }
    _SIGNED_INTS = {
        PrimitiveType.I8, PrimitiveType.I16,
        PrimitiveType.I32, PrimitiveType.I64,
    }

    def _int_literal_fits(self, value: int, type_id: TypeId) -> bool:
        """Return True if `value` is representable in the given integer type.

        Treats integer literals as unsigned magnitudes (the parser never
        produces a negative literal; unary minus is a separate UnaryOp).
        """
        info = self._c.type_table.get_type(type_id)
        if info is None or not isinstance(info.data, PrimitiveTypeData):
            return False
        prim = info.data.primitive
        bits = self._INT_BIT_WIDTHS.get(prim)
        if bits is None:
            return False
        if prim in self._SIGNED_INTS:
            return 0 <= value < (1 << (bits - 1))
        return 0 <= value < (1 << bits)

    def _infer_int_literal(self, lit: ast.Literal) -> TypeId:
        value = lit.value if isinstance(lit.value, int) else 0
        tt = self._c.type_table
        expected = self._c._expected_type

        # Unwrap Optional<T> if the immediate context expects an optional with
        # a numeric inner type — keeps `var x: i64? = 100;` honest.
        if expected is not None and tt.is_optional(expected):
            inner = tt.get_optional_inner(expected)
            if inner is not None and tt.is_numeric(inner):
                expected = inner

        if expected is not None:
            if tt.is_integer(expected):
                if self._int_literal_fits(value, expected):
                    return expected
                self._c._error(
                    TypeErrorKind.TYPE_MISMATCH,
                    f"Integer literal {value} does not fit in "
                    f"{tt.format_type(expected)}",
                    node=lit,
                )
                return expected  # avoid cascading diagnostics
            if tt.is_float(expected):
                # Integer literal used in float context — accept silently.
                return expected

        # No usable context — pick the narrowest signed default that holds the
        # value, widening through i32 -> i64 -> u64 in order.
        for name in ("i32", "i64", "u64"):
            candidate = tt.get_builtin(name)
            if candidate is not None and self._int_literal_fits(value, candidate):
                return candidate

        self._c._error(
            TypeErrorKind.TYPE_MISMATCH,
            f"Integer literal {value} exceeds the range of every built-in "
            f"integer type",
            node=lit,
        )
        return tt.get_builtin("i64") or tt.error_type

    def _infer_float_literal(self, lit: ast.Literal) -> TypeId:
        tt = self._c.type_table
        expected = self._c._expected_type
        if expected is not None and tt.is_optional(expected):
            inner = tt.get_optional_inner(expected)
            if inner is not None and tt.is_float(inner):
                expected = inner
        if expected is not None and tt.is_float(expected):
            return expected
        return tt.get_builtin("f64") or tt.error_type

    def _infer_identifier(self, ident: ast.Identifier) -> TypeId:
        """Infer type of an identifier reference."""
        symbol_id = self._c.node_symbols.get(id(ident))
        if symbol_id is None:
            return self._c.type_table.error_type

        # Check type environment first
        if symbol_id in self._c._type_env:
            return self._c._type_env[symbol_id]

        # Look up the symbol
        symbol = self._c.symbol_table.get_symbol(symbol_id)
        if symbol is None:
            return self._c.type_table.error_type

        # Get type based on symbol kind
        if symbol.kind == SymbolKind.FUNCTION or symbol.kind == SymbolKind.EXTERN_FUNC:
            return self._c._get_function_type(symbol)
        elif symbol.kind == SymbolKind.STRUCT:
            # Type as value - return the struct type
            return self._c.type_table.make_struct(symbol_id)
        elif symbol.kind == SymbolKind.ENUM:
            return self._c.type_table.make_enum(symbol_id)
        elif symbol.kind == SymbolKind.ENUM_CASE:
            # Get the parent enum type
            return self._c._get_enum_case_type(symbol)

        return self._c.type_table.error_type

    def _infer_type_reference(self, type_ref: ast.TypeReference) -> TypeId:
        if type_ref.type_name is None:
            return self._c.type_table.error_type
        return self._c._resolve_named_type(type_ref.type_name)

    def _infer_binary_op(self, binop: ast.BinaryOp) -> TypeId:
        """Infer type of a binary operation."""
        if binop.left is None or binop.right is None:
            return self._c.type_table.error_type

        left_type = self._infer_expr(binop.left)
        right_type = self._infer_expr(binop.right)

        return self._check_binary_op_types(binop, left_type, binop.op, right_type)

    def _try_operator_overload(
        self,
        binop: ast.BinaryOp,
        left_type: TypeId,
        op: str,
        right_type: TypeId
    ) -> Optional[TypeId]:
        """Try to resolve a binary operator via method overloading.

        Returns the return type if a matching operator method is found,
        or None if no overload exists.
        """
        method_name = to_method_name(op)
        if not method_name:
            return None

        method = self._c.member_resolver.get_method(left_type, method_name)
        if method is None:
            return None

        # Get function signature
        func_data = self._c.type_table.get_function_data(method.signature)
        if func_data is None or len(func_data.params) < 1:
            return None

        # Check that right operand matches the first parameter
        # (methods take self as first param implicitly, so first explicit
        # param is at index 0 in the function signature)
        if not self._c._types_equal(right_type, func_data.params[0]):
            return None

        # Record the operator resolution
        if binop is not None:
            self._c.operator_targets[id(binop)] = CalleeId(
                kind=CalleeKind.METHOD,
                symbol_id=method.symbol_id,
            )

        return func_data.return_type

    def _check_binary_op_types_raw(
        self,
        left_type: TypeId,
        op: str,
        right_type: TypeId,
        emit_error: bool = True
    ) -> TypeId:
        """Check binary operation types without operator overloading fallback.
        Used by compound assignment checker."""
        if is_arithmetic_op(op):
            if self._c.type_table.is_numeric(left_type) and self._c.type_table.is_numeric(right_type):
                return self._c._common_numeric_type(left_type, right_type)
            if emit_error:
                self._c._error(
                    TypeErrorKind.INVALID_OPERATION,
                    f"Cannot apply '{op}' to {self._c.type_table.format_type(left_type)} and {self._c.type_table.format_type(right_type)}"
                )
            return self._c.type_table.error_type

        elif is_order_comparison_op(op):
            if self._c.type_table.is_numeric(left_type) and self._c.type_table.is_numeric(right_type):
                return self._c.type_table.get_builtin("Bool") or self._c.type_table.error_type
            if emit_error:
                self._c._error(
                    TypeErrorKind.INVALID_OPERATION,
                    f"Cannot compare {self._c.type_table.format_type(left_type)} and {self._c.type_table.format_type(right_type)}"
                )
            return self._c.type_table.error_type

        elif is_equality_op(op):
            # Suppress cascading diagnostics after an earlier error.
            if self._c.type_table.is_error(left_type) or self._c.type_table.is_error(right_type):
                return self._c.type_table.get_builtin("Bool") or self._c.type_table.error_type
            left_info = self._c.type_table.get_type(left_type)
            right_info = self._c.type_table.get_type(right_type)
            both_primitive = (
                left_info and left_info.kind == TypeKind.PRIMITIVE and
                right_info and right_info.kind == TypeKind.PRIMITIVE
            )
            if both_primitive:
                both_numeric = (
                    self._c.type_table.is_numeric(left_type) and
                    self._c.type_table.is_numeric(right_type)
                )
                # Numeric operands may differ in width (implicit widening
                # applies); otherwise the two primitives must be the same type.
                # This rejects nonsensical mixes like `Bool == i32` or
                # `RawPtr == i32` that the old "both primitive" check allowed.
                if both_numeric or left_type == right_type:
                    return self._c.type_table.get_builtin("Bool") or self._c.type_table.error_type
            if emit_error:
                self._c._error(
                    TypeErrorKind.INVALID_OPERATION,
                    f"Cannot compare {self._c.type_table.format_type(left_type)} and {self._c.type_table.format_type(right_type)}"
                )
            return self._c.type_table.error_type

        elif is_logical_op(op):
            bool_type = self._c.type_table.get_builtin("Bool")
            if bool_type:
                self._c._check_boolean(left_type, "left operand")
                self._c._check_boolean(right_type, "right operand")
                return bool_type
            return self._c.type_table.error_type

        elif is_nil_coalescing_op(op):
            inner = self._c.type_table.get_optional_inner(left_type)
            if inner:
                return inner
            return left_type

        elif is_bitwise_op(op):
            if self._c.type_table.is_integer(left_type) and self._c.type_table.is_integer(right_type):
                return left_type
            if emit_error:
                self._c._error(
                    TypeErrorKind.INVALID_OPERATION,
                    f"Bitwise operation requires integer operands"
                )
            return self._c.type_table.error_type

        return self._c.type_table.error_type

    def _check_binary_op_types(
        self,
        binop: ast.BinaryOp,
        left_type: TypeId,
        op: str,
        right_type: TypeId
    ) -> TypeId:
        """Check binary operation types and return result type.

        Operator overloading takes priority: if the left-hand type defines a
        method matching the operator (e.g. ``add`` for ``+``, ``eq`` for
        ``==``), that method is used regardless of built-in rules.
        Built-in rules (arithmetic widening, logical ops, nil-coalescing,
        etc.) act as the fallback for primitives that have no methods.
        """
        # Try operator overloading first
        overload_type = self._try_operator_overload(binop, left_type, op, right_type)
        if overload_type is not None:
            return overload_type

        # Fall back to built-in rules for primitives
        return self._check_binary_op_types_raw(left_type, op, right_type, emit_error=True)

    def _collect_member_parts(self, expr: ast.MemberAccess) -> Optional[List[str]]:
        """Recursively collect parts of a dotted member access for namespace resolution."""
        if isinstance(expr.object, ast.Identifier):
            return [expr.object.name, expr.member]
        if isinstance(expr.object, ast.MemberAccess):
            inner = self._collect_member_parts(expr.object)
            if inner is not None:
                return inner + [expr.member]
        return None

    def _infer_block_return_type(self, stmts) -> TypeId:
        if isinstance(stmts, ast.Block):
            stmts = stmts.statements
        if not isinstance(stmts, list):
            return self._c.type_table.void_type
        for stmt in stmts:
            if isinstance(stmt, ast.ReturnStmt) and stmt.value:
                return self._c.expr_types.get(id(stmt.value), self._c.type_table.error_type)
            if isinstance(stmt, ast.IfStmt):
                if stmt.then_block:
                    t = self._infer_block_return_type(stmt.then_block)
                    if not self._c.type_table.is_error(t):
                        return t
                if stmt.else_block:
                    t = self._infer_block_return_type(stmt.else_block)
                    if not self._c.type_table.is_error(t):
                        return t
            if isinstance(stmt, ast.SwitchStmt):
                for case in stmt.cases:
                    t = self._infer_block_return_type(case.body)
                    if not self._c.type_table.is_error(t):
                        return t
        return self._c.type_table.void_type

    def _enum_has_case(self, data: EnumTypeData, case_name: str) -> bool:
        """True if the enum behind `data` declares a case named `case_name`."""
        symbol = self._c.symbol_table.get_symbol(data.symbol_id)
        if symbol is None or symbol.decl_node is None:
            return False
        from . import ast as ast_module
        if not isinstance(symbol.decl_node, ast_module.EnumDecl):
            return False
        for member in symbol.decl_node.members:
            if hasattr(member, "cases"):
                for case in member.cases:
                    if case.name == case_name:
                        return True
        return False

    def _is_result_shaped(self, data: EnumTypeData) -> bool:
        """A Result-shaped enum has both an `ok` and an `err` case. `try`/`?`
        rely on this layout (ok=tag 0, err=tag 1) when desugaring to an
        unwrap-or-return, so treating an arbitrary generic enum as Result
        would mis-lower the case indices."""
        return self._enum_has_case(data, "ok") and self._enum_has_case(data, "err")

    def _check_try_error_type(
        self,
        operand_enum_data,
        func_return: TypeId,
        func_return_info,
        node,
        operand_type: TypeId,
    ) -> None:
        if not operand_enum_data.type_args or len(operand_enum_data.type_args) < 2:
            return
        operand_err = operand_enum_data.type_args[1]
        func_ret_data = func_return_info.data
        if isinstance(func_ret_data, EnumTypeData):
            func_err = (func_ret_data.type_args[1]
                        if func_ret_data.type_args and len(func_ret_data.type_args) >= 2
                        else None)
            if func_err is not None:
                if not self._c._types_equal(operand_err, func_err) and not self._c.type_table.can_widen_int(operand_err, func_err) and not self._c.type_table.is_error(operand_err):
                    self._c._error(
                        TypeErrorKind.TYPE_MISMATCH,
                        f"Cannot propagate error type "
                        f"'{self._c.type_table.format_type(operand_err)}' "
                        f"to '{self._c.type_table.format_type(func_err)}' via 'try'"
                    )

    def _infer_unary_op(self, unop: ast.UnaryOp) -> TypeId:
        """Infer type of a unary operation."""
        if unop.operand is None:
            return self._c.type_table.error_type

        operand_type = self._infer_expr(unop.operand)

        if unop.op == "-":
            if self._c.type_table.is_numeric(operand_type):
                return operand_type
            self._c._error(
                TypeErrorKind.INVALID_OPERATION,
                f"Cannot negate {self._c.type_table.format_type(operand_type)}"
            )
            return self._c.type_table.error_type

        elif unop.op == "!":
            self._c._check_boolean(operand_type, "operand of !")
            return self._c.type_table.get_builtin("Bool") or self._c.type_table.error_type

        elif unop.op == "~":
            if self._c.type_table.is_integer(operand_type):
                return operand_type
            self._c._error(
                TypeErrorKind.INVALID_OPERATION,
                f"Bitwise not requires integer operand"
            )
            return self._c.type_table.error_type

        elif unop.op == "await":
            # Validate await is in async context
            if not self._c._in_async_function:
                self._c._error(
                    TypeErrorKind.INVALID_OPERATION,
                    "'await' can only be used inside an async function"
                )
                return self._c.type_table.error_type

            # `await` on a function reference extracts the return type; on
            # anything else it is the identity (the call has already been
            # resolved to the result type).
            info = self._c.type_table.get_type(operand_type)
            if info and info.kind == TypeKind.FUNCTION:
                data = info.data
                if isinstance(data, FunctionTypeData) and data.is_async:
                    return data.return_type
                self._c._error(
                    TypeErrorKind.INVALID_OPERATION,
                    "'await' can only be used on async functions"
                )
                return self._c.type_table.error_type
            return operand_type

        elif unop.op == "try":
            # Prefix ``try expr`` unwraps a ``Result<T, E>`` into ``T``
            # (or propagates the ``err`` case to the caller, lowered in
            # the HIR/MIR stage). Type-wise this is identical to the
            # postfix ``?`` operator.
            info = self._c.type_table.get_type(operand_type)
            if info and info.kind == TypeKind.ENUM:
                data = info.data
                if isinstance(data, EnumTypeData):
                    # The current function must also return a compatible
                    # Result type so the err case can be propagated; we
                    # only validate the structural shape (ENUM with two
                    # cases) here because Result lives in stdlib and may
                    # not be available.
                    cur = self._c._current_function_return
                    cur_info = (
                        self._c.type_table.get_type(cur) if cur is not None else None
                    )
                    if cur_info is None or cur_info.kind != TypeKind.ENUM:
                        self._c._error(
                            TypeErrorKind.INVALID_OPERATION,
                            "'try' can only be used in a function that "
                            "returns a Result type"
                        )
                        return self._c.type_table.error_type
                    if not self._is_result_shaped(data):
                        self._c._error(
                            TypeErrorKind.INVALID_OPERATION,
                            "'try' requires a Result type (an enum with 'ok' "
                            "and 'err' cases)"
                        )
                        return self._c.type_table.error_type
                    self._check_try_error_type(data, cur, cur_info, unop, operand_type)
                    if data.type_args:
                        return data.type_args[0]
                    # Non-generic Result: look up `ok` payload directly.
                    symbol = self._c.symbol_table.get_symbol(data.symbol_id)
                    if symbol and symbol.decl_node:
                        from . import ast as ast_module
                        if isinstance(symbol.decl_node, ast_module.EnumDecl):
                            for member in symbol.decl_node.members:
                                if hasattr(member, 'cases'):
                                    for case in member.cases:
                                        if case.name == "ok" and case.payload:
                                            _, payload_type = case.payload[0]
                                            return self._c._resolve_type(payload_type)
            self._c._error(
                TypeErrorKind.INVALID_OPERATION,
                "'try' requires a Result-typed expression"
            )
            return self._c.type_table.error_type

        return self._c.type_table.error_type

    def _infer_ternary_op(self, ternop: ast.TernaryOp) -> TypeId:
        """Infer type of a ternary operation."""
        if ternop.condition:
            cond_type = self._infer_expr(ternop.condition)
            self._c._check_boolean(cond_type, "ternary condition")

        then_type = self._infer_expr(ternop.then_expr) if ternop.then_expr else self._c.type_table.error_type
        else_type = self._infer_expr(ternop.else_expr) if ternop.else_expr else self._c.type_table.error_type

        # Result type is the common type of both branches
        if then_type == else_type:
            return then_type

        # Try implicit integer widening (e.g., i32 vs i64 -> i64)
        if self._c.type_table.can_widen_int(then_type, else_type):
            return else_type
        if self._c.type_table.can_widen_int(else_type, then_type):
            return then_type

        # Try optional promotion: T vs T? -> T?
        then_info = self._c.type_table.get_type(then_type)
        else_info = self._c.type_table.get_type(else_type)
        if then_info and else_info:
            if then_info.kind == TypeKind.OPTIONAL and else_info.kind != TypeKind.OPTIONAL:
                inner = then_info.data
                if isinstance(inner, OptionalTypeData) and self._c._types_equal(inner.inner, else_type):
                    return then_type
            if else_info.kind == TypeKind.OPTIONAL and then_info.kind != TypeKind.OPTIONAL:
                inner = else_info.data
                if isinstance(inner, OptionalTypeData) and self._c._types_equal(inner.inner, then_type):
                    return else_type

        # No common supertype found — report error but return then_type for recovery
        self._c._error(
            TypeErrorKind.TYPE_MISMATCH,
            f"Ternary branches have incompatible types: "
            f"'{self._c.type_table.format_type(then_type)}' vs "
            f"'{self._c.type_table.format_type(else_type)}'"
        )
        return then_type

    def _infer_call(self, call: ast.Call) -> TypeId:
        """Infer type of a function call."""
        # Capture the expected-type hint for THIS call and clear it for any
        # sub-expressions so nested calls don't inherit a stale context.
        expected = self._c._expected_type
        self._c._expected_type = None
        try:
            return self._infer_call_inner(call, expected)
        finally:
            self._c._expected_type = expected

    def _infer_call_inner(
        self,
        call: ast.Call,
        expected_type: Optional[TypeId],
    ) -> TypeId:
        if call.callee is None:
            return self._c.type_table.error_type

        callee_type = self._infer_expr(call.callee)

        # Check if it's a function type
        func_data = self._c.type_table.get_function_data(callee_type)
        if func_data:
            callee_symbol = self._c.node_symbols.get(id(call.callee))
            if callee_symbol is None and isinstance(call.callee, ast.MemberAccess):
                callee_symbol = self._c.member_method_symbols.get(id(call.callee))
            inferred_generics: Dict[str, TypeId] = {}
            if callee_symbol is not None:
                symbol = self._c.symbol_table.get_symbol(callee_symbol)
                if (
                    symbol is not None
                    and symbol.kind == SymbolKind.EXTERN_FUNC
                    and isinstance(symbol.decl_node, ast.ExternFuncDecl)
                ):
                    # All non-Rolang ABIs are unsafe. The previous gate
                    # checked only `abi == "C"`, leaving anything else
                    # silently safe to call from any context.
                    self._c._require_unsafe(
                        f"calling external {symbol.decl_node.abi!r} function",
                        call,
                    )
                elif (
                    symbol is not None
                    and isinstance(symbol.decl_node, ast.FuncDecl)
                    and getattr(symbol.decl_node, "is_unsafe", False)
                ):
                    self._c._require_unsafe(
                        f"calling `unsafe def {symbol.decl_node.name}`",
                        call,
                    )
                inferred_generics = self._c._infer_generic_call_args(callee_symbol, call, expected_type)
                if isinstance(call.callee, ast.MemberAccess) and self._is_type_reference(call.callee.object):
                    inferred_generics.update(
                        self._infer_static_method_owner_generics(callee_symbol, call, expected_type)
                    )
                    # Update the receiver expression's type so the HIR
                    # builder (and ultimately the MIR lowerer) sees the
                    # fully-instantiated struct type (e.g. Elem<String>
                    # rather than just Elem).
                    if inferred_generics:
                        self._update_static_receiver_type(
                            call.callee.object, inferred_generics
                        )

            # Async-safety: calling an async function from a non-async context
            # would later miscompile or behave non-deterministically. Reject it
            # at the type-check stage and point users at the fix.
            if func_data.is_async and not self._c._in_async_function:
                callee_name = "<async function>"
                if callee_symbol is not None:
                    sym = self._c.symbol_table.get_symbol(callee_symbol)
                    if sym is not None and sym.name:
                        callee_name = f"'{sym.name}'"
                self._c._error(
                    TypeErrorKind.INVALID_OPERATION,
                    f"async function {callee_name} can only be called from an "
                    "async function; mark the enclosing function 'async' or "
                    "use 'await' inside an async context"
                )

            # Check argument count
            decl_params = self._decl_params_for_callee(callee_symbol)
            expected_count = len(func_data.params)
            min_count = expected_count
            if decl_params is not None:
                while min_count > 0 and decl_params[min_count - 1].default_value is not None:
                    min_count -= 1

            argc = len(call.arguments)
            if argc > expected_count or argc < min_count:
                if min_count == expected_count:
                    msg = f"Expected {expected_count} arguments, got {argc}"
                else:
                    msg = f"Expected between {min_count} and {expected_count} arguments, got {argc}"
                self._c._error(TypeErrorKind.WRONG_ARG_COUNT, msg, node=call)
            else:
                if decl_params is not None:
                    for i, arg in enumerate(call.arguments):
                        expected_label = decl_params[i].external_name
                        if arg.label != expected_label:
                            expected_display = expected_label if expected_label is not None else "<none>"
                            actual_display = arg.label if arg.label is not None else "<none>"
                            self._c._error(
                                TypeErrorKind.WRONG_ARG_TYPE,
                                f"argument {i + 1} label mismatch: expected {expected_display}, got {actual_display}",
                                node=arg,
                            )
                # Check argument types
                for i, (arg, expected_type) in enumerate(zip(call.arguments, func_data.params)):
                    if arg.value:
                        arg_type = self._infer_expr(arg.value)
                        expected_type = self._c._substitute_type(expected_type, inferred_generics)
                        self._c._check_assignable(arg_type, expected_type, f"argument {i + 1}")

            # Record call target
            self._c._record_call_target(call, CalleeKind.STATIC, callee_symbol)

            return self._c._substitute_type(func_data.return_type, inferred_generics)

        # Check if callee is an enum case constructor: EnumName.case(args)
        info = self._c.type_table.get_type(callee_type)
        if (info and info.kind == TypeKind.ENUM
                and isinstance(call.callee, ast.MemberAccess)):
            case_name = call.callee.member
            case_def = self._c._lookup_enum_case(callee_type, case_name)
            if case_def is not None:
                instantiated = self._check_enum_ctor_args(
                    callee_type, case_def, call.arguments, expected_type
                )
                self._c.call_targets[id(call)] = CalleeId(
                    kind=CalleeKind.ENUM_CTOR,
                    case_name=case_name,
                )
                return instantiated

        # Not callable
        self._c._error(
            TypeErrorKind.NOT_CALLABLE,
            f"Cannot call {self._c.type_table.format_type(callee_type)}"
        )
        return self._c.type_table.error_type

    def _decl_params_for_callee(self, callee_symbol: Optional[SymbolId]) -> Optional[List[ast.Param]]:
        if callee_symbol is None:
            return None
        symbol = self._c.symbol_table.get_symbol(callee_symbol)
        if symbol is None:
            return None
        decl = symbol.decl_node
        if isinstance(decl, (ast.FuncDecl, ast.ExternFuncDecl)):
            return decl.params
        return None

    def _check_enum_ctor_args(
        self,
        enum_type: TypeId,
        case_def: ast.EnumCaseDef,
        args: List[ast.Argument],
        expected_type: Optional[TypeId] = None,
    ) -> TypeId:
        """Check arguments for an enum case constructor against its payload.

        Returns the (possibly instantiated) enum type. If the enum is generic
        and arguments allow it, type parameters are inferred from arg types and
        from any expected-type hint flowing in from the surrounding context.
        """
        if len(args) != len(case_def.payload):
            self._c._error(
                TypeErrorKind.WRONG_ARG_COUNT,
                f"Enum case '{case_def.name}' expects "
                f"{len(case_def.payload)} payload value(s), got {len(args)}"
            )
            return enum_type

        # Find the enum decl for generic-param info.
        info = self._c.type_table.get_type(enum_type)
        enum_decl: Optional[ast.EnumDecl] = None
        enum_symbol_id: Optional[SymbolId] = None
        if info and isinstance(info.data, EnumTypeData):
            enum_symbol_id = info.data.symbol_id
            symbol = self._c.symbol_table.get_symbol(enum_symbol_id)
            if symbol and isinstance(symbol.decl_node, ast.EnumDecl):
                enum_decl = symbol.decl_node

        generic_names = (
            {p.name for p in enum_decl.generic_params} if enum_decl else set()
        )
        # Seed inferred map from any existing type args (e.g. let x: MyOpt<i32>).
        inferred: Dict[str, TypeId] = {}
        if (enum_decl and isinstance(info.data, EnumTypeData)
                and len(info.data.type_args) == len(enum_decl.generic_params)):
            for param, arg in zip(enum_decl.generic_params, info.data.type_args):
                inferred[param.name] = arg

        # Seed inferred map from the expected type, if it's the same enum with
        # concrete args. Lets `return Result.ok(value: "x")` work when the
        # surrounding function returns `Result<String, String>`.
        if enum_decl and enum_symbol_id is not None and expected_type is not None:
            exp_info = self._c.type_table.get_type(expected_type)
            if (exp_info and exp_info.kind == TypeKind.ENUM
                    and isinstance(exp_info.data, EnumTypeData)
                    and exp_info.data.symbol_id == enum_symbol_id
                    and len(exp_info.data.type_args) == len(enum_decl.generic_params)):
                for param, arg in zip(enum_decl.generic_params, exp_info.data.type_args):
                    inferred.setdefault(param.name, arg)

        # Infer generics from argument types against payload annotations.
        for arg, (_, payload_type_node) in zip(args, case_def.payload):
            if arg.value is None:
                continue
            arg_type = self._infer_expr(arg.value)
            if generic_names:
                self._c._infer_type_node_generics(
                    payload_type_node, arg_type, generic_names, inferred
                )

        # Type-check each argument against the (substituted) payload type.
        for i, (arg, (_, payload_type_node)) in enumerate(zip(args, case_def.payload)):
            if arg.value is None:
                continue
            arg_type = self._c.expr_types.get(id(arg.value))
            if arg_type is None:
                arg_type = self._infer_expr(arg.value)
            expected = self._c._substitute_type(self._c._resolve_type(payload_type_node), inferred)
            self._c._check_assignable(arg_type, expected, f"enum payload {i + 1}")

        # Build the instantiated enum type if all generic params are bound.
        if enum_decl and enum_symbol_id is not None and enum_decl.generic_params:
            unbound = [p.name for p in enum_decl.generic_params if p.name not in inferred]
            if unbound:
                self._c._error(
                    TypeErrorKind.CANNOT_INFER,
                    f"Cannot infer type parameter(s) {', '.join(unbound)} of "
                    f"enum '{enum_decl.name}.{case_def.name}' from arguments; "
                    f"add a type annotation to the binding"
                )
                return enum_type
            self._c._check_generic_constraints(inferred, enum_decl.generic_params)
            type_args = tuple(inferred[p.name] for p in enum_decl.generic_params)
            return self._c.type_table.make_enum(enum_symbol_id, type_args)

        return enum_type

    def _infer_member_access(self, access: ast.MemberAccess) -> TypeId:
        """Infer type of a member access."""
        if access.object is None:
            return self._c.type_table.error_type

        # Handle nested module namespace access (e.g., std.io.println)
        if isinstance(access.object, (ast.Identifier, ast.MemberAccess)):
            parts = self._collect_member_parts(access)
            if parts:
                qualified = ".".join(parts)
                if qualified in self._c.imported_symbols:
                    symbol_id = self._c.imported_symbols[qualified]
                    sym = self._c.symbol_table.get_symbol(symbol_id)
                    if sym is not None and sym.decl_node is not None:
                        from .symbols import SymbolKind
                        from . import ast as ast_module
                        if sym.kind == SymbolKind.FUNCTION and isinstance(sym.decl_node, ast_module.FuncDecl):
                            func = sym.decl_node
                            params = []
                            for param in func.params:
                                param_type = self._c._resolve_type(param.type_annotation)
                                params.append(param_type)
                            ret_type = self._c._resolve_type(func.return_type) if func.return_type else self._c.type_table.void_type
                            return self._c.type_table.make_function(params=tuple(params), return_type=ret_type, is_async=func.is_async)
                        if sym.kind == SymbolKind.STRUCT:
                            return self._c.type_table.make_struct(symbol_id)
                        if sym.kind == SymbolKind.ENUM:
                            return self._c.type_table.make_enum(symbol_id)
                    return self._c.type_table.error_type

        # Handle single-level module namespace access (e.g., lib.get_value)
        if isinstance(access.object, ast.Identifier):
            qualified = f"{access.object.name}.{access.member}"
            if qualified in self._c.imported_symbols:
                symbol_id = self._c.imported_symbols[qualified]
                sym = self._c.symbol_table.get_symbol(symbol_id)
                if sym is not None and sym.decl_node is not None:
                    from .symbols import SymbolKind
                    from . import ast as ast_module
                    if sym.kind == SymbolKind.FUNCTION and isinstance(sym.decl_node, ast_module.FuncDecl):
                        func = sym.decl_node
                        params = []
                        for param in func.params:
                            param_type = self._c._resolve_type(param.type_annotation)
                            params.append(param_type)
                        ret_type = self._c._resolve_type(func.return_type) if func.return_type else self._c.type_table.void_type
                        return self._c.type_table.make_function(
                            params=tuple(params), return_type=ret_type, is_async=func.is_async
                        )
                return self._c.type_table.error_type

        obj_type = self._infer_expr(access.object)

        # Handle auto-deref (no-op in v2)
        actual_type = self._c._auto_deref(obj_type)
        object_is_type = self._is_type_reference(access.object)

        info = self._c.type_table.get_type(actual_type)
        if info and info.kind == TypeKind.EXISTENTIAL:
            from .types import ExistentialTypeData, ProtocolTypeData
            data = info.data
            if isinstance(data, ExistentialTypeData):
                # Look up method in the protocol
                protocol_info = self._c.type_table.get_type(data.protocol_id)
                if protocol_info and isinstance(protocol_info.data, ProtocolTypeData):
                    proto_data = protocol_info.data
                    for func_req in proto_data.func_requirements:
                        if func_req.name == access.member:
                            # Return the method signature
                            return self._c.type_table.make_function(
                                params=func_req.params,
                                return_type=func_req.return_type,
                                is_async=False
                            )
                    for prop_req in proto_data.prop_requirements:
                        if prop_req.name == access.member:
                            return prop_req.type_id

        # Type variable with protocol bounds (generic param like `T: Show`):
        # search each bound's requirements.
        if info and info.kind == TypeKind.TYPE_VARIABLE:
            from .types import ProtocolTypeData
            data = info.data
            if isinstance(data, TypeVariableData):
                for bound_id in data.bounds:
                    bound_info = self._c.type_table.get_type(bound_id)
                    if not (bound_info and isinstance(bound_info.data, ProtocolTypeData)):
                        continue
                    proto_data = bound_info.data
                    for func_req in proto_data.func_requirements:
                        if func_req.name == access.member:
                            return self._c.type_table.make_function(
                                params=func_req.params,
                                return_type=func_req.return_type,
                                is_async=func_req.is_async,
                            )
                    for prop_req in proto_data.prop_requirements:
                        if prop_req.name == access.member:
                            return prop_req.type_id

        if not object_is_type:
            field = self._c.member_resolver.get_field(actual_type, access.member)
            if field:
                self._enforce_field_visibility(field, access)
                return field.type_id

        method = self._c.member_resolver.get_method(
            actual_type,
            access.member,
            static=object_is_type,
        )
        if method:
            # Record the resolved method's SymbolId in a side-table so the
            # HIR builder can disambiguate same-named methods across modules
            # without polluting `node_symbols` (which the HIR builder uses
            # as a heuristic to distinguish import-alias member access from
            # ordinary method access).
            self._c.member_method_symbols[id(access)] = method.symbol_id
            return method.signature

        # Check if it's an enum case access (EnumType.caseName)
        if object_is_type and info and info.kind == TypeKind.ENUM:
            case_def = self._c._lookup_enum_case(actual_type, access.member)
            if case_def is not None and not case_def.payload:
                # No-payload enum case used as a value: record so HIR can build it.
                self._c.call_targets[id(access)] = CalleeId(
                    kind=CalleeKind.ENUM_CTOR,
                    case_name=access.member,
                )
            # This is accessing an enum case - return the enum type
            return actual_type

        self._c._error(
            TypeErrorKind.UNDEFINED_MEMBER,
            f"Type {self._c.type_table.format_type(actual_type)} has no member '{access.member}'",
            node=access,
        )
        return self._c.type_table.error_type

    def _infer_subscript(self, sub: ast.Subscript) -> TypeId:
        """Infer type of a subscript expression.

        Subscript routing after the legacy `[T]` / `[K: V]` removal:
            * `Vec<T>`        -> `T` (forwards to `get`)
            * `Dict<K, V>`    -> `V?` (forwards to `get`)
            * tuples          -> element type by integer literal index
        """
        if sub.object is None:
            return self._c.type_table.error_type

        obj_type = self._infer_expr(sub.object)
        info = self._c.type_table.get_type(obj_type)

        if info is None:
            return self._c.type_table.error_type

        # Check index types
        for index in sub.indices:
            self._infer_expr(index)

        if info.kind == TypeKind.STRUCT:
            data = info.data
            if isinstance(data, StructTypeData):
                if data.symbol_id is None:
                    # Anonymous struct (tuple) subscript with integer literal
                    if len(sub.indices) == 1:
                        idx = sub.indices[0]
                        if isinstance(idx, ast.Literal) and idx.kind == "int":
                            fields = data.anon_fields or ()
                            index = idx.value
                            if isinstance(index, int) and 0 <= index < len(fields):
                                return fields[index][1]
                else:
                    struct_name = self._struct_symbol_name(data.symbol_id)
                    if struct_name == "Vec" and len(data.type_args) == 1:
                        return data.type_args[0]
                    if struct_name == "Dict" and len(data.type_args) == 2:
                        return self._c.type_table.make_optional(data.type_args[1])
                    # __get__ dunder method: any struct with def __get__(index: I) -> T
                    # supports subscript syntax a[index].
                    get_method = self._c.member_resolver.get_method(obj_type, "__get__")
                    if get_method is not None:
                        func_data = self._c.type_table.get_function_data(get_method.signature)
                        if func_data and len(func_data.params) >= 1 and func_data.return_type:
                            return func_data.return_type

        self._c._error(
            TypeErrorKind.TYPE_MISMATCH,
            f"Type {self._c.type_table.format_type(obj_type)} does not support subscripting",
            node=sub,
        )
        return self._c.type_table.error_type

    def _struct_symbol_name(self, symbol_id: SymbolId) -> Optional[str]:
        """Look up the source-level name of a struct symbol, or None."""
        sym = self._c.symbol_table.get_symbol(symbol_id)
        return sym.name if sym is not None else None

    def _infer_tuple(self, tup: ast.TupleExpr) -> TypeId:
        """Infer type of a tuple expression."""
        elements: List[Tuple[Optional[str], TypeId]] = []
        for label, elem in tup.elements:
            elem_type = self._infer_expr(elem)
            elements.append((label, elem_type))

        return self._c.type_table.make_tuple(tuple(elements))

    def _infer_array_literal(self, arr: ast.ArrayLiteral) -> TypeId:
        """Infer the type of an array literal as `Vec<T>`.

        The legacy builtin array type has been removed: `[1, 2, 3]` is
        sugar for `Vec<i32>` and lowers to `Vec.with_capacity` + `push`
        during MIR construction.
        """
        if not arr.elements:
            # Empty literal — element type is a fresh variable; the
            # surrounding context (var annotation, function arg, etc.)
            # is responsible for pinning it down.
            elem_type = self._c.type_table.make_type_variable("vec_elem")
            return self._c.type_resolver.make_vec_type(elem_type)

        # Infer element types
        elem_types = [self._infer_expr(elem) for elem in arr.elements]

        # Use the first element's type
        elem_type = elem_types[0] if elem_types else self._c.type_table.error_type

        # Check that all elements have the same type
        for i, t in enumerate(elem_types[1:], 1):
            if not self._c._types_equal(t, elem_type) and not self._c.type_table.is_error(t):
                self._c._error(
                    TypeErrorKind.TYPE_MISMATCH,
                    f"Vec element at index {i} has type {self._c.type_table.format_type(t)}, "
                    f"expected {self._c.type_table.format_type(elem_type)}"
                )

        return self._c.type_resolver.make_vec_type(elem_type)

    def _infer_dict_literal(self, dict_lit: ast.DictLiteral) -> TypeId:
        """Infer the type of a dict literal as `Dict<K, V>`.

        The legacy builtin dict type has been removed: `["a": 1, "b": 2]`
        is sugar for `Dict<String, i32>` and lowers to
        `Dict.with_capacity` + `set` during MIR construction.
        """
        if not dict_lit.entries:
            # Empty literal — key/value types are fresh variables.
            key_type = self._c.type_table.make_type_variable("dict_key")
            value_type = self._c.type_table.make_type_variable("dict_value")
            return self._c.type_resolver.make_dict_type(key_type, value_type)

        # Infer key and value types from first entry
        first_key, first_val = dict_lit.entries[0]
        key_type = self._infer_expr(first_key)
        value_type = self._infer_expr(first_val)

        # Check remaining entries
        for i, (k, v) in enumerate(dict_lit.entries[1:], 1):
            k_type = self._infer_expr(k)
            v_type = self._infer_expr(v)

            if not self._c._types_equal(k_type, key_type) and not self._c.type_table.is_error(k_type):
                self._c._error(
                    TypeErrorKind.TYPE_MISMATCH,
                    f"Dictionary key at index {i} has inconsistent type"
                )
            if not self._c._types_equal(v_type, value_type) and not self._c.type_table.is_error(v_type):
                self._c._error(
                    TypeErrorKind.TYPE_MISMATCH,
                    f"Dictionary value at index {i} has inconsistent type"
                )

        return self._c.type_resolver.make_dict_type(key_type, value_type)

    def _infer_lambda(self, lam: ast.Lambda) -> TypeId:
        """Infer type of a lambda expression.

        A lambda body is checked in a *safe* context regardless of the
        surrounding code. The closure value itself can escape an
        `unsafe { ... }` block, so the body's unsafe operations would
        otherwise execute later in a safe call site — defeating the
        purpose of the unsafe gate. Forcing the body to be safe means
        `unsafe extern` / `RawPtr` operations inside a lambda must use
        their own `unsafe { }` block, scoped to the closure body.
        """
        param_types: List[TypeId] = []

        for pattern, type_ann in lam.params:
            if type_ann:
                param_type = self._c._resolve_type(type_ann)
            else:
                param_type = self._c.type_table.make_type_variable("lambda_param")
            param_types.append(param_type)
            self._c._bind_pattern_type(pattern, param_type)

        # Save and clear current function return type for lambda body.
        old_return_type = self._c._current_function_return
        self._c._current_function_return = None  # Lambdas infer their return type
        # Force the lambda body to type-check as safe even if the lambda
        # literal appears inside an `unsafe { ... }` block.
        old_in_unsafe = self._c._in_unsafe
        self._c._in_unsafe = False
        # A lambda is its own (synchronous) function value: the enclosing
        # function's async-ness must not leak in, or the body would be allowed
        # to call async functions directly (forbidden — async may only be
        # awaited from an async function, and a lambda is never async).
        old_in_async = self._c._in_async_function
        self._c._in_async_function = False

        # Check body and infer return type
        return_type = self._c.type_table.void_type
        for stmt in lam.body:
            self._c._check_stmt(stmt)
        return_type = self._infer_block_return_type(lam.body)

        # Restore outer function return type and unsafe state.
        self._c._current_function_return = old_return_type
        self._c._in_unsafe = old_in_unsafe
        self._c._in_async_function = old_in_async

        return self._c.type_table.make_function(tuple(param_types), return_type)

    def _infer_struct_literal(self, literal: ast.StructLiteral) -> TypeId:
        """Infer type of a struct literal: Type { field: value }."""
        if literal.type_name is None:
            return self._c.type_table.error_type

        type_id = self._c._resolve_named_type(literal.type_name)

        info = self._c.type_table.get_type(type_id)
        if info:
            type_id = self._c._check_struct_literal_fields(
                info,
                literal.arguments,
                self._c._expected_type,
            )

        return type_id

    def _is_type_reference(self, expr: ast.Expr | None) -> bool:
        if expr is None:
            return False
        if isinstance(expr, ast.TypeReference):
            return True
        symbol_id = self._c.node_symbols.get(id(expr))
        if symbol_id is None:
            return False
        symbol = self._c.symbol_table.get_symbol(symbol_id)
        return symbol is not None and symbol.kind in {
            SymbolKind.STRUCT,
            SymbolKind.ENUM,
            SymbolKind.BUILTIN_TYPE,
        }

    def _infer_static_method_owner_generics(
        self,
        method_symbol: SymbolId,
        call: ast.Call,
        expected_type: Optional[TypeId],
    ) -> Dict[str, TypeId]:
        symbol = self._c.symbol_table.get_symbol(method_symbol)
        if symbol is None or not isinstance(symbol.decl_node, ast.FuncDecl):
            return {}
        method = symbol.decl_node

        owner = self._find_method_owner(method)
        if owner is None or not owner.generic_params:
            return {}

        generic_names = {param.name for param in owner.generic_params}
        inferred: Dict[str, TypeId] = {}
        for i, arg in enumerate(call.arguments):
            if i >= len(method.params) or arg.value is None:
                break
            arg_type = self._c.expr_types.get(id(arg.value))
            if arg_type is None:
                arg_type = self._infer_expr(arg.value)
            self._c._infer_type_node_generics(
                method.params[i].type_annotation,
                arg_type,
                generic_names,
                inferred,
            )

        if expected_type is not None and method.return_type is not None:
            self._c._infer_type_node_generics(
                method.return_type,
                expected_type,
                generic_names,
                inferred,
            )
        return inferred

    def _update_static_receiver_type(
        self,
        obj_expr: ast.Expr,
        inferred_generics: Dict[str, TypeId],
    ) -> None:
        """Update a type-reference expression to carry inferred generic args."""
        symbol_id = self._c.node_symbols.get(id(obj_expr))
        if symbol_id is None:
            return
        symbol = self._c.symbol_table.get_symbol(symbol_id)
        if symbol is None or symbol.decl_node is None:
            return
        decl = symbol.decl_node
        if not isinstance(decl, (ast.StructDecl, ast.EnumDecl)):
            return
        generic_params = getattr(decl, "generic_params", [])
        if not generic_params:
            return
        type_args = tuple(
            inferred_generics.get(p.name, self._c.type_table.error_type)
            for p in generic_params
        )
        if not all(self._c.type_table.get_type(t) is not None for t in type_args):
            return
        if symbol.kind == SymbolKind.STRUCT:
            specialized = self._c.type_table.make_struct(symbol_id, type_args)
        else:
            specialized = self._c.type_table.make_enum(symbol_id, type_args)
        self._c.expr_types[id(obj_expr)] = specialized

    def _find_method_owner(self, method: ast.FuncDecl) -> Optional[ast.StructDecl | ast.EnumDecl]:
        for symbol in self._c.symbol_table.symbols.values():
            decl = symbol.decl_node
            if isinstance(decl, (ast.StructDecl, ast.EnumDecl)):
                if any(member is method for member in decl.members):
                    return decl
        return None

    def _enforce_field_visibility(self, field, access_node) -> None:
        """Reject cross-module access to non-`pub` fields.

        Mirrors the visibility filter already used for extension methods:
        a field marked `internal`/`private` (the default if no visibility
        marker is present on a `var` declaration) is only reachable from
        the module that declared the containing struct. This is how the
        stdlib keeps its `RawPtr` handles (`String.handle`, `Vec.handle`,
        `Dict.handle`, `File.handle`, ...) hidden from safe user code.
        """
        if field.visibility == "pub":
            return
        callsite = self._c.member_resolver._current_source_module
        decl_module = field.source_module
        if (
            decl_module is not None
            and callsite is not None
            and decl_module != callsite
        ):
            self._c._error(
                TypeErrorKind.INVALID_OPERATION,
                f"field '{field.name}' is not accessible from outside module "
                f"'{decl_module}' (mark it `pub` to expose it)",
                node=access_node,
            )

    def _infer_cast(self, cast: ast.Cast) -> TypeId:
        """Infer type of a type cast.

        The `as` operator is intentionally restricted. Only conversions that
        the runtime can perform safely are accepted:

            * numeric -> numeric (incl. int <-> float, signed <-> unsigned)
            * bool <-> numeric
            * identity (T -> T)
            * unsafe: any -> RawPtr / RawPtr -> any
            * any -> Optional<T> (lifts to T?)
            * Type variables / error types pass through (handled by
              monomorphization).

        Runtime-checked downcasts use the dedicated operators:

            * ``e as? T`` — yields ``Optional<T>``: ``Some`` iff the
              existential ``e`` actually carries a value of concrete
              type ``T``, else ``None``.
            * ``e as! T`` — yields ``T`` directly; panics at runtime if
              ``e`` does not carry a ``T``.

        Everything else is rejected. These were previously accepted and
        silently miscompiled into LLVM bitcasts that broke memory safety.
        """
        source_type = self._c.type_table.error_type
        if cast.expr:
            source_type = self._infer_expr(cast.expr)

        if not cast.target_type:
            return self._c.type_table.error_type

        target_type = self._c._resolve_type(cast.target_type)
        kind = getattr(cast, "kind", "safe")

        if kind in ("optional", "forced"):
            return self._infer_runtime_downcast(cast, source_type, target_type, kind)

        # Unsafe-gated: any cast involving RawPtr.
        src_is_raw = self._c._is_raw_ptr_type(source_type)
        tgt_is_raw = self._c._is_raw_ptr_type(target_type)
        if src_is_raw or tgt_is_raw:
            self._c._require_unsafe("casting to or from RawPtr", cast)
            return target_type

        # Error types pass through (avoid cascading diagnostics).
        if self._c.type_table.is_error(source_type) or self._c.type_table.is_error(target_type):
            return target_type

        # Identity cast.
        if self._c._types_equal(source_type, target_type):
            return target_type

        tt = self._c.type_table
        src_info = tt.get_type(source_type)
        tgt_info = tt.get_type(target_type)

        # Type variables (generic contexts): allow — monomorphization will
        # substitute and the resulting cast will be re-checked there if
        # still present.
        if (src_info and src_info.kind == TypeKind.TYPE_VARIABLE) or (
            tgt_info and tgt_info.kind == TypeKind.TYPE_VARIABLE
        ):
            return target_type

        # numeric <-> numeric (int/int, int/float, float/float)
        if tt.is_numeric(source_type) and tt.is_numeric(target_type):
            return target_type

        # bool <-> numeric
        if tt.is_bool(source_type) and tt.is_numeric(target_type):
            return target_type
        if tt.is_numeric(source_type) and tt.is_bool(target_type):
            return target_type
        # bool <-> bool already covered by identity.

        # T -> T? promotion through `as`. Identity wrt the inner T.
        if tgt_info and tgt_info.kind == TypeKind.OPTIONAL:
            inner = tt.get_optional_inner(target_type)
            if inner is not None and (
                self._c._types_equal(source_type, inner)
                or tt.is_error(inner)
            ):
                return target_type

        # Anything else: reject with a helpful message.
        msg = (
            f"cannot cast {tt.format_type(source_type)} to "
            f"{tt.format_type(target_type)} using `as`. "
        )
        # Tailored hints for common foot-guns.
        if src_info and src_info.kind in (TypeKind.STRUCT, TypeKind.ENUM):
            if tgt_info and tgt_info.kind in (TypeKind.STRUCT, TypeKind.ENUM):
                msg += (
                    "Heap types cannot be reinterpreted as other heap types; "
                    "construct the target type explicitly."
                )
            elif tt.is_numeric(target_type):
                msg += (
                    "Heap types cannot be reinterpreted as integers."
                )
            else:
                msg += "Heap-to-non-heap casts are not allowed."
        elif src_info and src_info.kind == TypeKind.EXISTENTIAL:
            msg += (
                "Existential downcasts via `as` are not supported; use "
                "the runtime-checked operators `as?` (optional) or "
                "`as!` (force-unwrap) instead."
            )
        else:
            msg += "Only numeric, bool and identity casts are allowed in safe code."

        self._c._error(TypeErrorKind.INVALID_OPERATION, msg, node=cast)
        return target_type

    def _infer_runtime_downcast(
        self,
        cast: ast.Cast,
        source_type: TypeId,
        target_type: TypeId,
        kind: str,
    ) -> TypeId:
        """Type-check ``e as? T`` and ``e as! T``.

        Both forms require ``e`` to have an existential type (``any P``).
        ``as?`` yields ``Optional<T>``; ``as!`` yields ``T`` directly and
        panics at runtime on a mismatch. ``T`` must itself conform to the
        existential's protocol — otherwise the cast can never succeed.
        """
        tt = self._c.type_table

        # Error types short-circuit (avoid cascading diagnostics).
        if tt.is_error(source_type) or tt.is_error(target_type):
            return target_type if kind == "forced" else tt.make_optional(target_type)

        # Type variables: defer to monomorphization.
        src_info = tt.get_type(source_type)
        tgt_info = tt.get_type(target_type)
        if (src_info and src_info.kind == TypeKind.TYPE_VARIABLE) or (
            tgt_info and tgt_info.kind == TypeKind.TYPE_VARIABLE
        ):
            return target_type if kind == "forced" else tt.make_optional(target_type)

        if src_info is None or src_info.kind != TypeKind.EXISTENTIAL:
            self._c._error(
                TypeErrorKind.INVALID_OPERATION,
                (
                    f"runtime downcast `as{'?' if kind == 'optional' else '!'}` "
                    f"requires an existential source; got "
                    f"{tt.format_type(source_type)}"
                ),
                node=cast,
            )
            return target_type if kind == "forced" else tt.make_optional(target_type)

        if tgt_info is None or tgt_info.kind not in (
            TypeKind.STRUCT, TypeKind.ENUM,
        ):
            self._c._error(
                TypeErrorKind.INVALID_OPERATION,
                (
                    f"runtime downcast target must be a concrete struct or "
                    f"enum type, got {tt.format_type(target_type)}"
                ),
                node=cast,
            )
            return target_type if kind == "forced" else tt.make_optional(target_type)

        # The target must conform to the existential's protocol — otherwise
        # the cast can never succeed at runtime, which is almost certainly
        # a typo.
        from .types import ExistentialTypeData
        if isinstance(src_info.data, ExistentialTypeData):
            protocol_type = src_info.data.protocol_id
            conformance = self._c.conformance_checker.check_conformance(
                target_type, protocol_type,
            )
            if not conformance.conforms:
                self._c._error(
                    TypeErrorKind.INVALID_OPERATION,
                    (
                        f"{tt.format_type(target_type)} does not conform to "
                        f"{tt.format_type(protocol_type)}; runtime downcast "
                        f"can never succeed"
                    ),
                    node=cast,
                )

        if kind == "forced":
            return target_type
        return tt.make_optional(target_type)

    def _infer_type_check(self, check: ast.TypeCheck) -> TypeId:
        """Infer type of a type check (expr is Type)."""
        if check.expr:
            self._infer_expr(check.expr)

        return self._c.type_table.get_builtin("Bool") or self._c.type_table.error_type

    def _infer_try_expr(self, expr: ast.TryExpr) -> TypeId:
        """Infer type of a try expression (x?).
        
        x? returns T where x is Result<T, E>. The T is the ok-case payload type.
        """
        if expr.value is None:
            return self._c.type_table.error_type
            
        inner_type = self._infer_expr(expr.value)
        info = self._c.type_table.get_type(inner_type)
        
        if info and info.kind == TypeKind.ENUM:
            data = info.data
            if isinstance(data, EnumTypeData):
                cur = self._c._current_function_return
                cur_info = (
                    self._c.type_table.get_type(cur) if cur is not None else None
                )
                # Postfix `?` propagates the err case via an early return, so —
                # exactly like prefix `try` — the enclosing function must itself
                # return a Result-shaped (enum) type. Without this guard `r?`
                # was accepted inside e.g. an i32-returning function and only
                # blew up later as invalid LLVM IR.
                if cur_info is None or cur_info.kind != TypeKind.ENUM:
                    self._c._error(
                        TypeErrorKind.INVALID_OPERATION,
                        "'?' can only be used in a function that returns a "
                        "Result type"
                    )
                    return self._c.type_table.error_type
                if not self._is_result_shaped(data):
                    self._c._error(
                        TypeErrorKind.INVALID_OPERATION,
                        "'?' requires a Result type (an enum with 'ok' and "
                        "'err' cases)"
                    )
                    return self._c.type_table.error_type
                if isinstance(cur_info.data, EnumTypeData):
                    self._check_try_error_type(data, cur, cur_info, expr, inner_type)
                if data.type_args:
                    return data.type_args[0]
                # For non-generic Result, look up the 'ok' case payload
                symbol = self._c.symbol_table.get_symbol(data.symbol_id)
                if symbol and symbol.decl_node:
                    from . import ast as ast_module
                    if isinstance(symbol.decl_node, ast_module.EnumDecl):
                        for member in symbol.decl_node.members:
                            if hasattr(member, 'cases'):
                                for case in member.cases:
                                    if case.name == "ok" and case.payload:
                                        _, payload_type = case.payload[0]
                                        return self._c._resolve_type(payload_type)
        
        return self._c.type_table.error_type

    def _infer_size_of_expr(self, expr: ast.SizeOfExpr) -> TypeId:
        """Infer type of size_of(T) — always i32."""
        if expr.type_arg is None:
            return self._c.type_table.error_type
        type_id = self._c._resolve_type(expr.type_arg)
        size = self._c.layout.size_of(type_id)
        expr._sizeof_type_id = type_id
        # Store the computed size as a "constant" on the node for codegen
        expr._computed_size = size
        return self._c.type_table.get_builtin("i32") or self._c.type_table.error_type

    def _infer_type_id_expr(self, expr: ast.TypeIdExpr) -> TypeId:
        """Infer type_id(T) — runtime type-descriptor index as i32."""
        if expr.type_arg is None:
            return self._c.type_table.error_type
        type_id = self._c._resolve_type(expr.type_arg)
        expr._typeid_type_id = type_id
        return self._c.type_table.get_builtin("i32") or self._c.type_table.error_type

    def _infer_align_of_expr(self, expr: ast.AlignOfExpr) -> TypeId:
        """Infer type of align_of(T) — always i32."""
        if expr.type_arg is None:
            return self._c.type_table.error_type
        type_id = self._c._resolve_type(expr.type_arg)
        expr._alignof_type_id = type_id
        expr._computed_align = self._c.layout.align_of(type_id)
        return self._c.type_table.get_builtin("i32") or self._c.type_table.error_type

    def _infer_drop_of_expr(self, expr: ast.DropOfExpr) -> TypeId:
        """Infer type of drop_of(T) — Bool; true if T has a __release__ deinit."""
        if expr.type_arg is None:
            return self._c.type_table.error_type
        type_id = self._c._resolve_type(expr.type_arg)
        expr._dropof_type_id = type_id
        # Check whether the type has a __release__ method
        method = self._c.member_resolver.get_method(type_id, "__release__")
        expr._has_drop = method is not None
        return self._c.type_table.get_builtin("Bool") or self._c.type_table.error_type

    def _infer_clone_of_expr(self, expr: ast.CloneOfExpr) -> TypeId:
        """Infer type of clone_of(T) — Bool; true if T has a .clone() method."""
        if expr.type_arg is None:
            return self._c.type_table.error_type
        type_id = self._c._resolve_type(expr.type_arg)
        expr._cloneof_type_id = type_id
        method = self._c.member_resolver.get_method(type_id, "clone")
        expr._has_clone = method is not None
        return self._c.type_table.get_builtin("Bool") or self._c.type_table.error_type

    def _infer_optional_chain(self, chain: ast.OptionalChain) -> TypeId:
        """Infer type of optional chaining."""
        if chain.object is None:
            return self._c.type_table.error_type

        obj_type = self._infer_expr(chain.object)

        # Unwrap optional if present
        inner = self._c.type_table.get_optional_inner(obj_type)
        base_type = inner if inner else obj_type

        # Look up member
        field = self._c.member_resolver.get_field(base_type, chain.member)
        if field:
            self._enforce_field_visibility(field, chain)
            # Result is optional
            return self._c.type_table.make_optional(field.type_id)

        method = self._c.member_resolver.get_method(base_type, chain.member)
        if method:
            # Handle suffix (call or subscript)
            if chain.suffix and isinstance(chain.suffix, list):
                # It's a call
                func_data = self._c.type_table.get_function_data(method.signature)
                if func_data:
                    return self._c.type_table.make_optional(func_data.return_type)
            return self._c.type_table.make_optional(method.signature)

        self._c._error(
            TypeErrorKind.UNDEFINED_MEMBER,
            f"Type {self._c.type_table.format_type(base_type)} has no member '{chain.member}'",
            node=chain,
        )
        return self._c.type_table.error_type
