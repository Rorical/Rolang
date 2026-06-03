"""
Capture Analysis for Closures.

Analyzes lambda expressions to determine which variables from outer scopes
are captured (free variables). This information is used to generate closure
objects that carry the captured environment.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Set, List, Dict

from .types import TypeId, TypeTable
from .symbols import SymbolId, SymbolTable, SymbolKind
from .hir import (
    HirExpr, HirStmt, HirBlock, HirLambda, HirVar,
    HirBinaryOp, HirUnaryOp, HirTernary, HirCall, HirMethodCall,
    HirFieldAccess, HirSubscript, HirTuple, HirArray, HirDict,
    HirStructInit,
    HirEnumConstruct, HirCast, HirTypeCheck, HirTryExpr, HirOptionalSome,
    HirOptionalNone, HirOptionalMatch, HirLiteral,
    HirVarDecl, HirAssign, HirExprStmt, HirReturn,
    HirIf, HirIfLet, HirGuard, HirWhile, HirFor, HirSwitch, HirDefer,
)


@dataclass
class CaptureInfo:
    """Information about a captured variable."""
    symbol_id: SymbolId
    name: str
    type_id: TypeId
    is_mutable: bool

    def __hash__(self) -> int:
        return hash(self.symbol_id)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, CaptureInfo):
            return self.symbol_id == other.symbol_id
        return False


class CaptureAnalyzer:
    """
    Analyzes lambda expressions to find captured variables.

    A variable is captured if:
    1. It is referenced inside the lambda body
    2. It is defined in an enclosing scope (not the lambda itself)
    3. It is not a global or function (we don't capture functions)
    """

    def __init__(
        self,
        type_table: TypeTable,
        symbol_table: SymbolTable,
    ) -> None:
        self.type_table = type_table
        self.symbol_table = symbol_table

    def analyze_lambda(
        self,
        lam: HirLambda,
        outer_scope_symbols: Set[SymbolId],
        symbol_types: Dict[SymbolId, TypeId],
    ) -> List[CaptureInfo]:
        """
        Analyze a lambda to find captured variables.

        Args:
            lam: The lambda expression to analyze
            outer_scope_symbols: Set of symbol IDs visible in outer scope
            symbol_types: Map from symbol ID to type ID

        Returns:
            List of CaptureInfo for each captured variable
        """
        # Collect parameter symbols (these are not captured, they're local)
        param_symbols: Set[SymbolId] = set()
        for param in lam.params:
            param_symbols.add(param.symbol_id)

        # Find all variable references in the lambda body
        referenced_symbols = self._collect_references(lam.body)

        # Find all locally declared variables in the body
        local_symbols = self._collect_local_declarations(lam.body)

        # A captured variable is one that:
        # - Is referenced in the body
        # - Is in the outer scope (not a parameter or local)
        # - Is a variable (not a function or type)
        captures: List[CaptureInfo] = []
        seen: Set[SymbolId] = set()

        for sym_id in referenced_symbols:
            if sym_id in seen:
                continue
            if sym_id in param_symbols:
                continue
            if sym_id in local_symbols:
                continue
            if sym_id not in outer_scope_symbols:
                continue

            # Check if it's a capturable symbol
            symbol = self.symbol_table.get_symbol(sym_id)
            if symbol is None:
                continue

            # Only capture variables and parameters, not functions/types
            if symbol.kind not in (SymbolKind.VARIABLE, SymbolKind.PARAMETER):
                continue

            type_id = symbol_types.get(sym_id, self.type_table.error_type)

            captures.append(CaptureInfo(
                symbol_id=sym_id,
                name=symbol.name,
                type_id=type_id,
                is_mutable=symbol.is_mutable,
            ))
            seen.add(sym_id)

        return captures

    def _collect_references(self, block: HirBlock) -> Set[SymbolId]:
        """Collect all variable references in a block."""
        refs: Set[SymbolId] = set()

        for stmt in block.statements:
            refs.update(self._collect_refs_stmt(stmt))

        return refs

    def _collect_refs_stmt(self, stmt: HirStmt) -> Set[SymbolId]:
        """Collect variable references in a statement."""
        refs: Set[SymbolId] = set()

        if isinstance(stmt, HirBlock):
            refs.update(self._collect_references(stmt))

        elif isinstance(stmt, HirVarDecl):
            if stmt.initializer:
                refs.update(self._collect_refs_expr(stmt.initializer))

        elif isinstance(stmt, HirAssign):
            refs.update(self._collect_refs_expr(stmt.target))
            refs.update(self._collect_refs_expr(stmt.value))

        elif isinstance(stmt, HirExprStmt):
            refs.update(self._collect_refs_expr(stmt.expr))

        elif isinstance(stmt, HirReturn):
            if stmt.value:
                refs.update(self._collect_refs_expr(stmt.value))

        elif isinstance(stmt, HirIf):
            refs.update(self._collect_refs_expr(stmt.condition))
            refs.update(self._collect_references(stmt.then_block))
            if stmt.else_block:
                if isinstance(stmt.else_block, HirBlock):
                    refs.update(self._collect_references(stmt.else_block))
                elif isinstance(stmt.else_block, HirIf):
                    refs.update(self._collect_refs_stmt(stmt.else_block))

        elif isinstance(stmt, HirIfLet):
            refs.update(self._collect_refs_expr(stmt.scrutinee))
            refs.update(self._collect_references(stmt.then_block))
            if stmt.else_block:
                if isinstance(stmt.else_block, HirBlock):
                    refs.update(self._collect_references(stmt.else_block))
                elif isinstance(stmt.else_block, HirIf):
                    refs.update(self._collect_refs_stmt(stmt.else_block))

        elif isinstance(stmt, HirGuard):
            refs.update(self._collect_refs_expr(stmt.condition))
            refs.update(self._collect_references(stmt.else_block))

        elif isinstance(stmt, HirWhile):
            refs.update(self._collect_refs_expr(stmt.condition))
            refs.update(self._collect_references(stmt.body))

        elif isinstance(stmt, HirFor):
            refs.update(self._collect_refs_expr(stmt.iterable))
            refs.update(self._collect_references(stmt.body))

        elif isinstance(stmt, HirSwitch):
            refs.update(self._collect_refs_expr(stmt.scrutinee))
            for case in stmt.cases:
                for pattern, guard in case.patterns:
                    if guard:
                        refs.update(self._collect_refs_expr(guard))
                refs.update(self._collect_references(case.body))

        elif isinstance(stmt, HirDefer):
            refs.update(self._collect_references(stmt.body))

        return refs

    def _collect_refs_expr(self, expr: HirExpr) -> Set[SymbolId]:
        """Collect variable references in an expression."""
        refs: Set[SymbolId] = set()

        if isinstance(expr, HirVar):
            refs.add(expr.symbol_id)

        elif isinstance(expr, HirBinaryOp):
            refs.update(self._collect_refs_expr(expr.left))
            refs.update(self._collect_refs_expr(expr.right))

        elif isinstance(expr, HirUnaryOp):
            refs.update(self._collect_refs_expr(expr.operand))

        elif isinstance(expr, HirTernary):
            refs.update(self._collect_refs_expr(expr.condition))
            refs.update(self._collect_refs_expr(expr.then_expr))
            refs.update(self._collect_refs_expr(expr.else_expr))

        elif isinstance(expr, HirCall):
            refs.update(self._collect_refs_expr(expr.callee))
            for _, arg in expr.arguments:
                refs.update(self._collect_refs_expr(arg))

        elif isinstance(expr, HirMethodCall):
            if not expr.is_static:
                refs.update(self._collect_refs_expr(expr.receiver))
            for _, arg in expr.arguments:
                refs.update(self._collect_refs_expr(arg))

        elif isinstance(expr, HirFieldAccess):
            refs.update(self._collect_refs_expr(expr.object))

        elif isinstance(expr, HirSubscript):
            refs.update(self._collect_refs_expr(expr.object))
            for idx in expr.indices:
                refs.update(self._collect_refs_expr(idx))

        elif isinstance(expr, HirTuple):
            for _, elem in expr.elements:
                refs.update(self._collect_refs_expr(elem))

        elif isinstance(expr, HirArray):
            for elem in expr.elements:
                refs.update(self._collect_refs_expr(elem))

        elif isinstance(expr, HirDict):
            for key, val in expr.entries:
                refs.update(self._collect_refs_expr(key))
                refs.update(self._collect_refs_expr(val))

        elif isinstance(expr, HirLambda):
            # For nested lambdas, we still collect references
            # (they may capture from our scope)
            refs.update(self._collect_references(expr.body))

        elif isinstance(expr, HirStructInit):
            for _, arg in expr.arguments:
                refs.update(self._collect_refs_expr(arg))

        elif isinstance(expr, HirEnumConstruct):
            for _, payload in expr.payload:
                refs.update(self._collect_refs_expr(payload))

        elif isinstance(expr, HirCast):
            refs.update(self._collect_refs_expr(expr.expr))

        elif isinstance(expr, HirTryExpr):
            refs.update(self._collect_refs_expr(expr.expr))

        elif isinstance(expr, HirTypeCheck):
            refs.update(self._collect_refs_expr(expr.expr))

        elif isinstance(expr, HirOptionalSome):
            refs.update(self._collect_refs_expr(expr.value))

        elif isinstance(expr, HirOptionalMatch):
            refs.update(self._collect_refs_expr(expr.scrutinee))
            refs.update(self._collect_refs_expr(expr.some_expr))
            refs.update(self._collect_refs_expr(expr.none_expr))

        # HirLiteral, HirOptionalNone have no variable references

        return refs

    def _collect_local_declarations(self, block: HirBlock) -> Set[SymbolId]:
        """Collect all locally declared variable symbols in a block."""
        locals: Set[SymbolId] = set()

        for stmt in block.statements:
            locals.update(self._collect_locals_stmt(stmt))

        return locals

    def _collect_locals_stmt(self, stmt: HirStmt) -> Set[SymbolId]:
        """Collect local declarations in a statement."""
        locals: Set[SymbolId] = set()

        if isinstance(stmt, HirBlock):
            locals.update(self._collect_local_declarations(stmt))

        elif isinstance(stmt, HirVarDecl):
            locals.add(stmt.symbol_id)

        elif isinstance(stmt, HirIf):
            locals.update(self._collect_local_declarations(stmt.then_block))
            if stmt.else_block:
                if isinstance(stmt.else_block, HirBlock):
                    locals.update(self._collect_local_declarations(stmt.else_block))

        elif isinstance(stmt, HirIfLet):
            # The bound variable in if-let is local to the then block
            locals.update(self._collect_local_declarations(stmt.then_block))
            if stmt.else_block and isinstance(stmt.else_block, HirBlock):
                locals.update(self._collect_local_declarations(stmt.else_block))

        elif isinstance(stmt, HirWhile):
            locals.update(self._collect_local_declarations(stmt.body))

        elif isinstance(stmt, HirFor):
            # The loop variable is local to the loop body
            locals.update(self._collect_local_declarations(stmt.body))

        elif isinstance(stmt, HirSwitch):
            for case in stmt.cases:
                locals.update(self._collect_local_declarations(case.body))

        elif isinstance(stmt, HirDefer):
            locals.update(self._collect_local_declarations(stmt.body))

        return locals


def analyze_captures(
    lam: HirLambda,
    outer_scope_symbols: Set[SymbolId],
    symbol_types: Dict[SymbolId, TypeId],
    type_table: TypeTable,
    symbol_table: SymbolTable,
) -> List[CaptureInfo]:
    """
    Analyze a lambda expression to find captured variables.

    Args:
        lam: The lambda expression
        outer_scope_symbols: Symbols visible in enclosing scope
        symbol_types: Map from symbol ID to type
        type_table: The type table
        symbol_table: The symbol table

    Returns:
        List of capture information for each captured variable
    """
    analyzer = CaptureAnalyzer(type_table, symbol_table)
    return analyzer.analyze_lambda(lam, outer_scope_symbols, symbol_types)
