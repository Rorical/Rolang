"""Project scaffolding templates for 'rolang new' and 'rolang init'."""

from pathlib import Path


# ── Source templates ──────────────────────────────────────────────────────────

_BINARY_MAIN = '''\
import "io.rl"

def main() -> i32 {
    println("Hello, World!");
    return 0;
}
'''

_LIBRARY_LIB = '''\
// {name} — public library API.
// Add your exported functions and types here.

def greet(name: String) -> String {{
    return "Hello, " + name + "!"
}}
'''

_GITIGNORE = """\
# Rolang build output
build/

# Toolchain cache (symlinks to installed deps)
.rolang/

# Python bytecode (compiler tooling)
__pycache__/
*.pyc
.venv/
"""


# ── Manifest templates ────────────────────────────────────────────────────────

def _binary_manifest(name: str) -> str:
    return f"""\
[package]
name = "{name}"
version = "0.1.0"
description = ""
authors = []
edition = "2024"
type = "binary"
"""


def _library_manifest(name: str) -> str:
    return f"""\
[package]
name = "{name}"
version = "0.1.0"
description = ""
authors = []
edition = "2024"
type = "library"
"""


# ── Scaffolding ───────────────────────────────────────────────────────────────

def scaffold_binary(dest: Path, name: str) -> None:
    """Create a new binary project skeleton at *dest*."""
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "src").mkdir(exist_ok=True)
    (dest / "src" / "main.rl").write_text(_BINARY_MAIN, encoding="utf-8")
    (dest / "rolang.toml").write_text(_binary_manifest(name), encoding="utf-8")
    _maybe_write_gitignore(dest)


def scaffold_library(dest: Path, name: str) -> None:
    """Create a new library project skeleton at *dest*."""
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "src").mkdir(exist_ok=True)
    (dest / "src" / "lib.rl").write_text(
        _LIBRARY_LIB.format(name=name), encoding="utf-8"
    )
    (dest / "rolang.toml").write_text(_library_manifest(name), encoding="utf-8")
    _maybe_write_gitignore(dest)


def _maybe_write_gitignore(dest: Path) -> None:
    gi = dest / ".gitignore"
    if not gi.exists():
        gi.write_text(_GITIGNORE, encoding="utf-8")
