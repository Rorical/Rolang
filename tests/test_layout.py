"""Tests for the Rolang LayoutService."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from rolang.parser import parse
from rolang.resolver import resolve
from rolang.layout import LayoutService
from rolang.types import TypeTable
from rolang.symbols import SymbolTable



def _build_layout(source: str) -> LayoutService:
    """Parse and resolve source, returning a LayoutService."""
    program = parse(source)
    resolution = resolve(program)
    type_table = TypeTable()
    # Seed builtins
    for name in ("i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64",
                 "f32", "f64", "Bool", "Void"):
        type_table.get_builtin(name)
    layout = LayoutService(type_table, resolution.symbol_table)
    return layout


def _find_symbol(layout: LayoutService, name: str):
    """Find a symbol by name in the symbol table."""
    for sym in layout.symbol_table.symbols.values():
        if sym.name == name:
            return sym
    return None


# =============================================================================
# Primitive Sizes
# =============================================================================

def test_layout_primitive_sizes():
    """LayoutService returns correct sizes for primitives."""
    layout = _build_layout("")
    tt = layout.type_table

    assert layout.size_of(tt.get_builtin("i8")) == 1
    assert layout.size_of(tt.get_builtin("u8")) == 1
    assert layout.size_of(tt.get_builtin("Bool")) == 1
    assert layout.size_of(tt.get_builtin("i16")) == 2
    assert layout.size_of(tt.get_builtin("u16")) == 2
    assert layout.size_of(tt.get_builtin("i32")) == 4
    assert layout.size_of(tt.get_builtin("u32")) == 4
    assert layout.size_of(tt.get_builtin("f32")) == 4
    assert layout.size_of(tt.get_builtin("i64")) == 8
    assert layout.size_of(tt.get_builtin("u64")) == 8
    assert layout.size_of(tt.get_builtin("f64")) == 8
    assert layout.size_of(tt.get_builtin("Void")) == 0


# =============================================================================
# Tuple Sizes
# =============================================================================

def test_layout_tuple_size():
    """Tuples are heap-allocated; a value of tuple type is a
    pointer (8 bytes). Use `payload_size_of` for the actual element
    layout."""
    layout = _build_layout("")
    tt = layout.type_table

    i32 = tt.get_builtin("i32")
    i64 = tt.get_builtin("i64")
    tuple_id = tt.make_tuple(((None, i32), (None, i64)))

    assert layout.size_of(tuple_id) == 8
    assert layout.payload_size_of(tuple_id) == 4 + 8


def test_layout_empty_tuple_size():
    """An empty tuple value is still a heap pointer — its storage
    is 8 bytes. The payload is empty."""
    layout = _build_layout("")
    tt = layout.type_table

    empty = tt.make_tuple(())
    assert layout.size_of(empty) == 8
    assert layout.payload_size_of(empty) == 0


# =============================================================================
# Optional Sizes
# =============================================================================

def test_layout_optional_non_pointer():
    """Optional of a non-pointer primitive uses ``{i1, T}`` layout — and
    its byte size must agree with the LLVM struct's natural alignment
    (1 byte tag + padding + T + trailing padding). For ``i32`` this is
    4-byte aligned, giving size 8, not the packed 5 that older code
    reported (and which under-allocated vec slots).
    """
    layout = _build_layout("")
    tt = layout.type_table

    i32 = tt.get_builtin("i32")
    opt = tt.make_optional(i32)
    # 1 byte tag + 3 bytes padding + 4 bytes i32 = 8
    assert layout.size_of(opt) == 8


def test_layout_optional_pointer_primitive():
    """Optional of a primitive pointer-shaped type uses ``{i1, T}``.

    ``RawPtr`` is treated as a primitive (not a struct/enum), so it does
    NOT get the null-pointer-is-None shortcut. It uses the same
    aligned ``{i1, T}`` layout as ``Optional<i64>``: 1 tag + 7 padding +
    8 byte pointer = 16.
    """
    layout = _build_layout("")
    tt = layout.type_table

    raw_ptr = tt.get_builtin("RawPtr")
    opt = tt.make_optional(raw_ptr)
    assert layout.size_of(opt) == 16


# =============================================================================
# Struct Sizes
# =============================================================================

def test_layout_simple_struct():
    """Simple struct size is sum of field sizes."""
    source = """
struct Point {
    var x: i32
    var y: i32
}

def main() -> i32 { 0 }
"""
    layout = _build_layout(source)
    sym = _find_symbol(layout, "Point")
    assert sym is not None
    struct_type = layout.type_table.make_struct(sym.id, ())
    assert layout.size_of(struct_type) == 4 + 4


def test_layout_nested_struct():
    """Every struct is heap-allocated, so a value of struct type
    is an 8-byte pointer regardless of payload size. `payload_size_of`
    still returns the inline sum-of-fields for inspection."""
    source = """
struct Inner {
    var a: i64
    var b: i8
}

struct Outer {
    var inner: Inner
    var flag: Bool
}

def main() -> i32 { 0 }
"""
    layout = _build_layout(source)
    outer_sym = _find_symbol(layout, "Outer")
    assert outer_sym is not None
    outer_type = layout.type_table.make_struct(outer_sym.id, ())
    # Value size is the pointer width.
    assert layout.size_of(outer_type) == 8
    # Payload: inner pointer (8) + flag (1) = 9.
    assert layout.payload_size_of(outer_type) == 8 + 1


def test_layout_struct_layout_metadata():
    """StructLayout contains correct field metadata."""
    source = """
struct Point {
    var x: i32
    var y: i64
}

def main() -> i32 { 0 }
"""
    layout = _build_layout(source)
    sym = _find_symbol(layout, "Point")
    assert sym is not None

    sl = layout.get_struct_layout(sym.id)
    assert sl is not None
    assert len(sl.fields) == 2
    assert sl.fields[0].name == "x"
    assert sl.fields[1].name == "y"
    assert sl.size == 4 + 8


# =============================================================================
# Enum Sizes
# =============================================================================

def test_layout_enum_no_payload():
    """Enums are heap-allocated. A value of enum type is a pointer
    (8 bytes). The payload (just the tag, 1 byte) is reported by
    `payload_size_of`."""
    source = """
enum Color {
    case red
    case green
    case blue
}

def main() -> i32 { 0 }
"""
    layout = _build_layout(source)
    sym = _find_symbol(layout, "Color")
    assert sym is not None
    enum_type = layout.type_table.make_enum(sym.id, ())
    assert layout.size_of(enum_type) == 8
    assert layout.payload_size_of(enum_type) == 1  # i8 tag


def test_layout_enum_with_payload():
    """Enum value size is pointer-width; payload is tag + max payload."""
    source = """
enum Value {
    case int_val(i32)
    case long_val(i64)
    case empty
}

def main() -> i32 { 0 }
"""
    layout = _build_layout(source)
    sym = _find_symbol(layout, "Value")
    assert sym is not None
    enum_type = layout.type_table.make_enum(sym.id, ())
    assert layout.size_of(enum_type) == 8
    # tag = i8 (1 byte), max payload = i64 (8 bytes)
    assert layout.payload_size_of(enum_type) == 1 + 8


def test_layout_enum_layout_metadata():
    """EnumLayout contains correct case metadata."""
    source = """
enum Value {
    case int_val(i32)
    case long_val(i64)
}

def main() -> i32 { 0 }
"""
    layout = _build_layout(source)
    sym = _find_symbol(layout, "Value")
    assert sym is not None

    el = layout.get_enum_layout(sym.id)
    assert el is not None
    assert len(el.cases) == 2
    assert el.cases[0].name == "int_val"
    assert el.cases[0].tag == 0
    assert el.cases[0].payload_size == 4
    assert el.cases[1].name == "long_val"
    assert el.cases[1].tag == 1
    assert el.cases[1].payload_size == 8
    assert el.tag_size == 1
    assert el.max_payload_size == 8
    assert el.size == 1 + 8


# =============================================================================
# Generic Struct Sizes
# =============================================================================

def test_layout_generic_struct():
    """Generic struct size uses layout service (may be unresolved)."""
    source = """
struct Box<T> {
    var value: T
}

def main() -> i32 { 0 }
"""
    layout = _build_layout(source)
    sym = _find_symbol(layout, "Box")
    assert sym is not None
    # Generic struct with unresolved type variable - size should fallback
    struct_type = layout.type_table.make_struct(sym.id, ())
    size = layout.size_of(struct_type)
    # Because T is unresolved, it should return a fallback size (8)
    assert size == 8
