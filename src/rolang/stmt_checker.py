"""Statement type checking for Rolang."""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from . import ast
from .types import TypeId
from .symbols import SymbolKind
from .checker_core import TypeErrorKind
from .exhaustiveness import ExhaustivenessChecker

if TYPE_CHECKING:
    from .checker import TypeChecker


class StmtChecker:
    """Check statements: var decls, assignments, control flow."""

    def __init__(self, checker: TypeChecker) -> None:
        self._c = checker


    def _check_block(self, block: ast.Block) -> None:
        """Type check a block of statements."""
        old_in_unsafe = self._c._in_unsafe
        if block.is_unsafe:
            self._c._in_unsafe = True
        try:
            for stmt in block.statements:
                self._check_stmt(stmt)
        finally:
            self._c._in_unsafe = old_in_unsafe

    def _check_stmt(self, stmt: ast.Stmt) -> None:
        """Type check a statement."""
        if isinstance(stmt, ast.VarDecl):
            self._check_var_decl(stmt)
        elif isinstance(stmt, ast.Assignment):
            self._check_assignment(stmt)
        elif isinstance(stmt, ast.ExprStmt):
            if stmt.expr:
                self._c._infer_expr(stmt.expr)
        elif isinstance(stmt, ast.ReturnStmt):
            self._check_return(stmt)
        elif isinstance(stmt, ast.Block):
            self._check_block(stmt)
        elif isinstance(stmt, ast.IfStmt):
            self._check_if_stmt(stmt)
        elif isinstance(stmt, ast.WhileStmt):
            self._check_while_stmt(stmt)
        elif isinstance(stmt, ast.ForStmt):
            self._check_for_stmt(stmt)
        elif isinstance(stmt, ast.SwitchStmt):
            self._check_switch_stmt(stmt)
        elif isinstance(stmt, ast.GuardStmt):
            self._check_guard_stmt(stmt)
        elif isinstance(stmt, ast.DeferStmt):
            if stmt.body:
                self._check_block(stmt.body)

    def _definitely_returns_block(self, block: ast.Block) -> bool:
        for stmt in block.statements:
            if self._definitely_returns_stmt(stmt):
                return True
        return False

    def _definitely_returns_stmt(self, stmt: ast.Stmt) -> bool:
        if isinstance(stmt, ast.ReturnStmt):
            return True
        if isinstance(stmt, ast.Block):
            return self._definitely_returns_block(stmt)
        if isinstance(stmt, ast.IfStmt):
            if stmt.then_block is None or stmt.else_block is None:
                return False
            then_returns = self._definitely_returns_block(stmt.then_block)
            if isinstance(stmt.else_block, ast.IfStmt):
                else_returns = self._definitely_returns_stmt(stmt.else_block)
            else:
                else_returns = self._definitely_returns_block(stmt.else_block)
            return then_returns and else_returns
        if isinstance(stmt, ast.SwitchStmt):
            if not stmt.cases:
                return False
            return all(self._definitely_returns_case(case) for case in stmt.cases)
        return False

    def _definitely_returns_case(self, case: ast.SwitchCase) -> bool:
        for stmt in case.body:
            if self._definitely_returns_stmt(stmt):
                return True
        return False

    def _check_var_decl(self, var_decl: ast.VarDecl) -> None:
        """Type check a variable declaration."""
        # Determine variable type
        var_type: Optional[TypeId] = None

        if var_decl.type_annotation:
            var_type = self._c._resolve_type(var_decl.type_annotation)

        if var_decl.initializer:
            # Push the annotation (if any) as the expected type so generic
            # constructors can pick up unbound parameters from context.
            init_type = self._c._infer_with_expected(var_decl.initializer, var_type)

            if var_type is not None:
                # Check that initializer is assignable to declared type
                self._c._check_assignable(init_type, var_type, "variable initializer", node=var_decl)
            else:
                # Infer type from initializer
                if self._c.type_table.is_error(init_type):
                    var_type = self._c.type_table.error_type
                else:
                    var_type = init_type

        if var_type is None:
            self._c._error(
                TypeErrorKind.CANNOT_INFER,
                "Cannot infer type without initializer or annotation",
            )
            var_type = self._c.type_table.error_type

        # Bind pattern bindings to the type
        if var_decl.pattern:
            self._c._bind_pattern_type(var_decl.pattern, var_type)

    def _check_assignment(self, assign: ast.Assignment) -> None:
        """Type check an assignment statement."""
        if assign.target and assign.value:
            # Reject re-binding `let` locals: `let x = 1; x = 2;` is not
            # allowed. Field assignment through a `let`-bound reference IS
            # allowed (the binding is immutable, the heap object behind it is not).
            self._check_let_reassignment(assign.target)

            target_type = self._c._infer_expr(assign.target)
            value_type = self._c._infer_expr(assign.value)

            # Handle compound assignment
            if assign.op != "=":
                base_op = assign.op[:-1]  # Remove '='
                overloaded = self._c.expr_checker._try_operator_overload(None, target_type, base_op, value_type)
                if overloaded is None:
                    self._c._check_binary_op_types_raw(target_type, base_op, value_type)
            else:
                self._c._check_assignable(value_type, target_type, "assignment", node=assign)

    def _check_let_reassignment(self, target: ast.Expr) -> None:
        """
        Reject `x = ...` where `x` was bound with `let` (or is otherwise an
        immutable identifier — function params, `self`, etc.).

        Only direct identifier targets are checked; field assignments like
        `x.field = ...` are always allowed because every struct/enum value
        lives on the heap and is reached through an ARC reference — the
        binding's mutability tracks the *binding* only, not the storage
        behind it.
        """
        if not isinstance(target, ast.Identifier):
            return
        symbol_id = self._c.node_symbols.get(id(target))
        if symbol_id is None:
            return
        symbol = self._c.symbol_table.get_symbol(symbol_id)
        if symbol is None:
            return
        if not getattr(symbol, "is_mutable", True):
            self._c._error(
                TypeErrorKind.INVALID_OPERATION,
                f"cannot reassign immutable binding '{symbol.name}'; "
                "use `var` to declare a mutable binding",
                node=target,
            )

    def _check_return(self, ret: ast.ReturnStmt) -> None:
        """Type check a return statement."""
        if ret.value:
            value_type = self._c._infer_with_expected(
                ret.value, self._c._current_function_return
            )
            if self._c._current_function_return:
                self._c._check_assignable(
                    value_type,
                    self._c._current_function_return,
                    "return value",
                    node=ret,
                )
        elif self._c._current_function_return and self._c._current_function_return != self._c.type_table.void_type:
            self._c._error(
                TypeErrorKind.TYPE_MISMATCH,
                f"Function expects return value of type {self._c.type_table.format_type(self._c._current_function_return)}",
                node=ret,
            )

    def _check_if_stmt(self, if_stmt: ast.IfStmt) -> None:
        """Type check an if statement."""
        if isinstance(if_stmt.condition, tuple):
            # if let pattern = expr
            pattern, expr = if_stmt.condition
            expr_type = self._c._infer_expr(expr)
            # For if-let, the expression should be optional and we unwrap it
            inner_type = self._c.type_table.get_optional_inner(expr_type)
            if inner_type:
                self._c._bind_pattern_type(pattern, inner_type)
            else:
                # Not optional - pattern gets the full type
                self._c._bind_pattern_type(pattern, expr_type)
        else:
            # Regular condition
            if if_stmt.condition:
                cond_type = self._c._infer_expr(if_stmt.condition)
                self._c._check_boolean(cond_type, "if condition")

        if if_stmt.then_block:
            self._check_block(if_stmt.then_block)

        if if_stmt.else_block:
            if isinstance(if_stmt.else_block, ast.IfStmt):
                self._check_if_stmt(if_stmt.else_block)
            else:
                self._check_block(if_stmt.else_block)

    def _check_while_stmt(self, while_stmt: ast.WhileStmt) -> None:
        """Type check a while loop."""
        if while_stmt.condition:
            cond_type = self._c._infer_expr(while_stmt.condition)
            self._c._check_boolean(cond_type, "while condition")

        if while_stmt.body:
            self._check_block(while_stmt.body)

    def _check_for_stmt(self, for_stmt: ast.ForStmt) -> None:
        """Type check a for-in loop."""
        if for_stmt.iterable:
            iter_type = self._c._infer_expr(for_stmt.iterable)
            # Get element type from iterable
            elem_type = self._c._get_iterable_element(iter_type)

            if for_stmt.pattern:
                self._c._bind_pattern_type(for_stmt.pattern, elem_type)

        if for_stmt.body:
            self._check_block(for_stmt.body)

    def _check_switch_stmt(self, switch_stmt: ast.SwitchStmt) -> None:
        """Type check a switch statement."""
        if switch_stmt.value:
            value_type = self._c._infer_expr(switch_stmt.value)

            for case in switch_stmt.cases:
                self._check_switch_case(case, value_type)

            # Check exhaustiveness
            checker = ExhaustivenessChecker(
                self._c.type_table, self._c.symbol_table, self._c._error
            )
            checker.check_switch(switch_stmt, value_type)

    def _check_switch_case(self, case: ast.SwitchCase, value_type: TypeId) -> None:
        """Type check a switch case."""
        for pattern, guard in case.patterns:
            self._c._bind_pattern_type(pattern, value_type)
            if guard:
                guard_type = self._c._infer_expr(guard)
                self._c._check_boolean(guard_type, "case guard")

        for stmt in case.body:
            self._check_stmt(stmt)

    def _check_guard_stmt(self, guard: ast.GuardStmt) -> None:
        """Type check a guard statement."""
        if guard.condition:
            cond_type = self._c._infer_expr(guard.condition)
            self._c._check_boolean(cond_type, "guard condition")

        if guard.else_block:
            self._check_block(guard.else_block)
            # A guard's else block must leave the enclosing scope (return,
            # break or continue) — execution may never fall through past a
            # failed guard. Reject otherwise so the user gets a clear error
            # instead of malformed downstream IR.
            if not self._block_diverges(guard.else_block):
                self._c._error(
                    TypeErrorKind.INVALID_OPERATION,
                    "'guard' else block must exit the enclosing scope "
                    "(e.g. 'return', 'break' or 'continue')",
                )

    def _block_diverges(self, block: Optional[ast.Block]) -> bool:
        """Conservatively determine whether a block always exits its scope
        (its last statement diverges via return/break/continue or an
        if/switch all of whose arms diverge)."""
        if block is None or not block.statements:
            return False
        return self._stmt_diverges(block.statements[-1])

    def _stmt_diverges(self, stmt: ast.Stmt) -> bool:
        if isinstance(stmt, (ast.ReturnStmt, ast.BreakStmt, ast.ContinueStmt)):
            return True
        if isinstance(stmt, ast.Block):
            return self._block_diverges(stmt)
        if isinstance(stmt, ast.IfStmt):
            if stmt.else_block is None:
                return False
            then_div = self._block_diverges(stmt.then_block)
            if isinstance(stmt.else_block, ast.IfStmt):
                else_div = self._stmt_diverges(stmt.else_block)
            else:
                else_div = self._block_diverges(stmt.else_block)
            return then_div and else_div
        if isinstance(stmt, ast.SwitchStmt):
            cases = stmt.cases or []
            if not cases:
                return False
            has_default = any(getattr(c, "is_default", False) for c in cases)
            if not has_default:
                return False
            for case in cases:
                body = getattr(case, "body", None)
                if not body or not self._stmt_diverges(body[-1]):
                    return False
            return True
        return False
