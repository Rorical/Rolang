"""
Diagnostic formatting for the Rolang compiler.

Provides pretty-printed error and warning messages with source location context.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import List, Optional, TextIO

from .ast import Span
from .symbols import ResolutionError, ResolutionErrorKind
from .checker_core import TypeError, TypeErrorKind


class Severity(Enum):
    """Diagnostic severity level."""
    ERROR = auto()
    WARNING = auto()
    NOTE = auto()
    HELP = auto()


@dataclass
class SourceLocation:
    """A location in source code."""
    file_path: Path
    line: int
    column: int
    end_line: Optional[int] = None
    end_column: Optional[int] = None


@dataclass
class Diagnostic:
    """A compiler diagnostic message."""
    severity: Severity
    code: Optional[str]
    message: str
    location: Optional[SourceLocation]
    source_line: Optional[str]
    notes: List[str]

    def __init__(
        self,
        severity: Severity,
        message: str,
        code: Optional[str] = None,
        location: Optional[SourceLocation] = None,
        source_line: Optional[str] = None,
        notes: Optional[List[str]] = None,
    ) -> None:
        self.severity = severity
        self.code = code
        self.message = message
        self.location = location
        self.source_line = source_line
        self.notes = notes or []


class DiagnosticFormatter:
    """Formats diagnostics for terminal output."""

    def __init__(
        self,
        use_color: bool = True,
        stream: TextIO = sys.stderr,
    ) -> None:
        self.use_color = use_color
        self.stream = stream

    def _color(self, text: str, color_code: str) -> str:
        """Apply ANSI color code if colors are enabled."""
        if not self.use_color:
            return text
        return f"\033[{color_code}m{text}\033[0m"

    def _red(self, text: str) -> str:
        return self._color(text, "1;31")

    def _yellow(self, text: str) -> str:
        return self._color(text, "1;33")

    def _blue(self, text: str) -> str:
        return self._color(text, "1;34")

    def _cyan(self, text: str) -> str:
        return self._color(text, "1;36")

    def _bold(self, text: str) -> str:
        return self._color(text, "1")

    def _severity_str(self, severity: Severity) -> str:
        """Get colored severity string."""
        if severity == Severity.ERROR:
            return self._red("error")
        elif severity == Severity.WARNING:
            return self._yellow("warning")
        elif severity == Severity.NOTE:
            return self._cyan("note")
        else:
            return self._blue("help")

    def format_diagnostic(self, diag: Diagnostic) -> str:
        """Format a diagnostic message."""
        lines: List[str] = []

        # Header: severity[code]: message
        header = self._severity_str(diag.severity)
        if diag.code:
            header += f"[{diag.code}]"
        header += f": {self._bold(diag.message)}"
        lines.append(header)

        # Location line
        if diag.location:
            loc = diag.location
            loc_str = f"  --> {loc.file_path}:{loc.line}:{loc.column}"
            lines.append(loc_str)

            # Source context
            if diag.source_line:
                line_num = str(loc.line)
                padding = " " * len(line_num)

                # Empty line before source
                lines.append(f"   {padding}|")

                # Source line with line number
                lines.append(f"   {line_num} | {diag.source_line}")

                # Caret pointing to the error
                caret_padding = " " * (loc.column - 1)
                if loc.end_column and loc.end_column > loc.column:
                    carets = "^" * (loc.end_column - loc.column)
                else:
                    carets = "^"
                caret_line = f"   {padding} | {caret_padding}{self._red(carets)}"
                lines.append(caret_line)

        # Notes
        for note in diag.notes:
            lines.append(f"   = {self._cyan('note')}: {note}")

        return "\n".join(lines)

    def emit(self, diag: Diagnostic) -> None:
        """Emit a diagnostic to the output stream."""
        formatted = self.format_diagnostic(diag)
        print(formatted, file=self.stream)
        print(file=self.stream)  # Blank line after diagnostic


class DiagnosticCollector:
    """Collects and manages diagnostics from all compiler phases."""

    def __init__(self, source_files: dict[Path, str]) -> None:
        """
        Initialize the collector.

        Args:
            source_files: Map of file paths to their contents
        """
        self.source_files = source_files
        self.diagnostics: List[Diagnostic] = []
        self.error_count = 0
        self.warning_count = 0

    def _get_source_line(self, path: Path, line: int) -> Optional[str]:
        """Get a source line from the cached files."""
        content = self.source_files.get(path)
        if content is None:
            return None
        lines = content.splitlines()
        if 1 <= line <= len(lines):
            return lines[line - 1]
        return None

    def _span_to_location(
        self,
        span: Optional[Span],
        file_path: Path,
    ) -> Optional[SourceLocation]:
        """Convert a Span to a SourceLocation."""
        if span is None:
            return None
        return SourceLocation(
            file_path=file_path,
            line=span.line,
            column=span.column,
            end_line=span.end_line,
            end_column=span.end_column,
        )

    def add_error(
        self,
        message: str,
        file_path: Optional[Path] = None,
        span: Optional[Span] = None,
        code: Optional[str] = None,
        notes: Optional[List[str]] = None,
    ) -> None:
        """Add an error diagnostic."""
        location = None
        source_line = None

        if span and file_path:
            location = self._span_to_location(span, file_path)
            source_line = self._get_source_line(file_path, span.line)

        diag = Diagnostic(
            severity=Severity.ERROR,
            message=message,
            code=code,
            location=location,
            source_line=source_line,
            notes=notes,
        )
        self.diagnostics.append(diag)
        self.error_count += 1

    def add_warning(
        self,
        message: str,
        file_path: Optional[Path] = None,
        span: Optional[Span] = None,
        code: Optional[str] = None,
        notes: Optional[List[str]] = None,
    ) -> None:
        """Add a warning diagnostic."""
        location = None
        source_line = None

        if span and file_path:
            location = self._span_to_location(span, file_path)
            source_line = self._get_source_line(file_path, span.line)

        diag = Diagnostic(
            severity=Severity.WARNING,
            message=message,
            code=code,
            location=location,
            source_line=source_line,
            notes=notes,
        )
        self.diagnostics.append(diag)
        self.warning_count += 1

    def add_resolution_error(
        self,
        error: ResolutionError,
        file_path: Path,
    ) -> None:
        """Add a name resolution error."""
        code_map = {
            ResolutionErrorKind.UNDEFINED_TYPE: "E0001",
            ResolutionErrorKind.UNDEFINED_VALUE: "E0002",
            ResolutionErrorKind.DUPLICATE_TYPE: "E0003",
            ResolutionErrorKind.DUPLICATE_VALUE: "E0004",
        }
        self.add_error(
            message=error.message,
            file_path=file_path,
            span=error.span,
            code=code_map.get(error.kind),
        )

    def add_type_error(
        self,
        error: TypeError,
        file_path: Path,
    ) -> None:
        """Add a type checking error."""
        code_map = {
            TypeErrorKind.TYPE_MISMATCH: "E0101",
            TypeErrorKind.UNDEFINED_MEMBER: "E0102",
            TypeErrorKind.NOT_CALLABLE: "E0103",
            TypeErrorKind.WRONG_ARG_COUNT: "E0104",
            TypeErrorKind.WRONG_ARG_TYPE: "E0105",
            TypeErrorKind.CANNOT_INFER: "E0106",
            TypeErrorKind.NOT_ASSIGNABLE: "E0107",
            TypeErrorKind.INVALID_OPERATION: "E0108",
            TypeErrorKind.NOT_A_TYPE: "E0109",
            TypeErrorKind.GENERIC_ARG_COUNT: "E0110",
        }
        self.add_error(
            message=error.message,
            file_path=file_path,
            span=error.span,
            code=code_map.get(error.kind, "E0199"),
        )

    def add_parse_error(
        self,
        message: str,
        file_path: Path,
        line: int = 1,
        column: int = 1,
    ) -> None:
        """Add a parse error."""
        span = Span(line=line, column=column, end_line=line, end_column=column)
        self.add_error(
            message=message,
            file_path=file_path,
            span=span,
            code="E0000",
        )

    def add_codegen_error(self, message: str) -> None:
        """Add a code generation error."""
        self.add_error(message=message, code="E0200")

    def add_io_error(self, message: str, file_path: Optional[Path] = None) -> None:
        """Add an I/O error."""
        if file_path:
            self.add_error(message=f"{file_path}: {message}", code="E0300")
        else:
            self.add_error(message=message, code="E0300")

    def has_errors(self) -> bool:
        """Check if any errors were collected."""
        return self.error_count > 0

    def emit_all(self, formatter: DiagnosticFormatter) -> None:
        """Emit all collected diagnostics."""
        for diag in self.diagnostics:
            formatter.emit(diag)

    def summary(self) -> str:
        """Get a summary of diagnostic counts."""
        parts = []
        if self.error_count > 0:
            s = "s" if self.error_count != 1 else ""
            parts.append(f"{self.error_count} error{s}")
        if self.warning_count > 0:
            s = "s" if self.warning_count != 1 else ""
            parts.append(f"{self.warning_count} warning{s}")
        if not parts:
            return "no errors"
        return " and ".join(parts) + " generated"


def create_formatter(use_color: Optional[bool] = None) -> DiagnosticFormatter:
    """
    Create a diagnostic formatter.

    Args:
        use_color: Whether to use colors. If None, auto-detect based on terminal.

    Returns:
        A DiagnosticFormatter instance.
    """
    if use_color is None:
        use_color = sys.stderr.isatty()
    return DiagnosticFormatter(use_color=use_color)
