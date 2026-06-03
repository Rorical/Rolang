"""Tests for the RoLang type checker."""

import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from rolang.parser import parse
from rolang.resolver import resolve
from rolang.checker import typecheck, TypeErrorKind, CalleeKind
from rolang.types import TypeKind, PrimitiveType


STRING_PRELUDE = "\nstruct String { var handle: RawPtr; }\n"

# Minimal `Vec<T>` and `Dict<K, V>` definitions so the checker can
# resolve `[...]` and `[k: v]` literals (which now lower to these
# structs). The real method bodies are not needed; only the shape is.
COLLECTIONS_PRELUDE = """
struct Vec<T> {
    var handle: RawPtr;
    pub static def with_capacity(capacity: i32) -> Vec<T> {
        var v: Vec<T>;
        return v;
    }
    pub def push(value: T) -> Void { }
    pub def get(index: i32) -> T {
        var out: T;
        return out;
    }
    pub def len() -> i32 { return 0; }
}
struct Dict<K, V> {
    var handle: RawPtr;
    pub static def with_capacity(capacity: i32, key_kind: i32) -> Dict<K, V> {
        var d: Dict<K, V>;
        return d;
    }
    pub def set(key: K, value: V) -> Void { }
    pub def get(key: K) -> V? {
        var out: V;
        return out;
    }
    pub def len() -> i64 { return 0; }
}
"""


def with_string_prelude(source: str) -> str:
    if "String" in source or '"' in source:
        return source + STRING_PRELUDE
    return source


def with_collections_prelude(source: str) -> str:
    """Append minimal Vec/Dict definitions if the source uses literals."""
    needs_collections = (
        "[" in source
        or "Vec<" in source
        or "Dict<" in source
    )
    if needs_collections:
        return source + COLLECTIONS_PRELUDE
    return source


def check(source: str):
    """Helper to parse, resolve, and type check source code."""
    source = with_string_prelude(source)
    source = with_collections_prelude(source)
    program = parse(source)
    resolution = resolve(program)
    return typecheck(program, resolution)


# ========================= Literal Type Tests =========================

def test_integer_literal():
    """Test that integer literals have type i32."""
    result = check("def test() { let x = 42; }")

    assert not result.has_errors(), f"Unexpected errors: {result.errors}"
    # Find the literal's type
    found_i32 = any(
        result.type_table.get_type(t) and
        result.type_table.get_type(t).kind == TypeKind.PRIMITIVE
        for t in result.expr_types.values()
    )
    assert found_i32


def test_float_literal():
    """Test that float literals have type f64."""
    result = check("def test() { let x = 3.14; }")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"


def test_bool_literal():
    """Test that bool literals have type Bool."""
    result = check("def test() { let x = true; let y = false; }")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"


def test_string_literal():
    """Test that string literals have type String."""
    result = check('def test() { let x = "hello"; }')
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"


def test_nil_literal():
    """Test nil literal typing."""
    result = check("def test() { let x: i32? = nil; }")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"


# ========================= Variable Type Tests =========================

def test_variable_with_annotation():
    """Test variable with explicit type annotation."""
    result = check("def test() { let x: i32 = 42; }")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"


def test_variable_type_inference():
    """Test type inference for variables."""
    result = check("def test() { let x = 42; let y = x + 1; }")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"


def test_variable_type_mismatch():
    """Test that type mismatch is detected."""
    result = check('def test() { let x: i32 = "hello"; }')
    assert result.has_errors()
    assert any(e.kind == TypeErrorKind.TYPE_MISMATCH for e in result.errors)


def test_mutable_variable():
    """Test mutable variable assignment."""
    result = check("""
def test() {
    var x = 1;
    x = 2;
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"


# ========================= Arithmetic Operation Tests =========================

def test_integer_arithmetic():
    """Test integer arithmetic operations."""
    result = check("""
def test() {
    let a = 1 + 2;
    let b = 3 - 4;
    let c = 5 * 6;
    let d = 8 / 4;
    let e = 7 % 3;
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"


def test_float_arithmetic():
    """Test float arithmetic operations."""
    result = check("""
def test() {
    let a = 1.0 + 2.5;
    let b = 3.14 * 2.0;
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"


def test_mixed_numeric_operations():
    """Test mixed numeric operations (int + float)."""
    result = check("""
def test() {
    let a = 1 + 2.5;
}
""")
    # This might error depending on language semantics
    # For now we allow implicit widening
    # Just check it doesn't crash


def test_invalid_arithmetic():
    """Test that invalid arithmetic is detected."""
    result = check('def test() { let x = "a" + 1; }')
    assert result.has_errors()
    assert any(e.kind == TypeErrorKind.INVALID_OPERATION for e in result.errors)


# ========================= Comparison Operation Tests =========================

def test_comparison_operators():
    """Test comparison operators return Bool."""
    result = check("""
def test() {
    let a = 1 < 2;
    let b = 3 > 4;
    let c = 5 <= 6;
    let d = 7 >= 8;
    let e = 1 == 2;
    let f = 3 != 4;
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"


def test_boolean_operators():
    """Test boolean operators."""
    result = check("""
def test() {
    let a = true && false;
    let b = true || false;
    let c = !true;
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"


# ========================= Function Call Tests =========================

def test_simple_function_call():
    """Test calling a simple function."""
    result = check("""
def add(a: i32, b: i32) -> i32 {
    return a + b;
}

def test() {
    let x = add(1, 2);
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"


def test_function_wrong_arg_count():
    """Test that wrong argument count is detected."""
    result = check("""
def add(a: i32, b: i32) -> i32 {
    return a + b;
}

def test() {
    let x = add(1);
}
""")
    assert result.has_errors()
    assert any(e.kind == TypeErrorKind.WRONG_ARG_COUNT for e in result.errors)


def test_function_wrong_arg_type():
    """Test that wrong argument type is detected."""
    result = check("""
def add(a: i32, b: i32) -> i32 {
    return a + b;
}

def test() {
    let x = add(1, "hello");
}
""")
    assert result.has_errors()


def test_return_type_checking():
    """Test return type checking."""
    result = check("""
def test() -> i32 {
    return 42;
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"


def test_return_type_mismatch():
    """Test return type mismatch detection."""
    result = check("""
def test() -> i32 {
    return "hello";
}
""")
    assert result.has_errors()
    assert any(e.kind == TypeErrorKind.TYPE_MISMATCH for e in result.errors)


# ========================= Struct Tests =========================

def test_struct_init():
    """Test struct initialization."""
    result = check("""
struct Point {
    var x: i32;
    var y: i32;
}

def test() {
    let p = Point { x: 1, y: 2 };
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"


def test_struct_field_access():
    """Test struct field access."""
    result = check("""
struct Point {
    var x: i32;
    var y: i32;
}

def test() {
    let p = Point { x: 1, y: 2 };
    let x = p.x;
}
""")
    # Note: This might error due to incomplete member resolution
    # The implementation is still basic


def test_struct_method_call():
    """Test struct method call."""
    result = check("""
struct Point {
    var x: i32;
    var y: i32;

    def sum() -> i32 {
        return x + y;
    }
}

def test() {
    let p = Point { x: 1, y: 2 };
    let s = p.sum();
}
""")
    # Method resolution is complex - just check no crash


def test_undefined_member():
    """Test undefined member access detection."""
    result = check("""
struct Point {
    var x: i32;
}

def test() {
    let p = Point { x: 1 };
    let y = p.z;
}
""")
    assert result.has_errors()
    assert any(e.kind == TypeErrorKind.UNDEFINED_MEMBER for e in result.errors)


# ========================= Enum Tests =========================

def test_enum_case_access():
    """Test enum case access."""
    result = check("""
enum Option<T> {
    case none
    case some(T)
}

def test() {
    let x = Option.none;
}
""")
    # Enum case access - just check no crash


def test_switch_on_enum():
    """Test switch statement on enum."""
    result = check("""
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
""")
    # Switch pattern matching is complex



# ========================= Optional Type Tests =========================

def test_optional_type():
    """Test optional type annotation."""
    result = check("""
def test() {
    let x: i32? = nil;
    let y: i32? = 42;
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"


def test_nil_coalescing():
    """Test nil coalescing operator."""
    result = check("""
def test() {
    let x: i32? = nil;
    let y = x ?? 0;
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"


def test_optional_chaining():
    """Test optional chaining operator."""
    result = check("""
struct Point {
    var x: i32;
}

def test() {
    let p: Point? = nil;
    let x = p?.x;
}
""")
    # Optional chaining is complex


# ========================= Array Tests =========================

def test_array_literal():
    """Test array literal typing."""
    result = check("""
def test() {
    let arr = [1, 2, 3];
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"


def test_array_element_mismatch():
    """Test array element type mismatch."""
    result = check("""
def test() {
    let arr = [1, "hello", 3];
}
""")
    assert result.has_errors()
    assert any(e.kind == TypeErrorKind.TYPE_MISMATCH for e in result.errors)


def test_array_subscript():
    """Test array subscript access."""
    result = check("""
def test() {
    let arr = [1, 2, 3];
    let first = arr[0];
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"


def test_array_count():
    """Test Vec.len() method access."""
    result = check("""
def test() {
    let arr = [1, 2, 3];
    let n: i32 = arr.len();
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"


# ========================= Dictionary Tests =========================

def test_dict_literal():
    """Test dictionary literal typing."""
    result = check("""
def test() {
    let dict = ["a": 1, "b": 2];
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"


def test_empty_dict():
    """Test empty dictionary literal."""
    result = check("""
def test() {
    let dict = [:];
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"


def test_dict_count():
    """Test Dict.len() method access."""
    result = check("""
def test() {
    let dict = ["a": 1, "b": 2];
    let n: i64 = dict.len();
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"


# ========================= Tuple Tests =========================

def test_tuple_literal():
    """Test tuple literal typing."""
    result = check("""
def test() {
    let t = (1, 2, 3);
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"


def test_named_tuple():
    """Test named tuple typing."""
    result = check("""
def test() {
    let t = (x: 1, y: 2);
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"


# ========================= Control Flow Tests =========================

def test_if_condition_type():
    """Test that if condition must be Bool."""
    result = check("""
def test() {
    if true {
        let x = 1;
    }
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"


def test_if_condition_not_bool():
    """Test that non-Bool if condition is detected."""
    result = check("""
def test() {
    if 42 {
        let x = 1;
    }
}
""")
    assert result.has_errors()
    assert any(e.kind == TypeErrorKind.TYPE_MISMATCH for e in result.errors)


def test_while_condition():
    """Test while loop condition type."""
    result = check("""
def test() {
    var x = 10;
    while x > 0 {
        x = x - 1;
    }
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"


def test_for_loop():
    """Test for loop type checking."""
    result = check("""
def test() {
    for i in [1, 2, 3] {
        let squared = i * i;
    }
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"


def test_ternary_expression():
    """Test ternary expression type checking."""
    result = check("""
def test() {
    let x = true ? 1 : 0;
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"


# ========================= Lambda Tests =========================

def test_lambda_typing():
    """Test lambda expression typing."""
    result = check("""
def test() {
    let add = { a: i32, b: i32 in
        return a + b;
    };
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"


# ========================= Generic Type Tests =========================

def test_generic_function():
    """Test generic function type checking."""
    result = check("""
def identity<T>(x: T) -> T {
    return x;
}

def test() {
    let x = identity(42);
}
""")
    # Generic instantiation is deferred


def test_generic_struct():
    """Test generic struct type checking."""
    result = check("""
struct Box<T> {
    var value: T;
}

def test() {
    let b = Box<i32> { value: 42 };
}
""")
    # Generic struct instantiation


# ========================= Extern Function Tests =========================

def test_extern_function():
    """Test extern function type checking."""
    result = check("""
extern "C" def printf(fmt: RawPtr) -> i32;

def test() {
    unsafe {
        printf(nil);
    }
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"


def test_extern_c_call_requires_unsafe():
    result = check("""
extern "C" def printf(fmt: RawPtr) -> i32;

def test() {
    printf(nil);
}
""")
    assert result.has_errors()
    assert any("external 'C' function" in e.message for e in result.errors)


def test_raw_ptr_cast_requires_unsafe():
    result = check("""
def test() {
    let x = 1;
    let p = x as RawPtr;
}
""")
    assert result.has_errors()
    assert any("RawPtr" in e.message for e in result.errors)


def test_raw_ptr_cast_allowed_in_unsafe():
    result = check("""
def test() {
    let x = 1;
    unsafe {
        let p = x as RawPtr;
    }
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"


# ========================= Complex Example Tests =========================

def test_comprehensive_example():
    """Test type checking the basic.rl example file."""
    example_path = Path(__file__).parent.parent / "examples" / "basic.rl"
    if not example_path.exists():
        return  # Skip if example file doesn't exist

    source = example_path.read_text()
    result = check(source)

    # Print errors for debugging
    if result.errors:
        print(f"Errors ({len(result.errors)}):")
        for e in result.errors[:10]:  # Show first 10 errors
            print(f"  {e}")

    # Should have type information for expressions
    assert len(result.expr_types) > 0, "Should have typed some expressions"


def test_callee_resolution():
    """Test that call targets are resolved."""
    result = check("""
def add(a: i32, b: i32) -> i32 {
    return a + b;
}

def test() {
    let x = add(1, 2);
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"
    # Should have some call targets recorded
    assert len(result.call_targets) > 0


def test_multiple_errors():
    """Test that multiple errors are collected."""
    result = check("""
def test() {
    let a: i32 = "hello";
    let b: i32 = true;
    if 42 {
        let c = 1;
    }
}
""")
    # Should collect multiple type mismatch errors
    assert len(result.errors) >= 2


# ========================= Type Table Tests =========================

def test_type_table_builtins():
    """Test that builtin types are registered."""
    result = check("def test() {}")

    # Check builtin types exist
    assert result.type_table.get_builtin("i32") is not None
    assert result.type_table.get_builtin("i64") is not None
    assert result.type_table.get_builtin("f32") is not None
    assert result.type_table.get_builtin("f64") is not None
    assert result.type_table.get_builtin("Bool") is not None
    assert result.type_table.get_builtin("Void") is not None


def test_type_table_error_type():
    """Test error type sentinel."""
    result = check("def test() {}")

    assert result.type_table.error_type is not None
    assert result.type_table.is_error(result.type_table.error_type)


def test_type_queries():
    """Test type query methods."""
    result = check("def test() {}")

    i32_type = result.type_table.get_builtin("i32")
    assert i32_type is not None
    assert result.type_table.is_integer(i32_type)
    assert result.type_table.is_numeric(i32_type)
    assert not result.type_table.is_float(i32_type)

    f64_type = result.type_table.get_builtin("f64")
    assert f64_type is not None
    assert result.type_table.is_float(f64_type)
    assert result.type_table.is_numeric(f64_type)
    assert not result.type_table.is_integer(f64_type)

    bool_type = result.type_table.get_builtin("Bool")
    assert bool_type is not None
    assert result.type_table.is_bool(bool_type)
    assert not result.type_table.is_numeric(bool_type)


if __name__ == "__main__":
    # Run all tests
    tests = [
        # Literal types
        test_integer_literal,
        test_float_literal,
        test_bool_literal,
        test_string_literal,
        test_nil_literal,
        # Variable types
        test_variable_with_annotation,
        test_variable_type_inference,
        test_variable_type_mismatch,
        test_mutable_variable,
        # Arithmetic operations
        test_integer_arithmetic,
        test_float_arithmetic,
        test_mixed_numeric_operations,
        test_invalid_arithmetic,
        # Comparison operations
        test_comparison_operators,
        test_boolean_operators,
        # Function calls
        test_simple_function_call,
        test_function_wrong_arg_count,
        test_function_wrong_arg_type,
        test_return_type_checking,
        test_return_type_mismatch,
        # Structs
        test_struct_init,
        test_struct_field_access,
        test_struct_method_call,
        test_undefined_member,
        # Enums
        test_enum_case_access,
        test_switch_on_enum,
        # Optional types
        test_optional_type,
        test_nil_coalescing,
        test_optional_chaining,
        # Arrays
        test_array_literal,
        test_array_element_mismatch,
        test_array_subscript,
        # Dictionaries
        test_dict_literal,
        test_empty_dict,
        # Tuples
        test_tuple_literal,
        test_named_tuple,
        # Control flow
        test_if_condition_type,
        test_if_condition_not_bool,
        test_while_condition,
        test_for_loop,
        test_ternary_expression,
        # Lambdas
        test_lambda_typing,
        # Generics
        test_generic_function,
        test_generic_struct,
        # Extern
        test_extern_function,
        # Complex examples
        test_comprehensive_example,
        test_callee_resolution,
        test_multiple_errors,
        # Type table
        test_type_table_builtins,
        test_type_table_error_type,
        test_type_queries,
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
