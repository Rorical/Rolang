"""
Rolang Language Server (LSP).

Features implemented:
  * Diagnostics        textDocument/didOpen, textDocument/didChange
  * Hover              textDocument/hover      — inferred type at cursor
  * Go-to-definition   textDocument/definition — jump to symbol declaration
  * Document symbols   textDocument/documentSymbol — file outline

Run via:
    rolang-langserver [--stdio]   (default)
    rolang-langserver --tcp PORT  (useful for debugging)
"""

from __future__ import annotations

import argparse
import threading
from pathlib import Path
from urllib.parse import unquote, urlparse

from lsprotocol import types as lsp
from pygls.server import LanguageServer

from .. import ast as ast_module
from ..diagnostics import Severity, SourceLocation
from .analysis import AnalysisResult, analyze_source
from .position import find_node_at

# ── LSP conversion helpers ────────────────────────────────────────────────────


def _span_to_range(loc: SourceLocation) -> lsp.Range:
    """Convert a SourceLocation (1-based) to an LSP Range (0-based)."""
    sl = max(0, loc.line - 1)
    sc = max(0, loc.column - 1)
    el = max(0, (loc.end_line or loc.line) - 1)
    ec = max(0, (loc.end_column or loc.column) - 1)
    # Guarantee at least a 1-character range so VS Code shows the squiggle
    if sl == el and sc == ec:
        ec += 1
    return lsp.Range(
        start=lsp.Position(line=sl, character=sc),
        end=lsp.Position(line=el, character=ec),
    )


def _severity_to_lsp(severity: Severity) -> lsp.DiagnosticSeverity:
    """Convert a Rolang Severity to an LSP DiagnosticSeverity."""
    if severity == Severity.ERROR:
        return lsp.DiagnosticSeverity.Error
    if severity == Severity.WARNING:
        return lsp.DiagnosticSeverity.Warning
    if severity == Severity.NOTE:
        return lsp.DiagnosticSeverity.Information
    return lsp.DiagnosticSeverity.Hint


# ── Server instance ────────────────────────────────────────────────────────────

server = LanguageServer("rolang-langserver", "v0.1.0")

# Cache: uri → most-recent AnalysisResult
_results: dict[str, AnalysisResult] = {}
_lock = threading.Lock()


# ── URI helpers ────────────────────────────────────────────────────────────────


def _uri_to_path(uri: str) -> Path:
    parsed = urlparse(uri)
    return Path(unquote(parsed.path))


# ── Analysis + diagnostics ─────────────────────────────────────────────────────


def _analyze_and_publish(ls: LanguageServer, uri: str, text: str) -> None:
    path = _uri_to_path(uri)
    result = analyze_source(path, text)
    with _lock:
        _results[uri] = result

    lsp_diags: list[lsp.Diagnostic] = []
    for diag in result.diagnostics.diagnostics:
        loc = diag.location
        if loc is None:
            continue
        # Only report diagnostics for the file being edited (skip imported files)
        if loc.file_path != path:
            continue
        lsp_diags.append(
            lsp.Diagnostic(
                range=_span_to_range(loc),
                message=diag.message,
                severity=_severity_to_lsp(diag.severity),
                code=diag.code,
                source="rolangc",
            )
        )
    ls.publish_diagnostics(uri, lsp_diags)


# ── Text document synchronization ─────────────────────────────────────────────


@server.feature(lsp.TEXT_DOCUMENT_DID_OPEN)
def did_open(ls: LanguageServer, params: lsp.DidOpenTextDocumentParams) -> None:
    _analyze_and_publish(ls, params.text_document.uri, params.text_document.text)


@server.feature(lsp.TEXT_DOCUMENT_DID_CHANGE)
def did_change(ls: LanguageServer, params: lsp.DidChangeTextDocumentParams) -> None:
    # Full-document sync: use the last change's text
    text = params.content_changes[-1].text
    _analyze_and_publish(ls, params.text_document.uri, text)


@server.feature(lsp.TEXT_DOCUMENT_DID_SAVE)
def did_save(ls: LanguageServer, params: lsp.DidSaveTextDocumentParams) -> None:
    # Re-analyze on save so diagnostics stay fresh even if the editor
    # doesn't send didChange for every keystroke.
    with _lock:
        cached = _results.get(params.text_document.uri)
    if cached is not None:
        _analyze_and_publish(ls, params.text_document.uri, cached.source)


@server.feature(lsp.TEXT_DOCUMENT_DID_CLOSE)
def did_close(ls: LanguageServer, params: lsp.DidCloseTextDocumentParams) -> None:
    with _lock:
        _results.pop(params.text_document.uri, None)
    # Clear diagnostics for the closed file
    ls.publish_diagnostics(params.text_document.uri, [])


# ── Hover ──────────────────────────────────────────────────────────────────────


@server.feature(lsp.TEXT_DOCUMENT_HOVER)
def hover(ls: LanguageServer, params: lsp.HoverParams) -> lsp.Hover | None:
    """Return the inferred Rolang type of the expression under the cursor."""
    uri = params.text_document.uri
    pos = params.position

    with _lock:
        result = _results.get(uri)
    if result is None or result.program is None:
        return None

    # LSP positions are 0-based; AST spans are 1-based
    line = pos.line + 1
    col = pos.character + 1

    node = find_node_at(result.program, line, col)
    if node is None:
        return None

    # ── Type of an expression ──────────────────────────────────────────────────
    if result.typecheck is not None:
        type_id = result.typecheck.expr_types.get(id(node))
        if type_id is not None:
            type_str = result.typecheck.type_table.format_type(type_id)
            return lsp.Hover(
                contents=lsp.MarkupContent(
                    kind=lsp.MarkupKind.Markdown,
                    value=f"```rolang\n{type_str}\n```",
                )
            )

    # ── Declaration name — show the symbol kind and name ──────────────────────
    if result.resolution is not None and isinstance(
        node,
        (
            ast_module.FuncDecl,
            ast_module.StructDecl,
            ast_module.EnumDecl,
            ast_module.ProtocolDecl,
        ),
    ):
        name = getattr(node, "name", None)
        kind = type(node).__name__.replace("Decl", "").lower()
        if name:
            return lsp.Hover(
                contents=lsp.MarkupContent(
                    kind=lsp.MarkupKind.Markdown,
                    value=f"```rolang\n{kind} {name}\n```",
                )
            )

    return None


# ── Go-to-definition ───────────────────────────────────────────────────────────


@server.feature(lsp.TEXT_DOCUMENT_DEFINITION)
def definition(
    ls: LanguageServer, params: lsp.DefinitionParams
) -> lsp.Location | None:
    """Jump to the declaration of the symbol under the cursor."""
    uri = params.text_document.uri
    pos = params.position

    with _lock:
        result = _results.get(uri)
    if result is None or result.program is None or result.resolution is None:
        return None

    line = pos.line + 1
    col = pos.character + 1

    node = find_node_at(result.program, line, col)
    if not isinstance(node, ast_module.Identifier):
        return None

    sym_id = result.resolution.node_symbols.get(id(node))
    if sym_id is None:
        return None

    sym = result.resolution.symbol_table.get_symbol(sym_id)
    if sym is None or sym.span is None:
        return None

    loc = SourceLocation(
        file_path=result.path,
        line=sym.span.line,
        column=sym.span.column,
        end_line=sym.span.end_line or sym.span.line,
        end_column=sym.span.end_column or sym.span.column,
    )
    return lsp.Location(uri=result.path.as_uri(), range=_span_to_range(loc))


# ── Document symbols (outline) ─────────────────────────────────────────────────


_DECL_SYMBOL_KIND: dict[type, lsp.SymbolKind] = {
    ast_module.FuncDecl: lsp.SymbolKind.Function,
    ast_module.StructDecl: lsp.SymbolKind.Struct,
    ast_module.EnumDecl: lsp.SymbolKind.Enum,
    ast_module.ProtocolDecl: lsp.SymbolKind.Interface,
    ast_module.ExtensionDecl: lsp.SymbolKind.Module,
    ast_module.TypeAliasDecl: lsp.SymbolKind.TypeParameter,
}


@server.feature(lsp.TEXT_DOCUMENT_DOCUMENT_SYMBOL)
def document_symbols(
    ls: LanguageServer, params: lsp.DocumentSymbolParams
) -> list[lsp.DocumentSymbol]:
    """Return a flat list of top-level declarations for the outline view."""
    uri = params.text_document.uri
    with _lock:
        result = _results.get(uri)
    if result is None or result.program is None:
        return []

    out: list[lsp.DocumentSymbol] = []
    for item in result.program.items:
        sym_kind = _DECL_SYMBOL_KIND.get(type(item))
        if sym_kind is None:
            continue
        name = getattr(item, "name", None)
        span = getattr(item, "span", None)
        if not name or span is None:
            continue
        loc = SourceLocation(
            file_path=result.path,
            line=span.line,
            column=span.column,
            end_line=span.end_line or span.line,
            end_column=span.end_column or span.column,
        )
        rng = _span_to_range(loc)
        out.append(
            lsp.DocumentSymbol(
                name=name,
                kind=sym_kind,
                range=rng,
                selection_range=rng,
            )
        )
    return out


# ── Entry point ────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Rolang Language Server")
    parser.add_argument(
        "--stdio",
        action="store_true",
        default=True,
        help="Communicate over stdio (default)",
    )
    parser.add_argument(
        "--tcp",
        metavar="PORT",
        type=int,
        default=None,
        help="Listen on a TCP port instead of stdio (useful for debugging)",
    )
    args = parser.parse_args()

    if args.tcp:
        server.start_tcp("127.0.0.1", args.tcp)
    else:
        server.start_io()


if __name__ == "__main__":
    main()
