"""
Single-file analysis pass for the Rolang language server.

Runs parse → resolve → typecheck and returns a unified AnalysisResult
that the LSP server uses for hover, definition, and diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .. import ast as ast_module
from ..parser import parse
from ..resolver import resolve
from ..checker import typecheck
from ..diagnostics import DiagnosticCollector
from ..symbols import ResolutionResult
from ..checker_core import TypeCheckResult


@dataclass
class AnalysisResult:
    """Cached analysis state for one open document."""

    source: str
    path: Path
    program: Optional[ast_module.Program]
    resolution: Optional[ResolutionResult]
    typecheck: Optional[TypeCheckResult]
    diagnostics: DiagnosticCollector


def analyze_source(path: Path, source: str) -> AnalysisResult:
    """
    Run the front-end pipeline (parse + resolve + typecheck) on *source*.

    Never raises — all errors are collected into ``result.diagnostics``.
    Partial results are stored even when earlier passes fail so that the
    server can provide best-effort information while the user is typing.
    """
    source_files: dict[Path, str] = {path: source}
    diags = DiagnosticCollector(source_files)

    # ── Parse ─────────────────────────────────────────────────────────────────
    program: Optional[ast_module.Program] = None
    try:
        program = parse(source)
    except Exception as exc:
        line = int(getattr(exc, "line", 1) or 1)
        col = int(getattr(exc, "column", 1) or 1)
        diags.add_parse_error(str(exc), path, line, col)
        return AnalysisResult(
            source=source,
            path=path,
            program=None,
            resolution=None,
            typecheck=None,
            diagnostics=diags,
        )

    # ── Name resolution ────────────────────────────────────────────────────────
    resolution: Optional[ResolutionResult] = None
    try:
        resolution = resolve(program)
        for err in resolution.errors:
            diags.add_resolution_error(err, path)
    except Exception as exc:
        diags.add_error(f"Internal resolver error: {exc}", file_path=path)
        return AnalysisResult(
            source=source,
            path=path,
            program=program,
            resolution=None,
            typecheck=None,
            diagnostics=diags,
        )

    # ── Type checking ──────────────────────────────────────────────────────────
    # Run even if there are resolution errors so we surface as many issues as
    # possible in a single edit cycle.
    tc_result: Optional[TypeCheckResult] = None
    try:
        tc_result = typecheck(program, resolution)
        for err in tc_result.errors:
            diags.add_type_error(err, path)
    except Exception as exc:
        diags.add_error(f"Internal type-checker error: {exc}", file_path=path)

    return AnalysisResult(
        source=source,
        path=path,
        program=program,
        resolution=resolution,
        typecheck=tc_result,
        diagnostics=diags,
    )
