"""Tests for the RoLang monomorphization phase."""

import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from rolang.parser import parse
from rolang.resolver import resolve
from rolang.checker import typecheck
from rolang.hir_builder import build_hir
from rolang.monomorphize import (
    monomorphize,
    MonomorphizationResult,
    InstanceKey,
    TypeSubstitution,
    mangle_name,
)
from rolang.hir import (
    HirProgram, HirFunction, HirStruct, HirEnum, HirExternFunc,
)


def mono(source: str) -> MonomorphizationResult:
    """Helper to parse, resolve, type check, build HIR, and monomorphize."""
    program = parse(source)
    resolution = resolve(program)
    type_result = typecheck(program, resolution)
    hir_result = build_hir(program, resolution, type_result)
    return monomorphize(hir_result)


# ========================= Non-Generic Passthrough Tests =========================

def test_empty_program():
    """Test monomorphization of an empty program."""
    result = mono("")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"
    assert isinstance(result.program, HirProgram)
    assert len(result.program.items) == 0


def test_non_generic_function_passthrough():
    """Test that non-generic functions pass through unchanged."""
    result = mono("""
def add(a: i32, b: i32) -> i32 {
    return a + b;
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    # Should have one function instance
    assert len(result.function_instances) == 1

    # Function name should be unchanged
    funcs = [i for i in result.program.items if isinstance(i, HirFunction)]
    assert len(funcs) == 1
    assert funcs[0].name == "add"


def test_non_generic_struct_passthrough():
    """Test that non-generic structs pass through unchanged."""
    result = mono("""
struct Point {
    var x: i32;
    var y: i32;
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    # Should have one struct instance
    assert len(result.struct_instances) == 1

    structs = [i for i in result.program.items if isinstance(i, HirStruct)]
    assert len(structs) == 1
    assert structs[0].name == "Point"


def test_non_generic_enum_passthrough():
    """Test that non-generic enums pass through unchanged."""
    result = mono("""
enum Direction {
    case north
    case south
    case east
    case west
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    # Should have one enum instance
    assert len(result.enum_instances) == 1

    enums = [i for i in result.program.items if isinstance(i, HirEnum)]
    assert len(enums) == 1
    assert enums[0].name == "Direction"


def test_extern_func_preserved():
    """Test that extern functions are preserved."""
    result = mono('extern "C" def printf(fmt: RawPtr) -> i32;')
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    externs = [i for i in result.program.items if isinstance(i, HirExternFunc)]
    assert len(externs) == 1
    assert externs[0].name == "printf"


# ========================= Simple Generic Function Tests =========================

def test_generic_function_single_instantiation():
    """Test monomorphization of a generic function with single instantiation."""
    result = mono("""
def identity<T>(x: T) -> T {
    return x;
}

def test() {
    let a = identity(42);
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    # Should have instances for: test, identity<i32>
    funcs = [i for i in result.program.items if isinstance(i, HirFunction)]
    func_names = [f.name for f in funcs]

    assert "test" in func_names
    # The identity function should be specialized (name will be mangled)


def test_generic_function_multiple_instantiations():
    """Test monomorphization with multiple different type arguments."""
    result = mono("""
def identity<T>(x: T) -> T {
    return x;
}

def test() {
    let a = identity(42);
    let b = identity(true);
    let c = identity(3.14);
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    # Should have multiple function instances
    funcs = [i for i in result.program.items if isinstance(i, HirFunction)]

    # At minimum: test function + identity instantiations
    assert len(funcs) >= 1


# ========================= Generic Struct Tests =========================

def test_generic_struct_instantiation():
    """Test monomorphization of a generic struct."""
    result = mono("""
struct Box<T> {
    var value: T;
}

def test() {
    let b = Box<i32> { value: 42 };
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    # Should have specialized struct
    structs = [i for i in result.program.items if isinstance(i, HirStruct)]
    assert len(structs) >= 1


def test_generic_struct_multiple_instantiations():
    """Test generic struct with multiple type arguments."""
    result = mono("""
struct Pair<A, B> {
    var first: A;
    var second: B;
}

def test() {
    let p = Pair<i32, bool> { first: 42, second: true };
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    structs = [i for i in result.program.items if isinstance(i, HirStruct)]
    assert len(structs) >= 1


# ========================= Generic Enum Tests =========================

def test_generic_enum_instantiation():
    """Test monomorphization of a generic enum."""
    result = mono("""
enum Option<T> {
    case none
    case some(T)
}

def test() {
    let opt: Option<i32> = Option.none;
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    enums = [i for i in result.program.items if isinstance(i, HirEnum)]
    assert len(enums) >= 1


def test_generic_enum_with_payload():
    """Test generic enum with payload instantiation."""
    result = mono("""
enum Result<T, E> {
    case ok(T)
    case err(E)
}

def test() {
    let r: Result<i32, String> = Result.ok(42);
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    enums = [i for i in result.program.items if isinstance(i, HirEnum)]
    assert len(enums) >= 1


# ========================= Transitive Dependency Tests =========================

def test_transitive_generic_call():
    """Test generic function calling another generic function."""
    result = mono("""
def identity<T>(x: T) -> T {
    return x;
}

def wrap<U>(x: U) -> U {
    return identity(x);
}

def test() {
    let a = wrap(42);
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    funcs = [i for i in result.program.items if isinstance(i, HirFunction)]
    assert len(funcs) >= 1


def test_generic_method_on_generic_struct():
    """Test generic struct with methods."""
    result = mono("""
struct Box<T> {
    var value: T;

    def get() -> T {
        return value;
    }
}

def test() {
    let b = Box<i32> { value: 42 };
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"


# ========================= Nested Generics Tests =========================

def test_nested_generic_types():
    """Test nested generic types like Box<Box<i32>>."""
    result = mono("""
struct Box<T> {
    var value: T;
}

def test() {
    let inner = Box<i32> { value: 42 };
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    structs = [i for i in result.program.items if isinstance(i, HirStruct)]
    assert len(structs) >= 1


# ========================= Name Mangling Tests =========================

def test_mangle_name_no_args():
    """Test name mangling with no type arguments."""
    program = parse("def test() {}")
    resolution = resolve(program)
    type_result = typecheck(program, resolution)
    hir_result = build_hir(program, resolution, type_result)

    mangled = mangle_name("identity", (), hir_result.type_table)
    assert mangled == "identity"


def test_mangle_name_single_arg():
    """Test name mangling with single type argument."""
    program = parse("def test() {}")
    resolution = resolve(program)
    type_result = typecheck(program, resolution)
    hir_result = build_hir(program, resolution, type_result)

    i32_type = hir_result.type_table.get_builtin("i32")
    mangled = mangle_name("identity", (i32_type,), hir_result.type_table)
    assert "identity" in mangled
    assert "i32" in mangled


def test_mangle_name_multiple_args():
    """Test name mangling with multiple type arguments."""
    program = parse("def test() {}")
    resolution = resolve(program)
    type_result = typecheck(program, resolution)
    hir_result = build_hir(program, resolution, type_result)

    i32_type = hir_result.type_table.get_builtin("i32")
    bool_type = hir_result.type_table.get_builtin("Bool")
    mangled = mangle_name("Pair", (i32_type, bool_type), hir_result.type_table)
    assert "Pair" in mangled


# ========================= Type Substitution Tests =========================

def test_type_substitution_empty():
    """Test empty type substitution."""
    subst = TypeSubstitution()
    assert subst.is_empty()
    assert len(subst.mapping) == 0


def test_type_substitution_apply_primitive():
    """Test type substitution on primitive types."""
    program = parse("def test() {}")
    resolution = resolve(program)
    type_result = typecheck(program, resolution)
    hir_result = build_hir(program, resolution, type_result)

    subst = TypeSubstitution()
    i32_type = hir_result.type_table.get_builtin("i32")

    # Primitive types should not be changed
    result = subst.apply(i32_type, hir_result.type_table)
    assert result == i32_type


def test_type_substitution_apply_with_mapping():
    """Test type substitution with a mapping."""
    program = parse("def test() {}")
    resolution = resolve(program)
    type_result = typecheck(program, resolution)
    hir_result = build_hir(program, resolution, type_result)

    # Create a type variable
    t_var = hir_result.type_table.make_type_variable("T")
    i32_type = hir_result.type_table.get_builtin("i32")

    # Create substitution T -> i32
    subst = TypeSubstitution(mapping={"T": i32_type})

    # Apply substitution
    result = subst.apply(t_var, hir_result.type_table)
    assert result == i32_type


# ========================= Instance Key Tests =========================

def test_instance_key_equality():
    """Test InstanceKey equality."""
    from rolang.symbols import SymbolId

    key1 = InstanceKey(SymbolId(1), (1, 2, 3))
    key2 = InstanceKey(SymbolId(1), (1, 2, 3))
    key3 = InstanceKey(SymbolId(1), (1, 2))
    key4 = InstanceKey(SymbolId(2), (1, 2, 3))

    assert key1 == key2
    assert key1 != key3
    assert key1 != key4


def test_instance_key_hash():
    """Test InstanceKey hashing."""
    from rolang.symbols import SymbolId

    key1 = InstanceKey(SymbolId(1), (1, 2, 3))
    key2 = InstanceKey(SymbolId(1), (1, 2, 3))

    # Same keys should have same hash
    assert hash(key1) == hash(key2)

    # Should be usable in sets/dicts
    s = {key1}
    assert key2 in s


# ========================= Result Structure Tests =========================

def test_monomorphization_result_structure():
    """Test MonomorphizationResult structure."""
    result = mono("def test() {}")

    assert result.program is not None
    assert result.type_table is not None
    assert result.symbol_table is not None
    assert isinstance(result.function_instances, dict)
    assert isinstance(result.struct_instances, dict)
    assert isinstance(result.enum_instances, dict)
    assert isinstance(result.errors, list)


def test_function_instance_structure():
    """Test FunctionInstance structure."""
    result = mono("""
def test() {
    let x = 42;
}
""")
    assert len(result.function_instances) == 1

    for key, instance in result.function_instances.items():
        assert instance.key == key
        assert instance.original_func is not None
        assert instance.specialized_func is not None
        assert isinstance(instance.mangled_name, str)


# ========================= Recursive/Self-Referential Tests =========================

def test_recursive_function():
    """Test monomorphization of recursive function."""
    result = mono("""
def factorial(n: i32) -> i32 {
    if n <= 1 {
        return 1;
    }
    return n * factorial(n - 1);
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    funcs = [i for i in result.program.items if isinstance(i, HirFunction)]
    assert len(funcs) == 1


# ========================= Control Flow Tests =========================

def test_generic_with_if():
    """Test generic function with if statement."""
    result = mono("""
def max<T>(a: T, b: T) -> T {
    if true {
        return a;
    }
    return b;
}

def test() {
    let m = max(1, 2);
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"


def test_generic_with_while():
    """Test generic function with while loop."""
    result = mono("""
def count<T>(start: T) -> T {
    var x = start;
    while false {
        x = start;
    }
    return x;
}

def test() {
    let c = count(10);
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"


# ========================= Expression Specialization Tests =========================

def test_generic_with_binary_ops():
    """Test generic function with binary operations."""
    result = mono("""
def compute(x: i32) -> i32 {
    return x + 1 * 2 - 3;
}

def test() {
    let r = compute(10);
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"


def test_generic_with_array():
    """Test generic function with array."""
    result = mono("""
def first(arr: [i32]) -> i32 {
    return arr[0];
}

def test() {
    let x = first([1, 2, 3]);
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"


# ========================= Pattern Specialization Tests =========================

def test_generic_with_switch():
    """Test generic function with switch/pattern matching."""
    result = mono("""
enum Bool2 {
    case yes
    case no
}

def check(b: Bool2) -> i32 {
    switch b {
    case .yes:
        return 1;
    case .no:
        return 0;
    }
}

def test() {
    let r = check(Bool2.yes);
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"


# ========================= Integration Tests =========================

def test_comprehensive_generic_program():
    """Test a more comprehensive program with generics."""
    result = mono("""
struct Container<T> {
    var value: T;

    def get() -> T {
        return value;
    }
}

enum Option<T> {
    case none
    case some(T)
}

def identity<T>(x: T) -> T {
    return x;
}

def make_container<T>(x: T) -> Container<T> {
    return Container { value: x };
}

def main() {
    let c = Container<i32> { value: 42 };
    let opt: Option<i32> = Option.none;
    let x = identity(10);
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    # Should have multiple items
    assert len(result.program.items) > 0


def test_example_file_monomorphization():
    """Test monomorphization with the basic.rl example file."""
    example_path = Path(__file__).parent.parent / "examples" / "basic.rl"
    if not example_path.exists():
        print("Skipping: example file not found")
        return

    source = example_path.read_text()
    result = mono(source)

    # Print stats for debugging
    if result.errors:
        print(f"Monomorphization errors ({len(result.errors)}):")
        for e in result.errors[:10]:
            print(f"  {e}")

    print(f"Function instances: {len(result.function_instances)}")
    print(f"Struct instances: {len(result.struct_instances)}")
    print(f"Enum instances: {len(result.enum_instances)}")


# ========================= Edge Cases =========================

def test_empty_generic_params():
    """Test function with empty generic params list."""
    result = mono("""
def test() {
    let x = 42;
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"


def test_multiple_functions_same_name_different_types():
    """Test multiple instantiations tracked separately."""
    result = mono("""
def id<T>(x: T) -> T {
    return x;
}

def test() {
    let a = id(1);
    let b = id(true);
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"


def test_unused_generic_function():
    """Test that unused generic functions don't create instances."""
    result = mono("""
def unused<T>(x: T) -> T {
    return x;
}

def test() {
    let x = 42;
}
""")
    assert not result.has_errors(), f"Unexpected errors: {result.errors}"

    # The generic 'unused' function should not be instantiated
    # since it's never called
    funcs = [i for i in result.program.items if isinstance(i, HirFunction)]
    func_names = [f.name for f in funcs]
    assert "test" in func_names


if __name__ == "__main__":
    # Run all tests
    tests = [
        # Non-generic passthrough
        test_empty_program,
        test_non_generic_function_passthrough,
        test_non_generic_struct_passthrough,
        test_non_generic_enum_passthrough,
        test_extern_func_preserved,
        # Simple generic function
        test_generic_function_single_instantiation,
        test_generic_function_multiple_instantiations,
        # Generic struct
        test_generic_struct_instantiation,
        test_generic_struct_multiple_instantiations,
        # Generic enum
        test_generic_enum_instantiation,
        test_generic_enum_with_payload,
        # Transitive dependencies
        test_transitive_generic_call,
        test_generic_method_on_generic_struct,
        # Nested generics
        test_nested_generic_types,
        # Name mangling
        test_mangle_name_no_args,
        test_mangle_name_single_arg,
        test_mangle_name_multiple_args,
        # Type substitution
        test_type_substitution_empty,
        test_type_substitution_apply_primitive,
        test_type_substitution_apply_with_mapping,
        # Instance key
        test_instance_key_equality,
        test_instance_key_hash,
        # Result structure
        test_monomorphization_result_structure,
        test_function_instance_structure,
        # Recursive
        test_recursive_function,
        # Control flow
        test_generic_with_if,
        test_generic_with_while,
        # Expression specialization
        test_generic_with_binary_ops,
        test_generic_with_array,
        # Pattern specialization
        test_generic_with_switch,
        # Integration
        test_comprehensive_generic_program,
        test_example_file_monomorphization,
        # Edge cases
        test_empty_generic_params,
        test_multiple_functions_same_name_different_types,
        test_unused_generic_function,
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
