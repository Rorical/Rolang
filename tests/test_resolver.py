"""Tests for the RoLang name resolver."""

import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from rolang.parser import parse
from rolang.resolver import resolve
from rolang.symbols import (
    SymbolKind,
    Namespace,
    ResolutionErrorKind,
)


# ========================= Basic Resolution Tests =========================

def test_simple_function():
    """Test resolving a simple function."""
    source = """
def add(a: i32, b: i32) -> i32 {
    return a + b;
}
"""
    program = parse(source)
    result = resolve(program)

    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    # Function should be registered
    func_symbols = [s for s in result.symbol_table.symbols.values()
                    if s.name == "add" and s.kind == SymbolKind.FUNCTION]
    assert len(func_symbols) == 1

    # Parameters should be registered
    param_symbols = [s for s in result.symbol_table.symbols.values()
                     if s.kind == SymbolKind.PARAMETER]
    assert len(param_symbols) == 2
    param_names = {s.name for s in param_symbols}
    assert param_names == {"a", "b"}


def test_variable_declaration():
    """Test resolving variable declarations."""
    source = """
def test() {
    let x: i32 = 42;
    var y = x + 1;
    let z = x + y;
}
"""
    program = parse(source)
    result = resolve(program)

    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    # Variables should be registered
    var_symbols = [s for s in result.symbol_table.symbols.values()
                   if s.kind == SymbolKind.VARIABLE]
    assert len(var_symbols) == 3
    var_names = {s.name for s in var_symbols}
    assert var_names == {"x", "y", "z"}


def test_mutable_variable():
    """Test that var creates mutable symbols."""
    source = """
def test() {
    let immutable = 1;
    var mutable = 2;
}
"""
    program = parse(source)
    result = resolve(program)

    assert not result.has_errors()

    symbols = {s.name: s for s in result.symbol_table.symbols.values()
               if s.kind == SymbolKind.VARIABLE}
    assert symbols["immutable"].is_mutable is False
    assert symbols["mutable"].is_mutable is True


# ========================= Type Namespace Tests =========================

def test_struct_type():
    """Test resolving struct types."""
    source = """
struct Point {
    var x: i32;
    var y: i32;
}

def make_point() -> Point {
    return Point { x: 0, y: 0 };
}
"""
    program = parse(source)
    result = resolve(program)

    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    # Struct should be registered in type namespace
    struct_symbols = [s for s in result.symbol_table.symbols.values()
                      if s.name == "Point" and s.kind == SymbolKind.STRUCT]
    assert len(struct_symbols) == 1
    assert struct_symbols[0].namespace == Namespace.TYPE


def test_generic_struct():
    """Test resolving generic struct with type parameters."""
    source = """
struct Container<T> {
    var value: T;
}

def wrap<T>(x: T) -> Container<T> {
    return Container { value: x };
}
"""
    program = parse(source)
    result = resolve(program)

    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    # Generic parameters should be registered
    generic_symbols = [s for s in result.symbol_table.symbols.values()
                       if s.kind == SymbolKind.GENERIC_PARAM]
    assert len(generic_symbols) >= 2  # One for struct, one for function


def test_builtin_types():
    """Test that builtin types are pre-registered."""
    source = """
def test(a: i32, b: i64, c: f32, d: f64, e: Bool) {
}
"""
    program = parse(source)
    result = resolve(program)

    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    # All builtin types should be resolvable
    builtins = result.symbol_table.builtins
    assert "i32" in builtins
    assert "i64" in builtins
    assert "f32" in builtins
    assert "f64" in builtins
    assert "Bool" in builtins


def test_enum_type():
    """Test resolving enum types."""
    source = """
enum Option<T> {
    case none
    case some(T)
}

def test() -> Option<i32> {
    return Option.none;
}
"""
    program = parse(source)
    result = resolve(program)

    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    enum_symbols = [s for s in result.symbol_table.symbols.values()
                    if s.name == "Option" and s.kind == SymbolKind.ENUM]
    assert len(enum_symbols) == 1


def test_protocol_type():
    """Test resolving protocol types."""
    source = """
protocol Show {
    def show() -> String;
}

struct String {
    var handle: RawPtr;
}

def display<T: Show>(item: T) {
    let s = item.show();
}
"""
    program = parse(source)
    result = resolve(program)

    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    protocol_symbols = [s for s in result.symbol_table.symbols.values()
                        if s.name == "Show" and s.kind == SymbolKind.PROTOCOL]
    assert len(protocol_symbols) == 1


# ========================= Scope Chain Tests =========================

def test_block_scoping():
    """Test that blocks create new scopes."""
    source = """
def test() {
    let x = 1;
    {
        let y = x + 1;
        let z = y + 1;
    }
}
"""
    program = parse(source)
    result = resolve(program)

    assert not result.has_errors()

    var_names = {s.name for s in result.symbol_table.symbols.values()
                 if s.kind == SymbolKind.VARIABLE}
    assert var_names == {"x", "y", "z"}


def test_variable_shadowing():
    """Test that variables can shadow outer scope variables."""
    source = """
def test() {
    let x = 1;
    {
        let x = 2;
        let y = x;
    }
    let z = x;
}
"""
    program = parse(source)
    result = resolve(program)

    # Shadowing is allowed, no errors
    assert not result.has_errors()

    # There should be two 'x' symbols
    x_symbols = [s for s in result.symbol_table.symbols.values()
                 if s.name == "x" and s.kind == SymbolKind.VARIABLE]
    assert len(x_symbols) == 2


def test_nested_functions():
    """Test that nested function scopes work correctly."""
    source = """
struct Point {
    var x: i32;
    var y: i32;

    def distance() -> f64 {
        let dx = x * x;
        let dy = y * y;
        return 0.0;
    }
}
"""
    program = parse(source)
    result = resolve(program)

    assert not result.has_errors()


# ========================= Pattern Binding Tests =========================

def test_for_loop_pattern():
    """Test that for loop binds the pattern variable."""
    source = """
def test() {
    for i in [1, 2, 3] {
        let squared = i * i;
    }
}
"""
    program = parse(source)
    result = resolve(program)

    assert not result.has_errors()

    # 'i' should be a variable
    i_symbols = [s for s in result.symbol_table.symbols.values()
                 if s.name == "i" and s.kind == SymbolKind.VARIABLE]
    assert len(i_symbols) == 1


def test_switch_case_pattern():
    """Test that switch case binds pattern variables."""
    source = """
enum Option<T> {
    case none
    case some(T)
}

def test() {
    let opt: Option<i32> = Option.some(42);
    switch opt {
    case .none:
        let x = 0;
    case .some(let v):
        let x = v;
    }
}
"""
    program = parse(source)
    result = resolve(program)

    assert not result.has_errors()

    # 'v' should be bound in the some case
    v_symbols = [s for s in result.symbol_table.symbols.values()
                 if s.name == "v" and s.kind == SymbolKind.VARIABLE]
    assert len(v_symbols) == 1


def test_if_let_pattern():
    """Test that if-let binds pattern variables in then block."""
    source = """
enum Option<T> {
    case none
    case some(T)
}

def test() {
    let opt: Option<i32> = Option.some(42);
    if let value = opt {
        let x = value;
    }
}
"""
    program = parse(source)
    result = resolve(program)

    assert not result.has_errors()


def test_tuple_pattern():
    """Test tuple pattern binding."""
    source = """
def test() {
    let (a, b) = (1, 2);
    let sum = a + b;
}
"""
    program = parse(source)
    result = resolve(program)

    assert not result.has_errors()

    var_names = {s.name for s in result.symbol_table.symbols.values()
                 if s.kind == SymbolKind.VARIABLE}
    assert "a" in var_names
    assert "b" in var_names


# ========================= Forward Reference Tests =========================

def test_forward_reference_function():
    """Test that functions can be called before they are defined."""
    source = """
def first() -> i32 {
    return second();
}

def second() -> i32 {
    return 42;
}
"""
    program = parse(source)
    result = resolve(program)

    assert not result.has_errors()


def test_forward_reference_type():
    """Test that types can be referenced before they are defined."""
    source = """
def make_point() -> Point {
    return Point { x: 0, y: 0 };
}

struct Point {
    var x: i32;
    var y: i32;
}
"""
    program = parse(source)
    result = resolve(program)

    assert not result.has_errors()


# ========================= Namespace Separation Tests =========================

def test_same_name_type_and_function():
    """Test that a type and function can have the same name."""
    source = """
struct Foo {
    var x: i32;
}

def Foo() -> Foo {
    return Foo { x: 0 };
}
"""
    program = parse(source)
    result = resolve(program)

    # No error - type and value namespaces are separate
    assert not result.has_errors()

    # Should have both a struct and a function named Foo
    foo_struct = [s for s in result.symbol_table.symbols.values()
                  if s.name == "Foo" and s.kind == SymbolKind.STRUCT]
    foo_func = [s for s in result.symbol_table.symbols.values()
                if s.name == "Foo" and s.kind == SymbolKind.FUNCTION]

    assert len(foo_struct) == 1
    assert len(foo_func) == 1


# ========================= Lambda Tests =========================

def test_lambda_parameters():
    """Test that lambda parameters are bound in lambda scope."""
    source = """
def test() {
    let f = { x: i32, y: i32 in
        return x + y;
    };
}
"""
    program = parse(source)
    result = resolve(program)

    assert not result.has_errors()


def test_lambda_captures():
    """Test that lambdas can capture outer variables."""
    source = """
def test() {
    let x = 10;
    let f = { y: i32 in
        return x + y;
    };
}
"""
    program = parse(source)
    result = resolve(program)

    assert not result.has_errors()


# ========================= Error Detection Tests =========================

def test_undefined_variable():
    """Test that undefined variables are detected."""
    source = """
def test() {
    let x = undefined_var;
}
"""
    program = parse(source)
    result = resolve(program)

    assert result.has_errors()
    assert any(e.kind == ResolutionErrorKind.UNDEFINED_VALUE
               for e in result.errors)


def test_undefined_type():
    """Test that undefined types are detected."""
    source = """
def test() -> UndefinedType {
    return nil;
}
"""
    program = parse(source)
    result = resolve(program)

    assert result.has_errors()
    assert any(e.kind == ResolutionErrorKind.UNDEFINED_TYPE
               for e in result.errors)


def test_duplicate_function():
    """Test that duplicate function names are detected."""
    source = """
def foo() {}
def foo() {}
"""
    program = parse(source)
    result = resolve(program)

    assert result.has_errors()
    assert any(e.kind == ResolutionErrorKind.DUPLICATE_VALUE
               for e in result.errors)


def test_duplicate_struct():
    """Test that duplicate struct names are detected."""
    source = """
struct Point {
    var x: i32;
}
struct Point {
    var y: i32;
}
"""
    program = parse(source)
    result = resolve(program)

    assert result.has_errors()
    assert any(e.kind == ResolutionErrorKind.DUPLICATE_TYPE
               for e in result.errors)


def test_duplicate_variable_in_scope():
    """Test that duplicate variable names in same scope are detected."""
    source = """
def test() {
    let x = 1;
    let x = 2;
}
"""
    program = parse(source)
    result = resolve(program)

    assert result.has_errors()
    assert any(e.kind == ResolutionErrorKind.DUPLICATE_VALUE
               for e in result.errors)


def test_multiple_errors():
    """Test that multiple errors are collected."""
    source = """
def test() {
    let x = undefined1;
    let y = undefined2;
    let z = undefined3;
}
"""
    program = parse(source)
    result = resolve(program)

    # Should report all three undefined variable errors
    assert len(result.errors) == 3


# ========================= Complex Example Tests =========================

def test_extern_function():
    """Test resolving extern function declarations."""
    source = """
extern "C" def printf(fmt: RawPtr) -> i32;

def test() {
    printf(nil);
}
"""
    program = parse(source)
    result = resolve(program)

    assert not result.has_errors()

    printf_symbols = [s for s in result.symbol_table.symbols.values()
                      if s.name == "printf" and s.kind == SymbolKind.EXTERN_FUNC]
    assert len(printf_symbols) == 1


def test_extension():
    """Test resolving extension declarations."""
    source = """
struct Point {
    var x: i32;
    var y: i32;
}

extension Point {
    def magnitude() -> f64 {
        return 0.0;
    }
}
"""
    program = parse(source)
    result = resolve(program)

    assert not result.has_errors()


def test_comprehensive_example():
    """Test resolving the basic.rl example file."""
    example_path = Path(__file__).parent.parent / "examples" / "basic.rl"
    if not example_path.exists():
        return  # Skip if example file doesn't exist

    source = example_path.read_text()
    if "String" in source:
        source += "\nstruct String { var handle: RawPtr; }\n"
    if "Dict" in source:
        source += "\nstruct Dict<K, V> { }\n"
    program = parse(source)
    result = resolve(program)

    # Print any errors for debugging
    if result.errors:
        for e in result.errors:
            print(f"  {e}")

    # The example file should resolve without errors
    assert not result.has_errors(), f"Errors in basic.rl: {result.errors}"

    # Should have a reasonable number of symbols
    assert len(result.symbol_table.symbols) > 20


def test_associated_types():
    """Test resolving associated type declarations in protocols."""
    source = """
protocol Container {
    associatedtype Element;
    def first() -> Element?;
}
"""
    program = parse(source)
    result = resolve(program)

    assert not result.has_errors()

    assoc_symbols = [s for s in result.symbol_table.symbols.values()
                     if s.kind == SymbolKind.ASSOCIATED_TYPE]
    assert len(assoc_symbols) == 1
    assert assoc_symbols[0].name == "Element"


def test_type_constraints():
    """Test resolving where clause constraints."""
    source = """
protocol Equatable {
    def equals(other: Self) -> Bool;
}

def find<T, C>(in container: C, item: T) -> Bool
    where T: Equatable, C: Container
{
    return false;
}

protocol Container {
    associatedtype Element;
}
"""
    program = parse(source)
    result = resolve(program)

    # Should resolve protocols and their usage in constraints
    assert not result.has_errors()


if __name__ == "__main__":
    # Run all tests
    tests = [
        # Basic resolution
        test_simple_function,
        test_variable_declaration,
        test_mutable_variable,
        # Type namespace
        test_struct_type,
        test_generic_struct,
        test_builtin_types,
        test_enum_type,
        test_protocol_type,
        # Scope chains
        test_block_scoping,
        test_variable_shadowing,
        test_nested_functions,
        # Pattern bindings
        test_for_loop_pattern,
        test_switch_case_pattern,
        test_if_let_pattern,
        test_tuple_pattern,
        # Forward references
        test_forward_reference_function,
        test_forward_reference_type,
        # Namespace separation
        test_same_name_type_and_function,
        # Lambdas
        test_lambda_parameters,
        test_lambda_captures,
        # Error detection
        test_undefined_variable,
        test_undefined_type,
        test_duplicate_function,
        test_duplicate_struct,
        test_duplicate_variable_in_scope,
        test_multiple_errors,
        # Complex examples
        test_extern_function,
        test_extension,
        test_comprehensive_example,
        test_associated_types,
        test_type_constraints,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            print(f"PASS: {test.__name__}")
            passed += 1
        except Exception as e:
            print(f"FAIL: {test.__name__}")
            print(f"      Error: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
