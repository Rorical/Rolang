"""Shared compilation context passed across phases."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, List

from .symbols import SymbolId, SymbolTable
from .types import TypeTable, TypeId
from .diagnostics import DiagnosticCollector


@dataclass
class CompilerContext:
    """State shared across compilation passes.

    Avoids ad-hoc parameter passing and hidden attributes on result objects.
    """

    symbol_table: SymbolTable
    type_table: TypeTable
    diagnostics: DiagnosticCollector
    node_symbols: Dict[int, SymbolId] = field(default_factory=dict)
    imported_symbols: Dict[str, SymbolId] = field(default_factory=dict)

    def has_errors(self) -> bool:
        return self.diagnostics.has_errors()


class PassRunner:
    """Simple pass sequencer that records pass names for verbose/tracing output."""

    def __init__(
        self,
        context: CompilerContext,
        verbose: bool = False,
    ) -> None:
        self.context = context
        self.verbose = verbose
        self._passes: List[str] = []

    def run(self, name: str, pass_fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Run a single pass, logging if verbose."""
        if self.verbose:
            print(f"{name}...")
        self._passes.append(name)
        return pass_fn(*args, **kwargs)

    @property
    def executed_passes(self) -> List[str]:
        return list(self._passes)
