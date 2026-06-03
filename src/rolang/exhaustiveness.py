"""Switch exhaustiveness checking for the Rolang compiler.

Extracted from TypeChecker to isolate pattern-match exhaustiveness logic.
"""

from typing import Optional, Set

from . import ast
from .types import TypeId, TypeKind, TypeTable, PrimitiveTypeData, PrimitiveType, EnumTypeData, OptionalTypeData
from .symbols import SymbolTable


class ExhaustivenessChecker:
    """Check that switch statements cover all possible values."""

    def __init__(
        self,
        type_table: TypeTable,
        symbol_table: SymbolTable,
        report_error,
    ) -> None:
        self.type_table = type_table
        self.symbol_table = symbol_table
        self._report_error = report_error

    def check_switch(
        self,
        switch_stmt: ast.SwitchStmt,
        value_type: TypeId,
    ) -> None:
        """Check that a switch statement covers all possible values."""
        # If there's a default case, it's exhaustive
        for case in switch_stmt.cases:
            if case.is_default:
                return

        info = self.type_table.get_type(value_type)
        if info is None:
            return

        if info.kind == TypeKind.ENUM:
            self._check_enum_exhaustiveness(switch_stmt, value_type)
        elif info.kind == TypeKind.OPTIONAL:
            self._check_optional_exhaustiveness(switch_stmt)
        elif info.kind == TypeKind.PRIMITIVE:
            data = info.data
            if isinstance(data, PrimitiveTypeData) and data.primitive == PrimitiveType.BOOL:
                self._check_bool_exhaustiveness(switch_stmt)

    def _check_enum_exhaustiveness(
        self,
        switch_stmt: ast.SwitchStmt,
        enum_type: TypeId,
    ) -> None:
        """Check that all enum cases are covered."""
        info = self.type_table.get_type(enum_type)
        if info is None or info.kind != TypeKind.ENUM:
            return
        data = info.data
        if not isinstance(data, EnumTypeData):
            return

        symbol = self.symbol_table.get_symbol(data.symbol_id)
        if symbol is None or not isinstance(symbol.decl_node, ast.EnumDecl):
            return

        # Collect all enum case names
        all_cases: set[str] = set()
        for member in symbol.decl_node.members:
            if isinstance(member, ast.EnumCaseDecl):
                for case_def in member.cases:
                    all_cases.add(case_def.name)

        # Collect matched cases from patterns. A pattern only fully covers
        # its domain when it has no ``where`` guard — a guarded arm may fall
        # through at runtime, so we cannot treat it as exhaustive.
        matched_cases: set[str] = set()
        for case in switch_stmt.cases:
            for pattern, guard in case.patterns:
                case_set = self._collect_matched_cases(pattern)
                if case_set is None:
                    # Catch-all pattern, but only counts as catch-all if
                    # there is no guard.
                    if guard is None:
                        return
                    continue
                if guard is not None:
                    # Guard may be false at runtime — does not contribute
                    # to static coverage.
                    continue
                matched_cases.update(case_set)

        # Check for missing cases
        missing = all_cases - matched_cases
        if missing:
            from .checker_core import TypeErrorKind
            missing_list = sorted(missing)
            self._report_error(
                TypeErrorKind.NON_EXHAUSTIVE_MATCH,
                f"Switch must be exhaustive, missing cases: {', '.join(missing_list)}"
            )

    def _check_optional_exhaustiveness(self, switch_stmt: ast.SwitchStmt) -> None:
        """Check that both Some and None cases are covered for Optional switches."""
        has_some = False
        has_none = False

        for case in switch_stmt.cases:
            for pattern, guard in case.patterns:
                kind = self._classify_optional_pattern(pattern)
                if kind is None:
                    # Catch-all only if not guarded.
                    if guard is None:
                        return
                    continue
                if guard is not None:
                    # Guarded — does not contribute to static coverage.
                    continue
                if "some" in kind:
                    has_some = True
                if "none" in kind:
                    has_none = True

        missing: list[str] = []
        if not has_some:
            missing.append("Some(...)")
        if not has_none:
            missing.append("nil")

        if missing:
            from .checker_core import TypeErrorKind
            self._report_error(
                TypeErrorKind.NON_EXHAUSTIVE_MATCH,
                f"Switch on Optional must be exhaustive, missing: {', '.join(missing)}"
            )

    def _is_irrefutable(self, pattern: ast.Pattern) -> bool:
        """Return True for patterns that always match — wildcards, simple
        identifiers, or typed wrappers around an irrefutable inner.
        """
        if isinstance(pattern, (ast.WildcardPattern, ast.IdentifierPattern)):
            return True
        if isinstance(pattern, ast.TypedPattern):
            return pattern.pattern is None or self._is_irrefutable(pattern.pattern)
        return False

    def _classify_optional_pattern(self, pattern: ast.Pattern) -> Optional[set]:
        """Classify a pattern against an Optional scrutinee.
        Returns None for catch-all, or a set with strings 'some'/'none'."""
        if isinstance(pattern, (ast.WildcardPattern, ast.IdentifierPattern)):
            return None  # Catch-all
        if isinstance(pattern, ast.EnumCasePattern):
            if pattern.case_name == "Some":
                # `Some(inner)` only fully covers the `Some` arm when ``inner``
                # itself is irrefutable. ``Some(.Ok(v))`` against
                # ``Optional<Result<T, E>>`` does NOT exhaust ``Some``.
                if all(self._is_irrefutable(p) for p in pattern.payload):
                    return {"some"}
                return set()  # partial — does not contribute
            if pattern.case_name in ("None", "nil"):
                return {"none"}
            return set()
        if isinstance(pattern, ast.LiteralPattern):
            if pattern.value and pattern.value.kind == "nil":
                return {"none"}
            return set()
        if isinstance(pattern, ast.OrPattern):
            result: set = set()
            for p in pattern.patterns:
                sub = self._classify_optional_pattern(p)
                if sub is None:
                    return None
                result.update(sub)
            return result
        if isinstance(pattern, ast.TypedPattern):
            if pattern.pattern:
                return self._classify_optional_pattern(pattern.pattern)
            return set()
        return set()

    def _check_bool_exhaustiveness(self, switch_stmt: ast.SwitchStmt) -> None:
        """Check that both true and false are covered for Bool switches."""
        matched_values: set[bool] = set()

        for case in switch_stmt.cases:
            for pattern, guard in case.patterns:
                case_set = self._collect_literal_values(pattern)
                if case_set is None:
                    # Catch-all only counts when unguarded.
                    if guard is None:
                        return
                    continue
                if guard is not None:
                    continue
                matched_values.update(case_set)

        missing: list[str] = []
        if True not in matched_values:
            missing.append("true")
        if False not in matched_values:
            missing.append("false")

        if missing:
            from .checker_core import TypeErrorKind
            self._report_error(
                TypeErrorKind.NON_EXHAUSTIVE_MATCH,
                f"Switch must be exhaustive, missing cases: {', '.join(missing)}"
            )

    def _collect_matched_cases(self, pattern: ast.Pattern) -> Optional[set[str]]:
        """Collect enum case names matched by a pattern.
        Returns None for catch-all patterns."""
        if isinstance(pattern, (ast.WildcardPattern, ast.IdentifierPattern)):
            return None  # Catch-all
        if isinstance(pattern, ast.EnumCasePattern):
            # The case is only fully covered when every payload sub-pattern is
            # irrefutable. A refutable payload (e.g. a nested `.case` or a
            # literal) means some values of this case are NOT matched, so it
            # must not count toward exhaustiveness.
            if all(self._is_irrefutable(p) for p in (pattern.payload or [])):
                return {pattern.case_name}
            return set()
        if isinstance(pattern, ast.OrPattern):
            result: set[str] = set()
            for p in pattern.patterns:
                sub = self._collect_matched_cases(p)
                if sub is None:
                    return None
                result.update(sub)
            return result
        if isinstance(pattern, ast.TypedPattern):
            if pattern.pattern:
                return self._collect_matched_cases(pattern.pattern)
            return set()
        if isinstance(pattern, ast.TuplePattern):
            return set()
        if isinstance(pattern, ast.LiteralPattern):
            return set()
        return set()

    def _collect_literal_values(self, pattern: ast.Pattern) -> Optional[set[bool]]:
        """Collect boolean literal values matched by a pattern.
        Returns None for catch-all patterns."""
        if isinstance(pattern, (ast.WildcardPattern, ast.IdentifierPattern)):
            return None  # Catch-all
        if isinstance(pattern, ast.LiteralPattern):
            if pattern.value and pattern.value.kind == "bool":
                return {pattern.value.value}
            return set()
        if isinstance(pattern, ast.OrPattern):
            result: set[bool] = set()
            for p in pattern.patterns:
                sub = self._collect_literal_values(p)
                if sub is None:
                    return None
                result.update(sub)
            return result
        if isinstance(pattern, ast.TypedPattern):
            if pattern.pattern:
                return self._collect_literal_values(pattern.pattern)
            return set()
        return set()
