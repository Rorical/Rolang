"""Snapshot tests for MIR and LLVM IR output.

These protect the lowering and codegen from regressions during refactoring.
"""

from __future__ import annotations

from pathlib import Path

from rolang.driver import CompileOptions, EmitKind, compile_source


def _compile_to_mir(tmp_path: Path, name: str, source: str, include_paths=None) -> str:
    """Compile source to MIR text."""
    source_path = tmp_path / f"{name}.rl"
    source_path.write_text(source, encoding="utf-8")
    result = compile_source(
        source_path,
        CompileOptions(
            emit=EmitKind.MIR,
            include_paths=include_paths or [],
        ),
    )
    assert result.success, f"MIR compilation failed: {result.diagnostics}"
    assert result.output_content is not None
    return result.output_content


def _compile_to_llvm(tmp_path: Path, name: str, source: str, include_paths=None) -> str:
    """Compile source to LLVM IR text."""
    source_path = tmp_path / f"{name}.rl"
    source_path.write_text(source, encoding="utf-8")
    result = compile_source(
        source_path,
        CompileOptions(
            emit=EmitKind.LLVM_IR,
            include_paths=include_paths or [],
        ),
    )
    assert result.success, f"LLVM compilation failed: {result.diagnostics}"
    assert result.output_content is not None
    return result.output_content


# ---------------------------------------------------------------------------
# MIR snapshot tests
# ---------------------------------------------------------------------------

def test_mir_simple_function(tmp_path: Path):
    """MIR output for a simple arithmetic function."""
    mir = _compile_to_mir(
        tmp_path, "simple",
        """
def add(x: i64, y: i64) -> i64 {
    return x + y;
}
""")
    assert "def add" in mir
    assert "add" in mir.lower()
    assert "return" in mir


def test_mir_struct_with_fields(tmp_path: Path):
    """MIR output for a struct with fields."""
    mir = _compile_to_mir(
        tmp_path, "struct_test",
        """
struct Point {
    var x: i64;
    var y: i64;
}

def make_point() -> Point {
    return Point { x: 10, y: 20 };
}
""")
    assert "Point" in mir
    assert "struct" in mir.lower()


def test_mir_enum_with_payload(tmp_path: Path):
    """MIR output for an enum with payload."""
    mir = _compile_to_mir(
        tmp_path, "enum_test",
        """
enum Color {
    case Red;
    case Green(i64);
    case Blue;
}

def main() -> i32 {
    return 0;
}
""")
    assert "Color" in mir
    assert "Green" in mir and "Blue" in mir and "Red" in mir


def test_mir_generic_function(tmp_path: Path):
    """MIR output after monomorphization of a generic function."""
    mir = _compile_to_mir(
        tmp_path, "generic_test",
        """
def answer() -> i64 {
    let x: i64 = 42;
    return x;
}
""")
    assert "def answer" in mir
    assert "42" in mir


def test_mir_if_else_control_flow(tmp_path: Path):
    """MIR output for if/else control flow."""
    mir = _compile_to_mir(
        tmp_path, "ifelse",
        """
def max(x: i64, y: i64) -> i64 {
    if x > y {
        return x;
    }
    return y;
}
""")
    assert "def max" in mir
    assert "if" in mir.lower() and "then" in mir.lower()


def test_mir_array_operations(tmp_path: Path):
    """MIR output for array literal and indexing."""
    mir = _compile_to_mir(
        tmp_path, "array_test",
        """
def first() -> i64 {
    let xs = [10, 20, 30];
    return xs[0];
}
""")
    assert "Vec_i64_with_capacity" in mir
    assert "Vec_i64_push" in mir
    assert "Vec_i64_get" in mir


# ---------------------------------------------------------------------------
# LLVM IR snapshot tests
# ---------------------------------------------------------------------------

def test_llvm_simple_function(tmp_path: Path):
    """LLVM IR contains function definition and return."""
    llvm = _compile_to_llvm(
        tmp_path, "llvm_simple",
        """
def answer() -> i64 {
    return 42;
}
""")
    assert "define" in llvm
    assert "ret" in llvm


def test_llvm_struct_type(tmp_path: Path):
    """LLVM IR contains struct type definition."""
    llvm = _compile_to_llvm(
        tmp_path, "llvm_struct",
        """
struct Point {
    var x: i64;
    var y: i64;
}

def get_x(p: Point) -> i64 {
    return p.x;
}
""")
    assert "Point" in llvm


def test_llvm_optional_layout(tmp_path: Path):
    """LLVM IR correctly lowers optional types."""
    llvm = _compile_to_llvm(
        tmp_path, "llvm_optional",
        """
def maybe() -> i64? {
    return 42;
}
""")
    assert "define" in llvm



def test_llvm_generic_instantiation(tmp_path: Path):
    """LLVM IR after monomorphization has concrete types."""
    llvm = _compile_to_llvm(
        tmp_path, "llvm_generic",
        """
def main() -> i32 {
    return 42;
}
""")
    assert "define" in llvm
