"""
Rolang Language Server — LSP implementation.

Entry point:  rolang-langserver [--stdio|--tcp PORT]

Requires the optional langserver extra:
    pip install 'rolang[langserver]'
    uv tool install 'rolang[langserver]'

The server supports:
  * Real-time diagnostics (parse / resolve / type errors)
  * Hover — inferred type of the expression under the cursor
  * Go-to-definition — jump to the declaration of a symbol
  * Document symbols — file outline for the editor sidebar
"""

from __future__ import annotations


def main() -> None:
    """Start the Rolang language server.

    The pygls package must be installed (pip install 'rolang[langserver]').
    """
    try:
        from .server import main as _main  # noqa: PLC0415
    except ImportError as exc:
        raise SystemExit(
            "pygls is required for rolang-langserver.\n"
            "Install it with:  pip install 'rolang[langserver]'\n"
            f"Original error: {exc}"
        ) from exc
    _main()


__all__ = ["main"]
