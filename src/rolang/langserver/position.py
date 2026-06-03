"""
Position utilities for the Rolang language server.

This module is intentionally free of pygls / lsprotocol imports so it can
be used without the optional langserver extra (e.g. in tests).

Provides:
  * find_node_at()   — locate the innermost AST node at a (line, col) position
"""

from __future__ import annotations

import dataclasses
from typing import Optional

from .. import ast as ast_module
from ..ast import Span


# ── Span helpers ───────────────────────────────────────────────────────────────


def _span_size(span: Span) -> int:
    """Return a measure of span width; smaller → more specific → preferred."""
    el = span.end_line if span.end_line and span.end_line >= span.line else span.line
    ec = span.end_column if span.end_column and span.end_column > 0 else span.column + 1
    return (el - span.line) * 100_000 + (ec - span.column)


def _span_contains(span: Optional[Span], line: int, col: int) -> bool:
    """Return True if 1-based (line, col) falls within *span*.

    Handles unset end_line / end_column (stored as 0) gracefully.
    """
    if span is None:
        return False

    sl, sc = span.line, span.column
    el = span.end_line if span.end_line and span.end_line >= sl else sl
    ec = span.end_column if span.end_column and span.end_column > 0 else sc + 1

    start = sl * 100_000 + sc
    end = el * 100_000 + ec
    pos = line * 100_000 + col
    return start <= pos < end


# ── AST traversal ──────────────────────────────────────────────────────────────


def find_node_at(
    root: ast_module.Node,
    line: int,
    col: int,
) -> Optional[ast_module.Node]:
    """
    Walk the AST rooted at *root* and return the **innermost** (most specific)
    Node whose span contains the 1-based (line, col) position.

    Returns None if no node matches.
    """
    best: list[Optional[ast_module.Node]] = [None]
    best_size: list[int] = [2 ** 31]

    def _visit(node: object) -> None:
        if isinstance(node, ast_module.Node):
            span = getattr(node, "span", None)
            if _span_contains(span, line, col):
                size = _span_size(span)  # type: ignore[arg-type]
                if size < best_size[0]:
                    best[0] = node
                    best_size[0] = size
            # Recurse into all dataclass fields
            if dataclasses.is_dataclass(node):
                for f in dataclasses.fields(node):  # type: ignore[arg-type]
                    if f.name == "span":
                        continue
                    _visit_val(getattr(node, f.name))

    def _visit_val(val: object) -> None:
        if isinstance(val, ast_module.Node):
            _visit(val)
        elif isinstance(val, (list, tuple)):
            for item in val:
                _visit_val(item)
        # str / int / bool / None — nothing to recurse into

    _visit(root)
    return best[0]
